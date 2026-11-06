"""Publish one queued post (or thread) to Bluesky and/or X.

Default is a dry run that prints exactly what would go out; pass --live to publish.
Credentials are read from ~/.config/tendollaragent/credentials.env and never printed.

Queue file format (content/queue/<id>.json):
    {"id": "001-intro", "sources": ["..."], "bluesky": ["post", "reply", ...], "x": ["post", ...]}
A list with several strings is published as a thread (each item replies to the previous one).
"""
import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = Path.home() / ".config/tendollaragent/credentials.env"
POSTED_LOG = REPO / "content/posted.jsonl"
STOP_FILE = REPO / "STOP"

DAILY_CAP = 3  # 每个平台每天最多几条原创（回帖链里的后续条不计）
LENGTH_LIMIT = {"bluesky": 300, "x": 280}
REQUIRED_KEYS = {
    "bluesky": ["BSKY_HANDLE", "BSKY_APP_PASSWORD"],
    "x": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
}
# X 按条计费，带链接的帖子贵十几倍，所以 X 上一律不放链接
LINK_PATTERN = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|ai|dev|app|xyz|so)\b", re.IGNORECASE)
# 一眼就是 AI 写的句式（见 ops/voice.md）。只拦模型自己写的草稿和回复，人挑过的定时帖不拦
AI_TELLS = re.compile(
    r"[—–;!]|\s-\s|[\U0001F300-\U0001FAFF☀-➿]"
    r"|\b(?:as an ai|great question|honestly|here'?s the thing|fun fact|let'?s be real|i appreciate|thanks? for"
    r"|i'?d be happy|feel free|hope this helps|journey|delve\w*|navigat\w*|landscape|leverag\w*|unlock\w*"
    r"|seamless\w*|robust|game[- ]?changer|testament|excited|thrilled|not just)\b",
    re.IGNORECASE)
BSKY_PDS = "https://bsky.social/xrpc"
X_TWEETS_URL = "https://api.x.com/2/tweets"


def load_credentials(platforms):
    creds = {}
    for line in CREDENTIALS_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            creds[key.strip()] = value.strip()
    missing = [k for p in platforms for k in REQUIRED_KEYS[p] if not creds.get(k)]
    if missing:
        sys.exit(f"凭证缺字段：{', '.join(missing)}（在 {CREDENTIALS_FILE} 里补上）")
    return creds


def find_problems(platform, texts, sources):
    problems = []
    for i, text in enumerate(texts):
        if not text.strip():
            problems.append(f"第 {i + 1} 条是空的")
        if len(text) > LENGTH_LIMIT[platform]:
            problems.append(f"第 {i + 1} 条 {len(text)} 字符，超过 {LENGTH_LIMIT[platform]}")
        if platform == "x" and LINK_PATTERN.search(text):
            problems.append(f"第 {i + 1} 条含链接，X 上带链接按高价计费")
        if re.search(r"\d", text) and not sources:
            problems.append(f"第 {i + 1} 条有数字但没写 sources（规矩第 2 条）")
    return problems


def find_image_problems(image):
    path = REPO / image["path"]
    if not path.exists():
        return [f"配图不存在：{path}"]
    problems = []
    if path.stat().st_size > 1_000_000:  # Bluesky 单图上限约 1MB
        problems.append(f"配图 {path.stat().st_size:,} 字节，超过 Bluesky 的 1MB 上限")
    if not image.get("alt", "").strip():
        problems.append("配图没有 alt 文本")
    return problems


def read_posted():
    if not POSTED_LOG.exists():
        return []
    return [json.loads(line) for line in POSTED_LOG.read_text().splitlines() if line.strip()]


def http_json(url, payload, headers):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def bluesky_facets(text):
    """Bluesky 不会自动识别链接和话题标签，必须按 UTF-8 字节区间标注。"""
    facets = []
    patterns = [
        (r"https?://[^\s)]+", lambda m: {"$type": "app.bsky.richtext.facet#link", "uri": m.group()}),
        (r"(?<!\w)#(\w+)", lambda m: {"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}),
    ]
    for pattern, make_feature in patterns:
        for m in re.finditer(pattern, text):
            start = len(text[: m.start()].encode())
            end = len(text[: m.end()].encode())
            facets.append({"index": {"byteStart": start, "byteEnd": end}, "features": [make_feature(m)]})
    return facets


class BlueskyClient:
    def __init__(self, creds):
        session = http_json(
            f"{BSKY_PDS}/com.atproto.server.createSession",
            {"identifier": creds["BSKY_HANDLE"], "password": creds["BSKY_APP_PASSWORD"]},
            {},
        )
        self.did = session["did"]
        self.auth = {"Authorization": f"Bearer {session['accessJwt']}"}

    def upload_image(self, path):
        request = urllib.request.Request(
            f"{BSKY_PDS}/com.atproto.repo.uploadBlob",
            data=Path(path).read_bytes(),
            headers={"Content-Type": "image/png", **self.auth},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())["blob"]

    def post(self, text, root=None, parent=None, image=None):
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "langs": ["en"],
        }
        facets = bluesky_facets(text)
        if facets:
            record["facets"] = facets
        if image:
            record["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": [{
                    "alt": image["alt"],
                    "image": self.upload_image(REPO / image["path"]),
                    "aspectRatio": {"width": image["width"], "height": image["height"]},
                }],
            }
        if parent:
            record["reply"] = {"root": root, "parent": parent}
        created = http_json(
            f"{BSKY_PDS}/com.atproto.repo.createRecord",
            {"repo": self.did, "collection": "app.bsky.feed.post", "record": record},
            self.auth,
        )
        return {"uri": created["uri"], "cid": created["cid"]}


def oauth_quote(s):
    return urllib.parse.quote(s, safe="~")  # OAuth 1.0a 要求的 RFC 3986 编码


def oauth1_header(method, url, creds, params=None):
    """params 是查询参数或表单参数，OAuth 1.0a 要求它们参与签名；JSON 请求体不参与。"""
    quote = oauth_quote
    oauth = {
        "oauth_consumer_key": creds["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    signed = {**oauth, **(params or {})}
    param_string = "&".join(f"{quote(k)}={quote(v)}" for k, v in sorted(signed.items()))
    base_string = "&".join([method, quote(url), quote(param_string)])
    signing_key = f"{quote(creds['X_API_SECRET'])}&{quote(creds['X_ACCESS_TOKEN_SECRET'])}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    oauth["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(f'{quote(k)}="{quote(v)}"' for k, v in sorted(oauth.items()))


class XClient:
    def __init__(self, creds):
        self.creds = creds

    def post(self, text, root=None, parent=None, image=None):
        assert image is None, "X 只发纯文字（带媒体另行计费），配图只给 Bluesky"
        payload = {"text": text}
        if parent:
            payload["reply"] = {"in_reply_to_tweet_id": parent["id"]}
        headers = {"Authorization": oauth1_header("POST", X_TWEETS_URL, self.creds)}
        return {"id": http_json(X_TWEETS_URL, payload, headers)["data"]["id"]}


CLIENTS = {"bluesky": BlueskyClient, "x": XClient}


def publish_thread(platform, item, creds, posted):
    """逐条发，已发过的条目跳过但沿用它的引用，所以中途失败后重跑能接着发。"""
    done = {e["index"]: e["ref"] for e in posted
            if e["id"] == item["id"] and e["platform"] == platform and not e.get("deleted_at")}  # 删掉的要重发
    client = None
    root = parent = None
    for index, text in enumerate(item[platform]):
        if index in done:
            ref = done[index]
        else:
            client = client or CLIENTS[platform](creds)
            image = item.get("bluesky_image") if platform == "bluesky" and index == 0 else None
            ref = client.post(text, root=root, parent=parent, image=image)
            entry = {
                "id": item["id"], "platform": platform, "index": index, "ref": ref,
                "posted_at": datetime.now().astimezone().isoformat(timespec="seconds"), "text": text,
            }
            with POSTED_LOG.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"[{platform}] 已发第 {index + 1} 条 → {ref}")
        root = root or ref
        parent = ref


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("queue_file", type=Path)
    parser.add_argument("--platform", choices=["bluesky", "x", "both"], default="both")
    parser.add_argument("--live", action="store_true", help="真的发布；不加则只预览")
    args = parser.parse_args()

    item = json.loads(args.queue_file.read_text())
    platforms = [p for p in ("bluesky", "x") if args.platform in (p, "both") and item.get(p)]
    sources = item.get("sources") or []

    has_problem = False
    for platform in platforms:
        problems = find_problems(platform, item[platform], sources)
        if platform == "bluesky" and item.get("bluesky_image"):
            problems += find_image_problems(item["bluesky_image"])
            print(f"\n[bluesky 配图] {item['bluesky_image']['path']}\nalt: {item['bluesky_image']['alt']}")
        print(f"\n=== {platform}（{len(item[platform])} 条）===")
        for i, text in enumerate(item[platform]):
            print(f"--- {i + 1} · {len(text)} 字符\n{text}")
        for p in problems:
            print(f"✗ {p}")
        has_problem = has_problem or bool(problems)
    if has_problem:
        sys.exit("有问题，未发布")
    if not args.live:
        print("\n预览结束（加 --live 才会真的发布）")
        return
    if STOP_FILE.exists():
        sys.exit("STOP 文件存在，停发")

    posted = read_posted()
    today = datetime.now().astimezone().date().isoformat()
    creds = load_credentials(platforms)
    for platform in platforms:
        roots_today = {e["id"] for e in posted if e["platform"] == platform and e["index"] == 0
                       and e["posted_at"].startswith(today) and e["id"] != item["id"]}
        if len(roots_today) >= DAILY_CAP:
            print(f"[{platform}] 今天已发 {len(roots_today)} 条原创，到上限，跳过")
            continue
        try:
            publish_thread(platform, item, creds, posted)
        except urllib.error.HTTPError as e:  # 平台拒绝时把原因打出来，响应体里不含凭证
            sys.exit(f"[{platform}] 发布失败 HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")


if __name__ == "__main__":
    main()
