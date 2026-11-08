"""A small X sample to check the Bluesky findings: AI posts and AI replies that X's search ranks as most relevant
(last 7 days), with each author's follower count, and for replies the follower count of the account replied to.

Budget: at most $1 of X API credits. At about $0.005 per post read and $0.010 per user read, 30 posts + 20 replies
+ at most 70 users comes to at most $0.95. The prices are third-party figures, not verified.
Saves to x.full.json, which stays on this machine: X's developer terms don't allow redistributing post content.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from metrics import x_get  # noqa: E402 — 先把脚本目录放进 path
from post import load_credentials  # noqa: E402

TOPIC = '(AI OR LLM OR chatbot OR "AI agent" OR "artificial intelligence")'
QUERIES = {
    "posts": (f"{TOPIC} -is:retweet -is:reply lang:en", 30),
    "replies": (f"{TOPIC} is:reply -is:retweet lang:en", 20),
}
POST_PRICE, USER_PRICE = 0.005, 0.010


def main():
    creds = load_credentials(["x"])
    posts, users, post_reads, user_reads = [], {}, 0, 0
    for name, (query, count) in QUERIES.items():
        params = {"query": query, "max_results": str(count), "sort_order": "relevancy",
                  "tweet.fields": "created_at,public_metrics,author_id,attachments,referenced_tweets,in_reply_to_user_id,entities",
                  "expansions": "author_id,in_reply_to_user_id", "user.fields": "public_metrics,created_at"}
        result = x_get("tweets/search/recent", params, creds)
        found = result.get("data", [])
        new_users = result.get("includes", {}).get("users", [])
        post_reads += len(found)
        user_reads += len(new_users)
        for user in new_users:
            users[user["id"]] = {"username": user["username"], "created_at": user.get("created_at"),
                                 "followers": user["public_metrics"]["followers_count"]}
        posts += [{**post, "query": name} for post in found]
        print(f"{name}: {len(found)} 条帖子，{len(new_users)} 个用户", flush=True)
    cost = post_reads * POST_PRICE + user_reads * USER_PRICE
    out = {"collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "queries": {k: v[0] for k, v in QUERIES.items()}, "post_reads": post_reads, "user_reads": user_reads,
           "estimated_cost": round(cost, 3), "users": users, "posts": posts}
    (HERE / "x.full.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"读了 {post_reads} 条帖子、{user_reads} 个用户，估计花费 ${cost:.3f} → x.full.json")


if __name__ == "__main__":
    main()
