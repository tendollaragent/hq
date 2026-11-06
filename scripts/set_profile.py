"""Set the profile on Bluesky and X from content/profile.json: name, bio, avatar, banner.

Bluesky also gets the bot self-label and the pinned post; on X the pin is set by hand.
Default is a dry run that checks lengths and images and touches no network; pass --live to write.
Bluesky fields this script doesn't manage are kept as they are.
"""
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post import (  # noqa: E402 — 先把脚本目录放进 path
    BSKY_PDS, REPO, BlueskyClient, http_json, load_credentials, oauth1_header, oauth_quote, read_posted,
)

PROFILE_FILE = REPO / "content/profile.json"
PUBLIC_APPVIEW = "https://public.api.bsky.app/xrpc"
SELF_LABELS = "com.atproto.label.defs#selfLabels"
BOT_LABEL = "bot"  # AT Protocol 机器人教程要求自动账号在主页带这个自我标签
X_ACCOUNT_API = "https://api.x.com/1.1/account"
X_ME_URL = "https://api.x.com/2/users/me"
# Bluesky 按字素计、X 按字符计；英文文案两者相同，这里统一用 len()
TEXT_LIMITS = {"bluesky": {"displayName": 64, "description": 256}, "x": {"name": 50, "bio": 160}}
# Bluesky 单个图片 blob 上限约 1MB；X 头像要求小于 700KB，横幅也按这个数保守卡
IMAGE_LIMITS = {"bluesky": 1_000_000, "x": 700_000}


def find_problems(profile, platforms):
    problems = []
    for platform in platforms:
        for field, limit in TEXT_LIMITS[platform].items():
            text = profile[platform].get(field, "")
            if not text.strip():
                problems.append(f"{platform}.{field} 是空的")
            elif len(text) > limit:
                problems.append(f"{platform}.{field} {len(text)} 字符，超过 {limit}")
        for field in ("avatar", "banner"):
            path = REPO / profile[platform][field]
            if not path.exists():
                problems.append(f"{platform}.{field} 图片不存在：{path}")
            elif path.stat().st_size > IMAGE_LIMITS[platform]:
                problems.append(f"{platform}.{field} 图片 {path.stat().st_size:,} 字节，超过 {IMAGE_LIMITS[platform]:,}")
    return problems


def http_get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


# ---------- Bluesky ----------

def read_current_record(client):
    """读出现有主页记录；新账号可能还没有，返回 None。"""
    query = urllib.parse.urlencode({"repo": client.did, "collection": "app.bsky.actor.profile", "rkey": "self"})
    try:
        return http_get(f"{BSKY_PDS}/com.atproto.repo.getRecord?{query}", client.auth)
    except urllib.error.HTTPError as e:
        if e.code == 400 and "RecordNotFound" in e.read().decode(errors="replace"):
            return None
        raise


def pinned_ref(post_id):
    """从发帖记录里找这条帖子在 Bluesky 上的第一条（线程开头）；删掉的不算，重发过就取最新的。"""
    refs = [e["ref"] for e in read_posted()
            if e["id"] == post_id and e["platform"] == "bluesky" and e["index"] == 0 and not e.get("deleted_at")]
    return refs[-1] if refs else None


def build_record(current, profile, client):
    record = dict(current["value"]) if current else {"$type": "app.bsky.actor.profile"}
    record["displayName"] = profile["displayName"]
    record["description"] = profile["description"]
    record["avatar"] = client.upload_image(REPO / profile["avatar"])
    record["banner"] = client.upload_image(REPO / profile["banner"])

    labels = record.get("labels") or {"$type": SELF_LABELS, "values": []}
    if not any(v.get("val") == BOT_LABEL for v in labels.get("values", [])):
        labels["values"] = labels.get("values", []) + [{"val": BOT_LABEL}]
    record["labels"] = labels

    if profile.get("pinned"):
        ref = pinned_ref(profile["pinned"])
        if ref:
            record["pinnedPost"] = {"uri": ref["uri"], "cid": ref["cid"]}
        else:
            print(f"置顶帖 {profile['pinned']} 还没在 Bluesky 发出，这次先不设置顶")
    return record


def set_bluesky_profile(profile, creds):
    client = BlueskyClient(creds)
    current = read_current_record(client)
    record = build_record(current, profile, client)
    payload = {"repo": client.did, "collection": "app.bsky.actor.profile", "rkey": "self", "record": record}
    if current:
        payload["swapRecord"] = current["cid"]  # 防止覆盖掉别处刚做的修改
    http_json(f"{BSKY_PDS}/com.atproto.repo.putRecord", payload, client.auth)
    print("\n已写入 Bluesky 主页")
    time.sleep(5)  # 公开接口索引新记录要几秒

    # 按公开接口读一遍，确认别人看到的就是写进去的
    seen = http_get(f"{PUBLIC_APPVIEW}/app.bsky.actor.getProfile?{urllib.parse.urlencode({'actor': client.did})}")
    print("Bluesky 公开主页现在显示：")
    print(f"  名字      {seen.get('displayName')}")
    print(f"  简介      {seen.get('description')}")
    print(f"  头像      {'有' if seen.get('avatar') else '无'} · 横幅 {'有' if seen.get('banner') else '无'}")
    print(f"  标签      {[label['val'] for label in seen.get('labels', [])] or '无'}")
    print(f"  置顶帖    {(seen.get('pinnedPost') or {}).get('uri', '无（公开接口可能还没刷新）')}")


# ---------- X ----------

def x_request(method, url, creds, params):
    """X 的 v1.1 账号接口收表单参数；表单参数和查询参数都要参与 OAuth 签名。"""
    encoded = "&".join(f"{oauth_quote(k)}={oauth_quote(v)}" for k, v in params.items())
    headers = {"Authorization": oauth1_header(method, url, creds, params)}
    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=encoded.encode(), headers=headers, method="POST")
    else:
        request = urllib.request.Request(f"{url}?{encoded}", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw.strip() else {}


def image_base64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def set_x_profile(profile, creds):
    # 先改文字：如果这个套餐不开放账号接口，第一步就会被拒，后面的图片不会再传
    x_request("POST", f"{X_ACCOUNT_API}/update_profile.json", creds,
              {"name": profile["name"], "description": profile["bio"], "url": profile["website"]})
    print("\nX：名字、简介、网站已更新")
    x_request("POST", f"{X_ACCOUNT_API}/update_profile_image.json", creds, {"image": image_base64(REPO / profile["avatar"])})
    print("X：头像已更新")
    x_request("POST", f"{X_ACCOUNT_API}/update_profile_banner.json", creds, {"banner": image_base64(REPO / profile["banner"])})
    print("X：横幅已更新")

    me = x_request("GET", X_ME_URL, creds, {"user.fields": "description,profile_image_url,url"})["data"]
    print("X 主页现在显示：")
    print(f"  名字      {me.get('name')}")
    print(f"  简介      {me.get('description')}")
    print(f"  头像      {me.get('profile_image_url')}")
    print(f"  网站      {me.get('url') or '无'}")


SETTERS = {"bluesky": set_bluesky_profile, "x": set_x_profile}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["bluesky", "x", "both"], default="both")
    parser.add_argument("--live", action="store_true", help="真的写入；不加则只检查")
    args = parser.parse_args()
    platforms = [p for p in ("bluesky", "x") if args.platform in (p, "both")]

    profile = json.loads(PROFILE_FILE.read_text())
    for platform in platforms:
        print(f"\n=== {platform} ===")
        for field, limit in TEXT_LIMITS[platform].items():
            text = profile[platform].get(field, "")
            print(f"{field}（{len(text)}/{limit}）: {text}")
        print(f"头像 {profile[platform]['avatar']} · 横幅 {profile[platform]['banner']}")

    problems = find_problems(profile, platforms)
    for p in problems:
        print(f"✗ {p}")
    if problems:
        sys.exit("有问题，未写入")
    if not args.live:
        print("\n检查通过（加 --live 才会写入）")
        return

    creds = load_credentials(platforms)
    for platform in platforms:
        try:
            SETTERS[platform](profile[platform], creds)
        except urllib.error.HTTPError as e:  # 平台拒绝时把原因打出来，响应体里不含凭证
            sys.exit(f"[{platform}] 写入失败 HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")


if __name__ == "__main__":
    main()
