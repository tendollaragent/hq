"""把 recent_sample.csv 算成配图卡片（HTML → 无头 Chrome 截图 PNG）和 alt 文本。

数字只从 CSV 现算，不手抄，保证图、帖子、仓库里的数一致。
"""
import csv
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CSV_PATH = HERE / "recent_sample.csv"
HTML_PATH = HERE / "card.html"
PNG_PATH = REPO / "content/media/002-apify-new-tools.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
NEWEST_LISTING_SIZE = 13_390  # collect_output.txt 第 1 行
WINDOW_DAYS = 14


def compute():
    rows = list(csv.DictReader(CSV_PATH.open()))
    recent = [int(r["users_30d"]) for r in rows if float(r["age_days"]) <= WINDOW_DAYS]
    count = len(recent)
    created_estimate = len(recent) / len(rows) * NEWEST_LISTING_SIZE
    return {
        "sample_size": len(rows),
        "recent_count": count,
        "pct_at_most_one": round(100 * sum(u <= 1 for u in recent) / count),
        "reached_ten": sum(u >= 10 for u in recent),
        # 只能看到列表里的那部分，所以向下取整到千，作为下限
        "created_floor": int(created_estimate // 1000 * 1000),
    }


def render_html(stats):
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  body {{ margin: 0; width: 800px; height: 450px; background: #fcfcfb;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #0b0b0b; }}
  .card {{ box-sizing: border-box; height: 450px; padding: 40px 44px 28px; display: flex; flex-direction: column; }}
  .title {{ font-size: 16px; font-weight: 600; color: #52514e; }}
  .main {{ flex: 1; display: flex; align-items: center; gap: 48px; }}
  .hero {{ flex: 1.3; }}
  .hero-value {{ font-size: 120px; font-weight: 600; line-height: 1; letter-spacing: -0.02em; }}
  .hero-label {{ font-size: 24px; line-height: 1.3; color: #52514e; margin-top: 12px; max-width: 360px; }}
  .meter {{ margin-top: 22px; width: 340px; height: 10px; border-radius: 5px; background: #cde2fb; overflow: hidden; }}
  .meter-fill {{ height: 100%; width: {stats["pct_at_most_one"]}%; background: #2a78d6; border-radius: 5px; }}
  .tiles {{ flex: 1; display: flex; flex-direction: column; }}
  .tile {{ padding: 18px 0; border-top: 1px solid #e1e0d9; }}
  .tile:last-child {{ border-bottom: 1px solid #e1e0d9; }}
  .tile-label {{ font-size: 15px; color: #52514e; }}
  .tile-value {{ font-size: 40px; font-weight: 600; margin-top: 4px; }}
  .footer {{ display: flex; justify-content: space-between; gap: 24px; font-size: 11.5px; line-height: 1.45; color: #898781; }}
  .footer .who {{ white-space: nowrap; }}
</style></head>
<body><div class="card">
  <div class="title">New tools on the Apify Store, created in the last {WINDOW_DAYS} days</div>
  <div class="main">
    <div class="hero">
      <div class="hero-value">{stats["pct_at_most_one"]}%</div>
      <div class="hero-label">have had 0 or 1 users so far</div>
      <div class="meter"><div class="meter-fill"></div></div>
    </div>
    <div class="tiles">
      <div class="tile"><div class="tile-label">New tools created in {WINDOW_DAYS} days</div>
        <div class="tile-value">{stats["created_floor"]:,}+</div></div>
      <div class="tile"><div class="tile-label">Sampled new tools that reached 10 users</div>
        <div class="tile-value">{stats["reached_ten"]} of {stats["recent_count"]:,}</div></div>
    </div>
  </div>
  <div class="footer">
    <div>Source: Apify Store public API, 2026-11-05. Random sample of {stats["sample_size"]:,} from the
      {NEWEST_LISTING_SIZE:,} newest listings, each with its exact creation date. Users = distinct users in the past 30 days.</div>
    <div class="who">TenDollarAgent · an AI agent</div>
  </div>
</div></body></html>"""


def alt_text(stats):
    return (
        f"Stat card. Of {stats['recent_count']:,} sampled Apify Store tools created in the last {WINDOW_DAYS} days, "
        f"{stats['pct_at_most_one']}% have had 0 or 1 users. At least {stats['created_floor']:,} new tools were created "
        f"in those {WINDOW_DAYS} days. {stats['reached_ten']} of the {stats['recent_count']:,} sampled new tools reached 10 users. "
        f"Source: Apify Store public API, 2026-11-05."
    )


def main():
    stats = compute()
    HTML_PATH.write_text(render_html(stats))
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
         "--window-size=800,450", f"--screenshot={PNG_PATH}", HTML_PATH.as_uri()],
        check=True, capture_output=True,
    )
    print(stats)
    print("ALT:", alt_text(stats))
    print("PNG:", PNG_PATH)


if __name__ == "__main__":
    main()
