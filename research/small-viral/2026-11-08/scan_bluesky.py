"""Find Bluesky posts about AI and tech from small accounts that still got a lot of likes, and the context around them.

1. Search twelve AI and tech queries, sorted by "top" (last 30 days) and by "latest" (the baseline).
2. Look up every author's follower count.
3. For each small-account hit (under 1,000 followers, 100+ likes): who reposted it, whose post it quoted,
   whose post it replied to, and how many followers each of those accounts has.

Bluesky's API is free. Saves everything to bluesky.full.json, which stays on this machine.
"""
import json
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from metrics import http_get  # noqa: E402 — 先把脚本目录放进 path
from post import BSKY_PDS, BlueskyClient, load_credentials  # noqa: E402

APPVIEW = "https://public.api.bsky.app/xrpc"
QUERIES = ["AI", "artificial intelligence", "LLM", "AI agent", "chatbot", "machine learning",
           "tech", "software", "programming", "coding", "startup", "data center"]
TOP_PAGES = 3          # 每个词按热度翻 3 页，每页 100 条
DAYS = 30
SMALL = 1000           # 关注者少于这个数算小号
HIT_LIKES = 100        # 小号帖子拿到这么多赞算爆
BIG = 10_000           # 转发者 / 被引用者 / 被回复者算大号的门槛


def get(url, headers=None):
    for attempt in range(3):
        try:
            return http_get(url, headers)
        except Exception as e:  # noqa: BLE001 — 网络抖动重试两次，还不行就让它抛
            if attempt == 2:
                raise
            print(f"  重试：{type(e).__name__}: {e}", flush=True)
            time.sleep(3)


def search(auth, query, sort, since, pages):
    posts, cursor = [], None
    for _ in range(pages):
        params = {"q": query, "sort": sort, "since": since, "limit": 100, "lang": "en"}
        if cursor:
            params["cursor"] = cursor
        page = get(f"{BSKY_PDS}/app.bsky.feed.searchPosts?{urllib.parse.urlencode(params)}", auth)
        posts += page.get("posts", [])
        cursor = page.get("cursor")
        if not cursor:
            break
    return posts


def profiles(dids):
    """did -> 关注者数等；每次最多 25 个。"""
    found, dids = {}, sorted(set(dids))
    for i in range(0, len(dids), 25):
        query = "&".join("actors=" + urllib.parse.quote(d) for d in dids[i:i + 25])
        for p in get(f"{APPVIEW}/app.bsky.actor.getProfiles?{query}").get("profiles", []):
            found[p["did"]] = {"handle": p["handle"], "followers": p.get("followersCount", 0),
                               "posts": p.get("postsCount", 0), "created_at": p.get("createdAt"),
                               "labels": [label["val"] for label in p.get("labels", [])]}
    return found


def slim(post):
    record, embed = post["record"], post.get("embed") or {}
    facets = [f["$type"].split("#")[-1] for facet in record.get("facets", []) for f in facet.get("features", [])]
    quoted = None
    if embed.get("$type", "").startswith("app.bsky.embed.record"):
        inner = embed.get("record", {})
        inner = inner.get("record", inner)  # recordWithMedia 多包一层
        quoted = {"uri": inner.get("uri"), "author": (inner.get("author") or {}).get("did")}
    return {
        "uri": post["uri"], "author": post["author"]["did"], "handle": post["author"]["handle"],
        "text": record.get("text", ""), "created_at": record.get("createdAt"),
        "likes": post.get("likeCount", 0), "reposts": post.get("repostCount", 0),
        "quotes": post.get("quoteCount", 0), "replies": post.get("replyCount", 0),
        "embed": embed.get("$type", "").replace("app.bsky.embed.", "").replace("#view", "") or None,
        "facets": facets, "reply_parent": (record.get("reply") or {}).get("parent", {}).get("uri"),
        "quoted": quoted,
    }


def reposters(uri):
    query = urllib.parse.urlencode({"uri": uri, "limit": 100})
    return [r["did"] for r in get(f"{APPVIEW}/app.bsky.feed.getRepostedBy?{query}").get("repostedBy", [])]


def post_authors(uris):
    found = {}
    for i in range(0, len(uris), 25):
        query = "&".join("uris=" + urllib.parse.quote(u, safe="") for u in uris[i:i + 25])
        for p in get(f"{APPVIEW}/app.bsky.feed.getPosts?{query}").get("posts", []):
            found[p["uri"]] = p["author"]["did"]
    return found


def main():
    client = BlueskyClient(load_credentials(["bluesky"]))
    since = (datetime.now().astimezone() - timedelta(days=DAYS)).isoformat(timespec="seconds")
    top, latest = {}, {}
    for query in QUERIES:
        for post in search(client.auth, query, "top", since, TOP_PAGES):
            top.setdefault(post["uri"], {**slim(post), "queries": []})["queries"].append(query)
        for post in search(client.auth, query, "latest", since, 1):
            latest.setdefault(post["uri"], {**slim(post), "queries": []})["queries"].append(query)
        print(f"{query}: top 累计 {len(top)}，latest 累计 {len(latest)}", flush=True)

    authors = profiles([p["author"] for p in [*top.values(), *latest.values()]])
    hits = [p for p in top.values()
            if authors.get(p["author"], {}).get("followers", SMALL) < SMALL and p["likes"] >= HIT_LIKES]
    print(f"小号爆款：{len(hits)} 条", flush=True)

    # 爆款的来龙去脉：谁转了它、它引用了谁、它回复了谁
    parent_author = post_authors([p["reply_parent"] for p in hits if p["reply_parent"]])
    for p in hits:
        p["reposters"] = reposters(p["uri"])
        p["parent_author"] = parent_author.get(p["reply_parent"])
    context = profiles([d for p in hits for d in p["reposters"]]
                       + [p["parent_author"] for p in hits if p["parent_author"]]
                       + [p["quoted"]["author"] for p in hits if p["quoted"] and p["quoted"]["author"]])
    for p in hits:
        counts = sorted((context.get(d, {}).get("followers", 0) for d in p["reposters"]), reverse=True)
        p["top_reposter_followers"] = counts[0] if counts else 0
        p["big_reposters"] = sum(c >= BIG for c in counts)
        p["parent_author_followers"] = context.get(p["parent_author"], {}).get("followers")
        p["quoted_author_followers"] = (context.get(p["quoted"]["author"], {}).get("followers")
                                        if p["quoted"] else None)
        del p["reposters"]  # 只留汇总，不存转发者名单

    out = {"collected_at": datetime.now().astimezone().isoformat(timespec="seconds"), "since": since,
           "queries": QUERIES, "small": SMALL, "hit_likes": HIT_LIKES, "big": BIG,
           "authors": authors, "top": list(top.values()), "latest": list(latest.values()), "hits": hits}
    (HERE / "bluesky.full.json").write_text(json.dumps(out, ensure_ascii=False) + "\n")
    print(f"top {len(top)} 条，latest {len(latest)} 条，作者 {len(authors)} 个 → bluesky.full.json")


if __name__ == "__main__":
    main()
