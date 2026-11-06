"""最近上架的 Apify Actor：按每个 Actor 的真实 createdAt 统计用户数（第一篇内容的数据底稿）。

已知接口限制（2026-11-05 实测，写进报告的方法说明）：
- 每次查询最多能翻到约 1.5 万个位置，再往后返回空列表
- 每页名义 1000 条，实际返回 800 多条；count 字段不可信
- newest 排序不严格按 createdAt，旧 Actor 发新版本也会排到前面，所以年龄必须逐个查
"""
import csv
import json
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = sys.argv[1]
API = "https://api.apify.com/v2"
SAMPLE_SIZE = 1500
random.seed(11)


def get_json(url, tries=8):
    for i in range(tries):
        r = subprocess.run(["curl", "-s", "-m", "60", url], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"连续 {tries} 次请求失败：{url}")


def share(values, cond):
    return sum(1 for v in values if cond(v)) / len(values)


def main():
    listing, seen, offset, page_sizes = [], set(), 0, []
    while True:
        items = get_json(f"{API}/store?limit=1000&offset={offset}&sortBy=newest")["data"]["items"]
        page_sizes.append(len(items))
        if not items:
            break
        for it in items:
            slug = f"{it['username']}~{it['name']}"
            if slug not in seen:
                seen.add(slug)
                listing.append((slug, (it.get("stats") or {}).get("totalUsers30Days") or 0))
        offset += 1000
        time.sleep(0.3)
    print(f"newest 列表可翻到 {len(listing)} 个唯一 Actor；每页实际条数 {page_sizes}")

    now = datetime.now(timezone.utc)
    rows = []
    for slug, users_30d in random.sample(listing, min(SAMPLE_SIZE, len(listing))):
        ts = (get_json(f"{API}/acts/{slug}").get("data") or {}).get("createdAt")
        if ts:
            age = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 86400
            rows.append({"slug": slug, "age_days": round(age, 2), "users_30d": users_30d})
        time.sleep(0.1)
    print(f"抽样 {SAMPLE_SIZE}，查到 createdAt 的 {len(rows)}")

    ages = [r["age_days"] for r in rows]
    print(f"样本年龄：≤7 天 {share(ages, lambda a: a <= 7):.1%} · ≤14 天 {share(ages, lambda a: a <= 14):.1%} · "
          f"≤30 天 {share(ages, lambda a: a <= 30):.1%} · 中位 {statistics.median(ages):.1f} 天")
    created_14d = share(ages, lambda a: a <= 14) * len(listing)
    print(f"推算：列表里创建于最近 14 天的 Actor 约 {created_14d:.0f} 个（下限，列表外的看不到），约 {created_14d / 14:.0f} 个/天")

    print("\n按年龄分组的 30 天用户数：")
    for lo, hi in [(0, 7), (7, 14), (14, 30), (30, 90), (90, 100000)]:
        us = sorted(r["users_30d"] for r in rows if lo <= r["age_days"] < hi)
        if us:
            print(f"{lo:>3}-{hi:<6}天 n={len(us):4d}  0用户={share(us, lambda u: u == 0):5.1%}  "
                  f"恰好1={share(us, lambda u: u == 1):5.1%}  ≥2={share(us, lambda u: u >= 2):5.1%}  "
                  f"≥10={share(us, lambda u: u >= 10):5.1%}  中位={statistics.median(us):.0f}  最大={us[-1]}")

    with open(f"{OUT_DIR}/apify_recent_sample.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "age_days", "users_30d"])
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
