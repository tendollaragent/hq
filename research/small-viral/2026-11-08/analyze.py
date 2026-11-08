"""Turn bluesky.full.json into the published evidence: hits_public.csv (one row per small-account hit, no post text)
and results.json (the numbers quoted in README.md and LOG.md).

The two hand labels are my judgment, made by reading each post: ON_TOPIC drops posts that matched a search word
but aren't about AI or tech (an art challenge called "Tech Control", a game), and CRITICAL marks posts that attack
AI, AI companies, big tech or data centers. Ranks are by likes, highest first.
"""
import csv
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
OFF_TOPIC = {6, 8, 10, 13, 17, 29, 37}
CRITICAL = {2, 3, 4, 5, 11, 12, 14, 15, 16, 18, 19, 22, 23, 24, 27, 30, 31, 32, 35}


def features(post):
    embed = post["embed"] or ""
    return {"short": len(post["text"]) <= 140, "link_card": embed == "external", "hashtag": "tag" in post["facets"],
            "image": "images" in embed, "quote": embed.startswith("record"), "reply": bool(post["reply_parent"])}


def shares(posts):
    keys = features(posts[0]).keys()
    return {k: round(100 * sum(features(p)[k] for p in posts) / len(posts)) for k in keys}


def main():
    data = json.loads((HERE / "bluesky.full.json").read_text())
    authors, big = data["authors"], data["big"]
    followers = lambda post: authors.get(post["author"], {}).get("followers")  # noqa: E731
    hits = sorted(data["hits"], key=lambda p: -p["likes"])
    rows = []
    for rank, post in enumerate(hits, start=1):
        reply_big = (post["parent_author_followers"] or 0) >= big
        rows.append({
            "rank": rank, "uri": post["uri"], "likes": post["likes"], "reposts": post["reposts"],
            "author_followers": followers(post), "chars": len(post["text"]), "embed": post["embed"] or "",
            "hashtag": int("tag" in post["facets"]), "reply_to_followers": post["parent_author_followers"],
            "quoted_followers": post["quoted_author_followers"], "top_reposter_followers": post["top_reposter_followers"],
            "reposters_10k_plus": post["big_reposters"], "on_topic": int(rank not in OFF_TOPIC),
            "critical_of_ai": int(rank in CRITICAL), "seen_via_big_account": int(reply_big or post["big_reposters"] > 0),
        })
    with (HERE / "hits_public.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    on_topic = [r for r in rows if r["on_topic"]]
    small_baseline = [p for p in data["latest"] if followers(p) is not None and followers(p) < data["small"]]
    big_top = [p for p in data["top"] if (followers(p) or 0) >= big and p["likes"] >= data["hit_likes"]]
    results = {
        "top_posts": len(data["top"]), "latest_posts": len(data["latest"]), "hits": len(hits),
        "hits_on_topic": len(on_topic),
        "on_topic_seen_via_big_account": sum(r["seen_via_big_account"] for r in on_topic),
        "on_topic_replies_under_big_account": sum((r["reply_to_followers"] or 0) >= big for r in on_topic),
        "on_topic_reposted_by_big_account": sum(r["reposters_10k_plus"] > 0 for r in on_topic),
        "on_topic_critical_of_ai": sum(r["critical_of_ai"] for r in on_topic),
        "hit_median_likes": median(r["likes"] for r in rows), "hit_median_followers": median(r["author_followers"] for r in rows),
        "small_baseline_posts": len(small_baseline), "small_baseline_median_likes": median(p["likes"] for p in small_baseline),
        "feature_share_pct": {"small_hits": shares(hits), "small_baseline": shares(small_baseline),
                              "big_account_hits": shares(big_top)},
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
