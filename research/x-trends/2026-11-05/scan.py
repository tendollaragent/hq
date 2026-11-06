"""Pull the posts X's search ranks as most relevant for four queries about AI agents and money (last 7 days).

Saves the raw posts to posts.full.json. That file stays on this machine: X's developer terms don't allow
redistributing post content, so only post IDs and derived numbers are published (see analyze.py).
Cost: X bills per post returned; 4 queries x 25 posts is about 100 reads.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from metrics import x_get  # noqa: E402 — 先把脚本目录放进 path
from post import load_credentials  # noqa: E402

PER_QUERY = 25
BASE_FILTER = "-is:retweet -is:reply lang:en"
# 不用 $ 符号：X 的搜索语法里 $ 是股票代码运算符
QUERIES = {
    "agent-money": '("AI agent" OR "AI agents") (money OR revenue OR profit OR income)',
    "gave-ai": '("gave an AI" OR "gave AI" OR "gave my AI" OR "gave an agent" OR "gave my agent")',
    "build-in-public": '(buildinpublic OR "build in public" OR "building in public") (AI OR agent)',
    "first-revenue": '(AI OR agent) (MRR OR "first customer" OR "first dollar" OR "first sale")',
}
FIELDS = "created_at,public_metrics,author_id,lang,entities,attachments,note_tweet"


def main():
    creds = load_credentials(["x"])
    posts, reads = {}, 0
    for name, query in QUERIES.items():
        params = {"query": f"{query} {BASE_FILTER}", "max_results": str(PER_QUERY),
                  "sort_order": "relevancy", "tweet.fields": FIELDS}
        result = x_get("tweets/search/recent", params, creds)
        found = result.get("data", [])
        reads += len(found)
        for post in found:
            posts.setdefault(post["id"], {**post, "queries": []})["queries"].append(name)
        print(f"{name}: {len(found)} 条")
    out = {"collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "queries": {k: f"{v} {BASE_FILTER}" for k, v in QUERIES.items()},
           "reads": reads, "posts": list(posts.values())}
    (HERE / "posts.full.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"共读取 {reads} 条，去重后 {len(posts)} 条 → posts.full.json")


if __name__ == "__main__":
    main()
