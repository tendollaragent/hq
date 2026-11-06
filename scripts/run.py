"""The scheduler. launchd runs it every 30 minutes; it does three things.

1. Posts scheduled items from content/schedule.json once they are due (within their "latest" window).
2. From 19:00 local time, writes and posts the Day N log, then appends it to LOG.md, updates the README
   scoreboard and pushes.
3. Answers people who mentioned me, replied to me or quoted me (scripts/replies.py).

The Day N writer is a model run in a locked-down mode inside a throwaway folder; the command that runs it
lives in private local config (TDA_WRITER). It can read the inputs
copied there and write one draft file; it cannot run commands or see the repo, so it cannot change
the scripts or checks below. Every draft passes these checks and post.py's checks before anything is posted.

Dry run by default. --live posts, logs and pushes. --test-writer writes a draft for today and only prints it.
Stops when the STOP file exists.
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import DAY_ZERO, METRICS_DIR  # noqa: E402 — 先把脚本目录放进 path
from post import REPO, STOP_FILE, read_posted  # noqa: E402
from replies import run_replies  # noqa: E402

PYTHON = sys.executable
SCRIPTS = REPO / "scripts"
SCHEDULE_FILE = REPO / "content/schedule.json"
QUEUE_DIR = REPO / "content/queue"
PROMPT_FILE = REPO / "ops/day_prompt.md"
STATE_DIR = Path.home() / "Library/Application Support/tendollaragent"  # 本机状态，不进仓库
DAY_N_HOUR = 19
FIRST_DAY_N = 1
MAX_WRITER_ATTEMPTS = 2  # 每天最多让模型写两次，失败就停下等人看
WRITER_TIMEOUT = 30 * 60
WRITER_INPUTS = ["CHARTER.md", "LOG.md", "content/PLAYBOOK.md", "content/posted.jsonl"]
# 写作命令和额外禁用词放在本机私有配置里，不进公开仓库
PRIVATE_DIR = Path.home() / ".config/tendollaragent"
PRIVATE_WORDS_FILE = PRIVATE_DIR / "forbidden_words.txt"
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def now():
    return datetime.now().astimezone()


def say(message):
    print(f"[{now().isoformat(timespec='seconds')}] {message}", flush=True)


def notify(message):
    """在 Mac 右上角弹通知；失败也不影响主流程。"""
    script = f'display notification {json.dumps(message)} with title "TenDollarAgent"'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def load_state():
    path = STATE_DIR / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"writer_attempts": {}, "notified": []}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps(state, indent=2) + "\n")


def notify_once(state, key, message):
    if key not in state["notified"]:
        notify(message)
        state["notified"].append(key)


def fully_posted(queue_path):
    item = json.loads(queue_path.read_text())
    done = {(e["platform"], e["index"]) for e in read_posted() if e["id"] == item["id"] and not e.get("deleted_at")}
    return all((p, i) in done for p in ("bluesky", "x") for i in range(len(item.get(p, []))))


def run_post(queue_path, live):
    command = [PYTHON, "-I", str(SCRIPTS / "post.py"), str(queue_path)] + (["--live"] if live else [])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    print(result.stdout + result.stderr, flush=True)
    return result.returncode == 0


def git_publish(paths, message):
    subprocess.run(["git", "-C", str(REPO), "add", "--", *map(str, paths)], check=True)
    staged = subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        return  # 没有改动
    subprocess.run(["git", "-C", str(REPO), "commit", "-q", "-m", message], check=True)
    push = subprocess.run(["git", "-C", str(REPO), "push", "-q"], capture_output=True, text=True, check=False)
    if push.returncode != 0:
        say(f"推送失败：{push.stderr.strip()[:300]}")
        notify("推送到 GitHub 失败，提交还在本机")


# ---------- 1. 定时帖 ----------

def run_schedule(live, state):
    if not SCHEDULE_FILE.exists():
        return
    current = now()
    for entry in json.loads(SCHEDULE_FILE.read_text()):
        queue_path = REPO / entry["queue"]
        if fully_posted(queue_path):
            continue
        at, latest = datetime.fromisoformat(entry["at"]), datetime.fromisoformat(entry["latest"])
        if current > latest:
            notify_once(state, f"missed:{entry['queue']}", f"错过了定时帖 {queue_path.stem}，没有发")
            continue
        if current < at:
            continue
        say(f"定时帖到点：{queue_path.stem}")
        if run_post(queue_path, live) and live:
            git_publish([REPO / "content/posted.jsonl"], f"chore(post): publish {queue_path.stem}")
            notify(f"已发布 {queue_path.stem}")


# ---------- 2. Day N ----------

def forbidden_pattern():
    """草稿里一律不许出现：@某人、链接，以及私有名单里的词。"""
    words = []
    if PRIVATE_WORDS_FILE.exists():
        words = [w.strip() for w in PRIVATE_WORDS_FILE.read_text().splitlines() if w.strip()]
    else:
        say(f"警告：没有私有禁用词名单 {PRIVATE_WORDS_FILE}")
    return re.compile("|".join([re.escape(w) for w in words] + [r"@\w", r"https?://", r"www\."]), re.IGNORECASE)


def known_numbers(metrics_path):
    """草稿里的数字必须在采数文件或公开日志里出现过，防止模型编数。"""
    sources = [metrics_path.read_text(), (REPO / "LOG.md").read_text(), (REPO / "content/PLAYBOOK.md").read_text()]
    return {n.replace(",", "") for text in sources for n in NUMBER.findall(text)}


def check_draft(draft, day, metrics_path):
    problems = []
    forbidden = forbidden_pattern()
    for platform in ("bluesky", "x"):
        posts = draft.get(platform)
        if not isinstance(posts, list) or not 1 <= len(posts) <= 2 or not all(isinstance(p, str) for p in posts):
            problems.append(f"{platform} 必须是 1–2 条文字")
            continue
        if not posts[0].startswith(f"Day {day}."):
            problems.append(f"{platform} 第一条要以 'Day {day}.' 开头")
        allowed = known_numbers(metrics_path)
        for text in posts:
            if forbidden.search(text):
                problems.append(f"{platform} 含禁用内容：{forbidden.search(text).group()}")
            unknown = {n.replace(",", "") for n in NUMBER.findall(text)} - allowed
            if unknown:
                problems.append(f"{platform} 有来源里找不到的数字：{sorted(unknown)}")
    log = draft.get("log")
    if not isinstance(log, list) or not 1 <= len(log) <= 5 or not all(isinstance(b, str) and b.strip() for b in log):
        problems.append("log 必须是 1–5 条要点")
        return problems
    # 日志里的文件路径带日期（content/metrics/2026-11-06.json），先去掉再核对数字
    log_text = re.sub(r"`[^`]*`", "", " ".join(log))
    if forbidden.search(log_text):
        problems.append(f"log 含禁用内容：{forbidden.search(log_text).group()}")
    unknown = {n.replace(",", "") for n in NUMBER.findall(log_text)} - known_numbers(metrics_path)
    if unknown:
        problems.append(f"log 有来源里找不到的数字：{sorted(unknown)}")
    return problems


def write_draft(day, today, metrics_path):
    """在临时目录里让模型写草稿；仓库对它不可见。返回草稿 dict，写不出来返回 None。"""
    # 写作命令在本机私有配置里（launchd 用 TDA_WRITER 传进来），负责以受限模式运行模型
    writer = os.environ.get("TDA_WRITER")
    if not writer or not Path(writer).exists():
        say("没有配置写作命令（环境变量 TDA_WRITER）")
        return None
    work = Path(tempfile.mkdtemp(prefix=f"tda-day{day:03d}-"))
    for rel in WRITER_INPUTS:
        shutil.copy(REPO / rel, work / Path(rel).name)
    shutil.copy(metrics_path, work / "metrics.json")
    (work / "previous").mkdir()
    for path in sorted(QUEUE_DIR.glob("day-*.json"))[-3:]:  # 最近三篇，避免重复用词
        shutil.copy(path, work / "previous" / path.name)

    prompt = PROMPT_FILE.read_text().replace("{{DAY}}", str(day)).replace("{{DATE}}", today.isoformat())
    say(f"让模型写 Day {day} 草稿（目录 {work}）")
    started = now()
    try:
        result = subprocess.run([writer], input=prompt, cwd=work, capture_output=True, text=True,
                                timeout=WRITER_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        say("写草稿超时")
        return None
    say(f"模型用时 {(now() - started).seconds} 秒")
    draft_path = work / "draft.json"
    if result.returncode != 0 or not draft_path.exists():
        say(f"没写出草稿（退出码 {result.returncode}）：{(result.stdout + result.stderr).strip()[-500:]}")
        return None
    try:
        return json.loads(draft_path.read_text())
    except ValueError as e:
        say(f"草稿不是合法 JSON：{e}")
        return None


def queue_item(draft, day, today, metrics_path):
    rel_metrics = metrics_path.relative_to(REPO) if metrics_path.is_relative_to(REPO) else metrics_path
    return {
        "id": f"day-{day:03d}",
        "sources": [f"{rel_metrics}: followers, posts and engagement collected {today.isoformat()}",
                    "LOG.md: ledger balance and today's decisions",
                    "content/posted.jsonl: what I posted"],
        "bluesky": draft["bluesky"], "x": draft["x"], "log": draft["log"],
    }


def bullet_text(bullet):
    """模型有时自己带了 "- " 前缀，去掉，免得变成 "- -"。"""
    return re.sub(r"^\s*[-*]\s+", "", bullet).strip()


def update_scoreboard(metrics):
    readme = REPO / "README.md"
    text = readme.read_text()
    for platform, label in (("bluesky", "Bluesky followers"), ("x", "X followers")):
        if "followers" in metrics[platform]:
            text = re.sub(rf"^\| {label} \| [^|]+ \|$", f"| {label} | {metrics[platform]['followers']:,} |", text, flags=re.M)
    readme.write_text(text)


def append_log(item, today, day):
    log = REPO / "LOG.md"
    header = f"## {today.isoformat()} · Day {day}"
    if header in log.read_text():
        return
    bullets = "\n".join(f"- {bullet_text(b)}" for b in item["log"])
    with log.open("a") as f:
        f.write(f"\n{header}\n\n{bullets}\n")


def run_day_n(live, state, test_writer=False):
    current = now()
    today = current.date()
    day = (today - DAY_ZERO).days
    if not test_writer and (day < FIRST_DAY_N or current.hour < DAY_N_HOUR):
        return
    queue_path = QUEUE_DIR / f"day-{day:03d}.json"
    if not test_writer and queue_path.exists() and fully_posted(queue_path):
        return

    if test_writer or not queue_path.exists():
        attempts = state["writer_attempts"].get(queue_path.stem, 0)
        if not test_writer and attempts >= MAX_WRITER_ATTEMPTS:
            notify_once(state, f"writer-gave-up:{queue_path.stem}", f"Day {day} 草稿写了 {attempts} 次都没过，今天不发")
            return
        metrics_dir = Path(tempfile.mkdtemp(prefix="tda-metrics-")) if test_writer else METRICS_DIR
        metrics_path = metrics_dir / f"{today.isoformat()}.json"
        subprocess.run([PYTHON, "-I", str(SCRIPTS / "metrics.py"), "--out", str(metrics_path)], check=True)
        if not test_writer:
            state["writer_attempts"][queue_path.stem] = attempts + 1
            save_state(state)
        draft = write_draft(day, today, metrics_path)
        if draft is None:
            return
        if "skip" in draft:
            say(f"模型决定今天不发：{draft['skip']}")
            notify_once(state, f"skip:{queue_path.stem}", f"Day {day} 没发：{draft['skip'][:80]}")
            return
        problems = check_draft(draft, day, metrics_path)
        for p in problems:
            say(f"✗ {p}")
        if problems:
            return
        item = queue_item(draft, day, today, metrics_path)
        if test_writer:
            preview = Path(tempfile.mkdtemp(prefix="tda-preview-")) / f"{item['id']}.json"
            preview.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n")
            run_post(preview, live=False)
            print("log:\n" + "\n".join(f"- {bullet_text(b)}" for b in item["log"]))
            return
        queue_path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n")

    item = json.loads(queue_path.read_text())
    metrics_path = METRICS_DIR / f"{today.isoformat()}.json"
    if not run_post(queue_path, live):
        if live:
            notify_once(state, f"post-failed:{queue_path.stem}", f"Day {day} 发布失败，日志里有原因")
        return
    if not live:
        return
    append_log(item, today, day)
    update_scoreboard(json.loads(metrics_path.read_text()))
    git_publish([queue_path, metrics_path, REPO / "content/posted.jsonl", REPO / "LOG.md", REPO / "README.md"],
                f"chore(day): Day {day} log")
    notify(f"Day {day} 已发布")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="真的发布、记日志、推送；不加则只预览")
    parser.add_argument("--test-writer", action="store_true", help="现在就写一篇今天的草稿，只打印，不动仓库")
    args = parser.parse_args()

    if STOP_FILE.exists():
        say("STOP 文件存在，什么都不做")
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            say("上一轮还在跑，跳过")
            return
        state = load_state()
        try:
            if args.test_writer:
                run_day_n(live=False, state=state, test_writer=True)
            else:
                run_schedule(args.live, state)
                run_day_n(args.live, state)
                try:  # 回复出了问题不能拖垮发帖和日志
                    run_replies(args.live, state, SimpleNamespace(
                        say=say, notify=notify, notify_once=lambda key, message: notify_once(state, key, message),
                        git_publish=git_publish, forbidden=forbidden_pattern))
                except Exception as e:  # noqa: BLE001 — 任何异常都只记下、弹一次通知
                    say(f"自动回复出错：{type(e).__name__}: {e}")
                    notify_once(state, f"replies-crashed:{now().date()}", "自动回复出错，日志里有原因")
        finally:
            save_state(state)


if __name__ == "__main__":
    main()
