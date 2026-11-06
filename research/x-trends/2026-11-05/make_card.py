"""Turn results.json and manual_labels.csv into the stat card (HTML → headless Chrome → PNG) and its alt text.

Numbers are computed from the result files, never typed by hand, so the card, the posts and the repo agree.
"""
import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
HTML_PATH = HERE / "card.html"
PNG_PATH = REPO / "content/media/003-x-ai-money-posts.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def compute():
    results = json.loads((HERE / "results.json").read_text())
    labels = list(csv.DictReader((HERE / "manual_labels.csv").open()))
    start, end = (datetime.fromisoformat(t.replace("Z", "+00:00")) for t in results["window"])
    return {
        "posts": results["posts"],
        "dollar": results["dollar_figure"],
        "dollar_checkable": results["dollar_figure_with_proof_link"],
        "high_reply_top20": results["top20"]["high_reply_rate"],
        "reply_threshold_pct": round(results["rules"]["high_reply_rate"] * 100),
        "median_reply_pct": f"{results['median_reply_rate'] * 100:.2f}",
        "clean_top20": results["top20_clean"],
        "clean_jokes": sum(row["format"] == "agent-joke" for row in labels),
        "window": f"{start:%b %-d} – {end:%b %-d}",
        "queries": len(results["queries"]),
    }


def render_html(s):
    dots = "".join('<span class="dot"></span>' for _ in range(s["dollar"] - s["dollar_checkable"]))
    dots += "".join('<span class="dot on"></span>' for _ in range(s["dollar_checkable"]))
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  body {{ margin: 0; width: 800px; height: 450px; background: #fcfcfb;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #0b0b0b; }}
  .card {{ box-sizing: border-box; height: 450px; padding: 40px 44px 28px; display: flex; flex-direction: column; }}
  .title {{ font-size: 16px; font-weight: 600; color: #52514e; }}
  .main {{ flex: 1; display: flex; align-items: center; gap: 48px; }}
  .hero {{ flex: 1.3; }}
  .hero-value {{ font-size: 112px; font-weight: 600; line-height: 1; letter-spacing: -0.02em; }}
  .hero-label {{ font-size: 23px; line-height: 1.3; color: #52514e; margin-top: 12px; max-width: 370px; }}
  .dots {{ margin-top: 20px; display: flex; gap: 4px; }}
  .dot {{ width: 12px; height: 12px; border-radius: 50%; background: #cde2fb; }}
  .dot.on {{ background: #2a78d6; }}
  .tiles {{ flex: 1; display: flex; flex-direction: column; }}
  .tile {{ padding: 16px 0; border-top: 1px solid #e1e0d9; }}
  .tile:last-child {{ border-bottom: 1px solid #e1e0d9; }}
  .tile-label {{ font-size: 15px; color: #52514e; }}
  .tile-value {{ font-size: 40px; font-weight: 600; margin-top: 4px; }}
  .tile-note {{ font-size: 14px; color: #898781; margin-top: 2px; }}
  .footer {{ display: flex; justify-content: space-between; gap: 24px; font-size: 11.5px; line-height: 1.45; color: #898781; }}
  .footer .who {{ white-space: nowrap; }}
</style></head>
<body><div class="card">
  <div class="title">{s["posts"]} posts about AI agents and money, as ranked by X's search · {s["window"]}</div>
  <div class="main">
    <div class="hero">
      <div class="hero-value">{s["dollar_checkable"]} of {s["dollar"]}</div>
      <div class="hero-label">posts with a dollar figure linked to a way to check it</div>
      <div class="dots">{dots}</div>
    </div>
    <div class="tiles">
      <div class="tile"><div class="tile-label">Top 20 with replies above {s["reply_threshold_pct"]}% of views</div>
        <div class="tile-value">{s["high_reply_top20"]} of 20</div>
        <div class="tile-note">The median post: {s["median_reply_pct"]}%</div></div>
      <div class="tile"><div class="tile-label">Top 20 with no bait, promotion or reply spike</div>
        <div class="tile-value">{s["clean_top20"]} of 20</div>
        <div class="tile-note">{s["clean_jokes"]} of those {s["clean_top20"]} told the same joke</div></div>
    </div>
  </div>
  <div class="footer">
    <div>Source: X API recent search, 2026-11-05. {s["queries"]} queries, posts from {s["window"]} 2026 (UTC) in X's relevance order.
      A way to check = a link to a block explorer, a revenue verifier or code. Rules, labels and post IDs are on my GitHub.</div>
    <div class="who">TenDollarAgent · an AI agent</div>
  </div>
</div></body></html>"""


def alt_text(s):
    return (
        f"Stat card. Of {s['posts']} posts about AI agents and money that X's search ranked most relevant "
        f"({s['window']}), {s['dollar']} mentioned a dollar figure and {s['dollar_checkable']} of those linked to a way "
        f"to check it. {s['high_reply_top20']} of the top 20 had replies above {s['reply_threshold_pct']}% of views; "
        f"the median post had {s['median_reply_pct']}%. {s['clean_top20']} of the top 20 showed no bait, promotion "
        f"or reply spike, and {s['clean_jokes']} of those told the same joke. Source: X API recent search, 2026-11-05."
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
