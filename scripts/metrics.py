"""Collect today's numbers into content/metrics/<date>.json. Every number in a Day N post comes from this file.

Bluesky numbers come from the free public API. X charges a little API credit per read, so this makes
two X reads: the account (followers) and its posts from the last 7 days (likes, reposts, replies).
A platform that fails is recorded as {"error": ...}; its numbers are then simply missing.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post import REPO, load_credentials, oauth1_header, oauth_quote, read_posted  # noqa: E402 — 先把脚本目录放进 path

DAY_ZERO = date(2026, 11, 5)
METRICS_DIR = REPO / "content/metrics"
BSKY_ACTOR = "tendollaragent.bsky.social"
PUBLIC_APPVIEW = "https://public.api.bsky.app/xrpc"
X_API = "https://api.x.com/2"
RECENT_DAYS = 7
# 账本表的一行：| 日期 | 项目 | 金额 | 余额 |
LEDGER_ROW = re.compile(r"^\| (\d{4}-\d{2}-\d{2}) \| (.+?) \| ([+−-]\$[\d.,]+) \| \$([\d.,]+) \|$", re.MULTILINE)


def http_get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def ledger_balance():
    rows = LEDGER_ROW.findall((REPO / "LOG.md").read_text())
    return float(rows[-1][3].replace(",", "")) if rows else None


def bluesky_numbers(since):
    profile = http_get(f"{PUBLIC_APPVIEW}/app.bsky.actor.getProfile?{urllib.parse.urlencode({'actor': BSKY_ACTOR})}")
    query = urllib.parse.urlencode({"actor": BSKY_ACTOR, "limit": 30, "filter": "posts_no_replies"})
    feed = http_get(f"{PUBLIC_APPVIEW}/app.bsky.feed.getAuthorFeed?{query}")
    posts = []
    for item in feed.get("feed", []):
        post = item["post"]
        if item.get("reason") or post["record"]["createdAt"] < since.isoformat():
            continue  # 跳过转发和一周以前的
        posts.append({
            "uri": post["uri"], "created_at": post["record"]["createdAt"], "text": post["record"]["text"][:140],
            "likes": post.get("likeCount", 0), "reposts": post.get("repostCount", 0),
            "replies": post.get("replyCount", 0), "quotes": post.get("quoteCount", 0),
        })
    return {"followers": profile.get("followersCount", 0), "following": profile.get("followsCount", 0),
            "posts_total": profile.get("postsCount", 0), "recent_posts": posts}


def x_get(path, params, creds):
    url = f"{X_API}/{path}"
    query = "&".join(f"{oauth_quote(k)}={oauth_quote(v)}" for k, v in params.items())
    request = urllib.request.Request(f"{url}?{query}", headers={"Authorization": oauth1_header("GET", url, creds, params)})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def x_numbers(since, creds):
    me = x_get("users/me", {"user.fields": "public_metrics"}, creds)["data"]
    params = {"max_results": "20", "exclude": "replies", "tweet.fields": "public_metrics,created_at",
              "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ")}
    timeline = x_get(f"users/{me['id']}/tweets", params, creds)
    posts = [{
        "id": t["id"], "created_at": t["created_at"], "text": t["text"][:140],
        "likes": t["public_metrics"]["like_count"], "reposts": t["public_metrics"]["retweet_count"],
        "replies": t["public_metrics"]["reply_count"], "quotes": t["public_metrics"]["quote_count"],
        "impressions": t["public_metrics"].get("impression_count"),
    } for t in timeline.get("data", [])]
    metrics = me["public_metrics"]
    return {"followers": metrics["followers_count"], "following": metrics["following_count"],
            "posts_total": metrics["tweet_count"], "recent_posts": posts}


def own_thread_replies(platform):
    """我自己串里的回复也算在平台的回复数里。返回 {帖子引用: 我自己回它的条数}，好从回复数里扣掉。"""
    threads = {}
    for e in read_posted():
        if e["platform"] == platform and not e.get("deleted_at"):
            threads.setdefault(e["id"], {})[e["index"]] = e["ref"]
    own = {}
    for refs in threads.values():
        for index, ref in refs.items():
            if index + 1 in refs:  # 下一条是我回它的
                key = ref.get("uri") or ref.get("id")
                own[key] = own.get(key, 0) + 1
    return own


def subtract_own_replies(numbers, platform):
    own = own_thread_replies(platform)
    for post in numbers["recent_posts"]:
        mine = own.get(post.get("uri") or post.get("id"), 0)
        post["replies_from_others"] = max(0, post.pop("replies") - mine)
    return numbers


def todays_activity(day):
    posted = [e for e in read_posted() if e["posted_at"].startswith(day.isoformat()) and not e.get("deleted_at")]
    log = subprocess.run(["git", "-C", str(REPO), "log", f"--since={day.isoformat()} 00:00", "--format=%s"],
                         capture_output=True, text=True, check=False)
    return {"posts": [{"id": e["id"], "platform": e["platform"], "index": e["index"], "text": e["text"]} for e in posted],
            "commits": log.stdout.splitlines()}


def collect(day):
    since = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    metrics = {
        "date": day.isoformat(), "day": (day - DAY_ZERO).days,
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "balance_usd": ledger_balance(), "balance_source": "LOG.md ledger",
    }
    try:
        metrics["bluesky"] = subtract_own_replies(bluesky_numbers(since), "bluesky")
    except (urllib.error.URLError, KeyError, ValueError) as e:
        metrics["bluesky"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    try:
        metrics["x"] = subtract_own_replies(x_numbers(since, load_credentials(["x"])), "x")
    except urllib.error.HTTPError as e:  # 响应体里不含凭证
        metrics["x"] = {"error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}
    except (urllib.error.URLError, KeyError, ValueError) as e:
        metrics["x"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    metrics["today"] = todays_activity(day)
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now().astimezone().date())
    parser.add_argument("--out", type=Path, help="默认写到 content/metrics/<date>.json")
    args = parser.parse_args()

    metrics = collect(args.date)
    out = args.out or METRICS_DIR / f"{args.date.isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    for platform in ("bluesky", "x"):
        numbers = metrics[platform]
        summary = numbers.get("error") or f"粉丝 {numbers['followers']} · 近 {RECENT_DAYS} 天原创 {len(numbers['recent_posts'])} 条"
        print(f"{platform}: {summary}")
    print(f"Day {metrics['day']} · 余额 ${metrics['balance_usd']:.2f} · 今天发了 {len(metrics['today']['posts'])} 条 → {out}")


if __name__ == "__main__":
    main()
