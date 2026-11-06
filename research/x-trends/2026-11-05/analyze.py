"""Tag every post in posts.full.json with fixed rules, then write the public results.

Publishes only post IDs, counts and tags (posts_public.csv, results.json), never post text: X's developer
terms don't allow redistributing it. Every rule is a regex or a count below, so anyone can rerun it.
"""
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

MONEY = re.compile(r"\$\s?\d|\b\d[\d,.]*\s?(k|m|million|billion)?\s?(usd|usdc|dollars?)\b|\b(mrr|arr)\b", re.IGNORECASE)
# 能核实的证据：区块浏览器、第三方收入核验、公开代码。截图不算，因为没法核实。
PROOF_DOMAINS = ("solscan.io", "explorer.solana.com", "solana.fm", "etherscan.io", "basescan.org", "bscscan.com",
                 "arbiscan.io", "polygonscan.com", "trustmrr.com", "github.com", "gitlab.com")
BAIT = re.compile(
    r"x algo|x algorithm|let'?s connect|#connect|follow for follow|\bf4f\b|"
    r"comment\s+[\"“'‘]?[\w-]+[\"”'’]?\s+(below|and|to|for)|reply\s+[\"“'‘][\w-]+|"
    r"\bdm me\b|\bi'?ll dm\b|\bi will dm\b|drop (a|your) ",
    re.IGNORECASE)
MENTION = re.compile(r"@(\w{1,15})")
CAMPAIGN_MIN_AUTHORS = 3  # 同一个账号被 3 个以上不同作者 @，算一次集中推广
PLATFORM_HANDLES = {"x"}  # @X 是平台官方，求关注帖常 @ 它，那类已算进 engagement_bait
HIGH_REPLY_RATE = 0.05  # 回复数超过浏览量的 5%
MIN_VIEWS_FOR_RATE = 100


def full_text(post):
    return (post.get("note_tweet") or {}).get("text") or post["text"]


def external_links(post):
    urls = [u.get("expanded_url", "") for u in post.get("entities", {}).get("urls", [])]
    return [u for u in urls if u and not re.search(r"//(www\.)?(x|twitter)\.com/", u)]


def tag(post, campaign_handles):
    text = full_text(post)
    m = post["public_metrics"]
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    letters = [c for c in first_line if c.isalpha()]
    views = m.get("impression_count") or 0
    links = external_links(post)
    mentions = {h.lower() for h in MENTION.findall(text)}
    return {
        "post_id": post["id"],
        "created_at": post["created_at"],
        "queries": "|".join(post["queries"]),
        "likes": m["like_count"], "reposts": m["retweet_count"], "replies": m["reply_count"],
        "quotes": m["quote_count"], "views": views,
        "engagement": m["like_count"] + m["retweet_count"] + m["reply_count"] + m["quote_count"],
        "chars": len(text),
        "dollar_figure": bool(MONEY.search(text)),
        "proof_link": any(d in link for link in links for d in PROOF_DOMAINS),
        "any_external_link": bool(links),
        "has_media": bool(post.get("attachments", {}).get("media_keys")),
        "engagement_bait": bool(BAIT.search(text)),
        "campaign_mention": bool(mentions & campaign_handles),
        "number_first": bool(re.match(r"\s*[$\d]", text)),
        "caps_hook": len(letters) >= 15 and sum(c.isupper() for c in letters) / len(letters) >= 0.7,
        "high_reply_rate": views >= MIN_VIEWS_FOR_RATE and m["reply_count"] / views > HIGH_REPLY_RATE,
    }


def campaign_handles(posts):
    authors_by_handle = defaultdict(set)
    for post in posts:
        for handle in {h.lower() for h in MENTION.findall(full_text(post))}:
            authors_by_handle[handle].add(post["author_id"])
    return {h for h, authors in authors_by_handle.items()
            if len(authors) >= CAMPAIGN_MIN_AUTHORS and h not in PLATFORM_HANDLES}


def share(rows, key):
    return sum(r[key] for r in rows)


def main():
    data = json.loads((HERE / "posts.full.json").read_text())
    posts = data["posts"]
    handles = campaign_handles(posts)
    rows = sorted((tag(p, handles) for p in posts), key=lambda r: r["engagement"], reverse=True)
    top = rows[:20]
    money = [r for r in rows if r["dollar_figure"]]

    results = {
        "collected_at": data["collected_at"], "queries": data["queries"],
        "window": [min(r["created_at"] for r in rows), max(r["created_at"] for r in rows)],
        "posts": len(rows),
        "median_engagement": statistics.median(r["engagement"] for r in rows),
        "zero_engagement": sum(r["engagement"] == 0 for r in rows),
        "dollar_figure": len(money),
        "dollar_figure_with_proof_link": share(money, "proof_link"),
        "dollar_figure_with_any_link": share(money, "any_external_link"),
        "engagement_bait": share(rows, "engagement_bait"),
        "campaign_handles_found": len(handles),
        "campaign_mention": share(rows, "campaign_mention"),
        "high_reply_rate": share(rows, "high_reply_rate"),
        "median_reply_rate": statistics.median(r["replies"] / r["views"] for r in rows if r["views"] >= MIN_VIEWS_FOR_RATE),
        "posts_with_rate": sum(r["views"] >= MIN_VIEWS_FOR_RATE for r in rows),
        "top20_clean": sum(not (r["engagement_bait"] or r["campaign_mention"] or r["high_reply_rate"]) for r in top),
        "top20": {k: share(top, k) for k in ("dollar_figure", "proof_link", "engagement_bait", "campaign_mention",
                                              "high_reply_rate", "has_media", "number_first", "caps_hook")},
        "rules": {"proof_domains": PROOF_DOMAINS, "campaign_min_authors": CAMPAIGN_MIN_AUTHORS,
                  "high_reply_rate": HIGH_REPLY_RATE, "min_views_for_rate": MIN_VIEWS_FOR_RATE},
    }
    (HERE / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    with (HERE / "posts_public.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({k: v for k, v in results.items() if k not in ("queries", "rules")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
