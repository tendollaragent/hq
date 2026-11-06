"""Nightly reflection: look back at what I posted, how it did and what my operator said, then update the lessons.

run.py calls run_reflection() every 30 minutes. It acts once a day: from 19:00 local time once today's Day N post
is out, or from 21:00 whatever happened. Four steps:

1. Evaluate (code): join each post I made to the newest numbers collected for it.
2. Reflect (the locked-down writer, TDA_WRITER): an After Action Review per post, then a list of operations on the
   lessons (ADD, EDIT, AGREE, REMOVE). The writer sees only my own posts, numbers computed by code and my operator's
   feedback. Never a stranger's words: a self-updating memory that reads strangers amplifies prompt injection.
3. Apply (code): the vote rules of ExpeL (github.com/LeapLabTHU/ExpeL, agent/expel.py update_rules). Operations that
   break a rule are dropped and logged, not applied.
4. Publish: ops/lessons.json (source of truth), LESSONS.md, one line in LOG.md, then commit and push.

Writers read the result through lessons_for(writer, platform). Dry run prints what would change and writes nothing.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import DAY_ZERO, METRICS_DIR  # noqa: E402 — 先把脚本目录放进 path
from post import AI_TELLS, REPO, read_posted  # noqa: E402

LESSONS_FILE = REPO / "ops/lessons.json"
FEEDBACK_FILE = REPO / "ops/feedback.jsonl"
PUBLIC_LESSONS = REPO / "LESSONS.md"
LOG_FILE = REPO / "LOG.md"
PROMPT_FILE = REPO / "ops/reflect_prompt.md"
QUEUE_DIR = REPO / "content/queue"
METRICS_SCRIPT = Path(__file__).resolve().parent / "metrics.py"
WRITER_INPUTS = ["CHARTER.md", "LOG.md", "ops/voice.md"]
NUMBER_SOURCES = ["LOG.md", "content/PLAYBOOK.md"]
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

REFLECT_HOUR = 19      # Day N 发完就复盘，最早 19:00
FALLBACK_HOUR = 21     # Day N 没发出来也不能跳过复盘
MAX_ATTEMPTS = 2       # 每天最多让写手跑两次，还不能用就放弃
WRITER_TIMEOUT = 10 * 60
RECENT_POSTS = 40      # 给写手看最近多少条帖子
FEEDBACK_SHOWN = 30    # 给写手看最近多少条反馈

LESSON_CAP = 12
MAX_LESSON_CHARS = 200
NEW_LESSON_VOTES = 2
FULL_REMOVE_PENALTY = 3  # 列表满了，REMOVE 一次扣 3 票，腾位置更快
STALE_DAYS = 14
GUESS_BELOW = 3          # 证据少于 3 条的标 guess
SUNDAY = 6
WRITERS = ("post", "reply", "day", "all")
PLATFORMS = ("x", "bluesky", "all")
OP_ORDER = {"REMOVE": 0, "AGREE": 1, "EDIT": 2, "ADD": 3}  # 应用顺序，同 ExpeL
PLATFORM_NAMES = {"x": "X", "bluesky": "Bluesky", "all": "all platforms"}


def local_now():
    return datetime.now().astimezone()


# ---------- 读写 ----------

def read_lessons():
    return json.loads(LESSONS_FILE.read_text())


def read_feedback():
    return [json.loads(line) for line in FEEDBACK_FILE.read_text().splitlines() if line.strip()]


def write_lessons(lessons):
    LESSONS_FILE.write_text(json.dumps(lessons, ensure_ascii=False, indent=2) + "\n")
    PUBLIC_LESSONS.write_text(render_public(lessons))


def numbers_in(text):
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def normalize(text):
    """查重用：忽略大小写、标点和空白。"""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# ---------- 给写手读的列表（AutoGuide：按写手和平台分流） ----------

def evidence_note(lesson):
    count = len(lesson["evidence"])
    label = "guess, " if count < GUESS_BELOW else ""
    return f"{label}votes {lesson['votes']}, evidence {count}"


def lessons_for(writer, platform):
    """Markdown of the lessons that apply to this writer ("post", "reply" or "day") on this platform.
    platform "all" (the Day N writer posts on both) sees every platform's lessons."""
    try:
        lessons = read_lessons()
    except ValueError as e:  # 人手改坏了 JSON：Day N 照发，只是这一轮没有经验可看
        print(f"警告：{LESSONS_FILE} 读不了，这一轮不带经验：{e}", file=sys.stderr, flush=True)
        lessons = []
    mine = [item for item in lessons if item["writer"] in (writer, "all")
            and (platform == "all" or item["platform"] in (platform, "all"))]
    if not mine:
        return "No lessons yet.\n"
    lines = []
    for item in mine:
        scope = f"[{PLATFORM_NAMES[item['platform']]}] " if platform == "all" and item["platform"] != "all" else ""
        lines.append(f"- {scope}{item['text']} ({evidence_note(item)})")
    return "# What I've learned so far\n\n" + "\n".join(lines) + "\n"


def render_numbered(lessons):
    """复盘写手看到的列表：编号就是操作里的 n。"""
    used = f"Slots used: {len(lessons)} of {LESSON_CAP}."
    if len(lessons) >= LESSON_CAP:
        used += " The list is full: prefer REMOVE over ADD."
    if not lessons:
        return f"No lessons yet. {used}\n"
    rows = [f"{i}. [{item['writer']} / {item['platform']}] {item['text']} ({evidence_note(item)})"
            for i, item in enumerate(lessons, start=1)]
    return used + "\n\n" + "\n".join(rows) + "\n"


def render_public(lessons):
    header = (
        "# Lessons\n\n"
        "Every night I look at what I posted, how it did and what my operator said, and update this list. "
        f"A new lesson starts with {NEW_LESSON_VOTES} votes; more evidence adds one, and a lesson at 0 votes is dropped.\n"
        f"The list holds {LESSON_CAP} at most. A lesson with fewer than {GUESS_BELOW} pieces of evidence is marked \"guess\". "
        f"One nobody confirms for {STALE_DAYS} days loses a vote each Sunday.\n"
        "Source of truth: `ops/lessons.json`. Written by `scripts/reflect.py`.\n\n")
    if not lessons:
        return header + "No lessons yet.\n"
    rows = []
    for i, item in enumerate(lessons, start=1):
        count = len(item["evidence"])
        guess = " (guess)" if count < GUESS_BELOW else ""
        rows.append(f"{i}. {item['text']}\n"
                    f"   For: {item['writer']} writer, {PLATFORM_NAMES[item['platform']]}. "
                    f"Votes: {item['votes']}. Evidence: {count}{guess}.")
    return header + "\n".join(rows) + "\n"


# ---------- 1. 评估：帖子 + 数字（代码算，不让模型算） ----------

def load_metrics(today, say):
    """content/metrics 里所有采数文件；今天的还没有（Day N 没跑）就临时采一份，不进仓库。"""
    files = sorted(METRICS_DIR.glob("*.json"))
    metrics_list = [json.loads(path.read_text()) for path in files]
    if any(path.stem == today.isoformat() for path in files):
        return metrics_list
    scratch = Path(tempfile.mkdtemp(prefix="tda-reflect-metrics-"))
    try:
        out = scratch / f"{today.isoformat()}.json"
        collect = subprocess.run([sys.executable, "-I", str(METRICS_SCRIPT), "--out", str(out)],
                                 capture_output=True, text=True, check=False)
        if collect.returncode == 0:
            metrics_list.append(json.loads(out.read_text()))
        else:
            say(f"今天的数字没采到，用旧的：{(collect.stdout + collect.stderr).strip()[-300:]}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return metrics_list


def latest_measurements(metrics_list):
    """(平台, 帖子引用) -> (采数时间, 数字)，取最近一份含这条帖子的采数文件。"""
    found = {}
    for metrics in sorted(metrics_list, key=lambda m: datetime.fromisoformat(m["collected_at"]), reverse=True):
        for platform in ("bluesky", "x"):
            for post in metrics[platform].get("recent_posts", []):  # 平台出错时没有 recent_posts
                found.setdefault((platform, post.get("uri") or post.get("id")), (metrics["collected_at"], post))
    return found


def experience(entry, measurement):
    row = {"queue_id": entry["id"], "platform": entry["platform"], "text": entry["text"],
           "posted_at": entry["posted_at"], "measured": measurement is not None}
    if measurement is None:
        return row
    collected_at, post = measurement
    counts = {"likes": post["likes"], "reposts": post["reposts"],
              "replies": post.get("replies_from_others", post.get("replies", 0)),  # 采数时已扣掉我自己串里的回复
              "quotes": post["quotes"]}
    hours = (datetime.fromisoformat(collected_at) - datetime.fromisoformat(entry["posted_at"])).total_seconds() / 3600
    row.update(counts, hours_measured=round(hours, 1), score=sum(counts.values()))
    if entry["platform"] == "x":
        row["impressions"] = post.get("impressions")
    return row


def build_experiences(posted, metrics_list):
    found = latest_measurements(metrics_list)
    rows = [experience(e, found.get((e["platform"], e["ref"].get("uri") or e["ref"].get("id"))))
            for e in posted if e["index"] == 0 and not e.get("deleted_at")]
    rows.sort(key=lambda row: datetime.fromisoformat(row["posted_at"]))
    scores = [row["score"] for row in rows if row["measured"]]
    newest = max(metrics_list, key=lambda m: datetime.fromisoformat(m["collected_at"]), default=None)
    return {
        "followers": {p: newest[p].get("followers") for p in ("bluesky", "x")} if newest else {},
        "measured_posts": len(scores),
        "median_score": median(scores) if scores else None,
        "posts": rows[-RECENT_POSTS:],
    }


def number_forms(value):
    text = str(value)
    return {text, str(int(value))} if float(value) == int(value) else {text}


def allowed_numbers(metrics_list, experiences, feedback):
    """教训里的数字必须出现在：采数文件、LOG、PLAYBOOK、反馈，或代码算出来的那几个。"""
    texts = ([json.dumps(m) for m in metrics_list] + [(REPO / rel).read_text() for rel in NUMBER_SOURCES]
             + [event["note"] for event in feedback])
    allowed = {n for text in texts for n in numbers_in(text)}
    computed = [experiences["measured_posts"], experiences["median_score"], *experiences["followers"].values()]
    for row in experiences["posts"]:
        computed += [row.get(key) for key in ("likes", "reposts", "replies", "quotes", "impressions", "score", "hours_measured")]
    for value in computed:
        if isinstance(value, (int, float)):
            allowed |= number_forms(value)
    return allowed


# ---------- 2. 反思：让写手出操作 ----------

def prepare_workdir(work, experiences, feedback, lessons):
    for rel in WRITER_INPUTS:
        shutil.copy(REPO / rel, work / Path(rel).name)
    (work / "experiences.json").write_text(json.dumps(experiences, ensure_ascii=False, indent=2) + "\n")
    (work / "feedback.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in feedback))
    (work / "lessons.md").write_text(render_numbered(lessons))


def call_writer(work, prompt, say):
    """跑写手；退出码 0 且写出了 ops.json 才算成功。"""
    writer = os.environ.get("TDA_WRITER")
    if not writer or not Path(writer).exists():
        say("没有配置写作命令（环境变量 TDA_WRITER）")
        return False
    try:
        writer_run = subprocess.run([writer], input=prompt, cwd=work, capture_output=True, text=True,
                                    timeout=WRITER_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        say("复盘写手超时")
        return False
    if writer_run.returncode != 0 or not (work / "ops.json").exists():
        say(f"复盘写手没写出结果（退出码 {writer_run.returncode}）：{(writer_run.stdout + writer_run.stderr).strip()[-500:]}")
        return False
    return True


def ask_writer(day, today, experiences, feedback, lessons, say):
    """在临时目录里跑一次复盘；返回写手的 dict，不能用就返回 None。目录用完就删。"""
    work = Path(tempfile.mkdtemp(prefix="tda-reflect-"))
    try:
        prepare_workdir(work, experiences, feedback, lessons)
        prompt = (PROMPT_FILE.read_text().replace("{{DATE}}", today.isoformat()).replace("{{DAY}}", str(day))
                  .replace("{{CAP}}", str(LESSON_CAP)).replace("{{MAX_CHARS}}", str(MAX_LESSON_CHARS)))
        say(f"让写手复盘 Day {day}（{len(experiences['posts'])} 条帖子，{len(feedback)} 条反馈）")
        if not call_writer(work, prompt, say):
            return None
        try:
            answer = json.loads((work / "ops.json").read_text())
        except ValueError as e:
            say(f"复盘结果不是合法 JSON：{e}")
            return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not isinstance(answer, dict):
        say("复盘结果不是 JSON 对象")
        return None
    return answer


# ---------- 3. 检查并应用（ExpeL 规则） ----------

def text_problems(text, forbidden, allowed):
    if not isinstance(text, str) or not text.strip():
        return ["text is empty"]
    text = text.strip()
    problems = []
    if len(text) > MAX_LESSON_CHARS:
        problems.append(f"{len(text)} characters, over {MAX_LESSON_CHARS}")
    if "\n" in text:
        problems.append("more than one line")
    hit = forbidden.search(text)
    if hit:
        problems.append(f"forbidden content: {hit.group()}")
    tell = AI_TELLS.search(text)
    if tell:
        problems.append(f"AI tell: {tell.group()!r}")
    unknown = numbers_in(text) - allowed
    if unknown:
        problems.append(f"numbers not in the sources: {sorted(unknown)}")
    return problems


class OpVetter:
    """一条条检查写手给的操作，通过的整理成干净的 op，不通过的给出原因。
    n 指的是写手看到的编号（复盘开始时的列表），所以检查和应用都按这份快照走。"""

    def __init__(self, lessons, valid_ids, forbidden, allowed):
        self.lessons, self.valid_ids = lessons, valid_ids
        self.forbidden, self.allowed = forbidden, allowed
        self.used_n = set()  # 每条教训一晚上只接受一个操作（ExpeL 只防了重复 REMOVE，这里一并防）
        self.owner_of_text = {normalize(item["text"]): item["id"] for item in lessons}

    def vet(self, raw):
        """返回 (op, None) 或 (None, 被丢掉的原因)。"""
        if not isinstance(raw, dict) or not isinstance(raw.get("op"), str) or raw["op"] not in OP_ORDER:
            return None, "unknown op"
        evidence = self.clean_evidence(raw.get("evidence"))
        if raw["op"] == "ADD":
            return self.vet_add(raw, evidence)
        return self.vet_existing(raw["op"], raw, evidence)

    def clean_evidence(self, evidence):
        ids = evidence if isinstance(evidence, list) else []
        return list(dict.fromkeys(i for i in ids if isinstance(i, str) and i in self.valid_ids))

    def problem_with_text(self, text):
        return "; ".join(text_problems(text, self.forbidden, self.allowed))

    def vet_add(self, raw, evidence):
        if raw.get("writer") not in WRITERS or raw.get("platform") not in PLATFORMS:
            return None, "writer or platform is not one of the allowed values"
        problem = self.problem_with_text(raw.get("text"))
        if problem:
            return None, problem
        if not evidence:
            return None, "no valid evidence id"
        norm = normalize(raw["text"])
        if norm in self.owner_of_text:
            return None, f"duplicate of {self.owner_of_text[norm]}"
        self.owner_of_text[norm] = "an earlier ADD"
        return {"op": "ADD", "text": raw["text"].strip(), "writer": raw["writer"], "platform": raw["platform"],
                "evidence": evidence}, None

    def vet_existing(self, kind, raw, evidence):
        n = raw.get("n")
        if type(n) is not int or not 1 <= n <= len(self.lessons):  # type is int 排除 True/False
            return None, f"no lesson {n!r}"
        if n in self.used_n:
            return None, f"lesson {n} already has an operation tonight"
        if kind == "REMOVE":
            self.used_n.add(n)
            return {"op": "REMOVE", "n": n, "reason": str(raw.get("reason", ""))[:200]}, None
        if not evidence:
            return None, "no valid evidence id"
        if kind == "EDIT":
            return self.vet_edit(n, raw, evidence)
        self.used_n.add(n)
        return {"op": "AGREE", "n": n, "evidence": evidence}, None

    def vet_edit(self, n, raw, evidence):
        problem = self.problem_with_text(raw.get("text"))
        if problem:
            return None, problem
        text = raw["text"].strip()
        lesson_id = self.lessons[n - 1]["id"]
        owner = self.owner_of_text.get(normalize(text))
        if owner is not None and owner != lesson_id:
            return None, f"duplicate of {owner}"
        self.used_n.add(n)
        if owner == lesson_id:  # 和自己原文一样的 EDIT 当 AGREE
            return {"op": "AGREE", "n": n, "evidence": evidence}, None
        self.owner_of_text[normalize(text)] = lesson_id
        return {"op": "EDIT", "n": n, "text": text, "evidence": evidence}, None


def vet_ops(raw_ops, vetter):
    kept, rejected = [], []
    for raw in raw_ops:
        op, why = vetter.vet(raw)
        if why is None:
            kept.append(op)
        else:
            rejected.append((raw, why))
    return kept, rejected


def merge_evidence(old, new):
    return list(dict.fromkeys([*old, *new]))


def apply_op(lesson, op, remove_penalty, today):
    if op["op"] == "REMOVE":
        lesson["votes"] -= remove_penalty
        return
    lesson["votes"] += 1
    lesson["evidence"] = merge_evidence(lesson["evidence"], op["evidence"])
    lesson["updated"] = today.isoformat()
    if op["op"] == "EDIT":
        lesson["text"] = op["text"]


def apply_ops(lessons, ops, today):
    """ExpeL update_rules：REMOVE、AGREE、EDIT、ADD 依次来。返回新列表，不动传进来的。"""
    lessons = copy.deepcopy(lessons)
    by_n = dict(enumerate(lessons, start=1))
    remove_penalty = FULL_REMOVE_PENALTY if len(lessons) >= LESSON_CAP else 1
    next_id = max((int(item["id"][1:]) for item in lessons), default=0) + 1
    for op in sorted(ops, key=lambda o: OP_ORDER[o["op"]]):  # sorted 稳定，同类保持写手的顺序
        if op["op"] == "ADD":
            lessons.append({"id": f"L{next_id}", "text": op["text"], "writer": op["writer"], "platform": op["platform"],
                            "votes": NEW_LESSON_VOTES, "evidence": op["evidence"],
                            "created": today.isoformat(), "updated": today.isoformat()})
            next_id += 1
        else:
            apply_op(by_n[op["n"]], op, remove_penalty, today)
    return lessons


def decay_stale(lessons, today):
    """周日检查：两周没人 AGREE 或 EDIT 的教训扣一票。"""
    for item in lessons:
        if (today - date.fromisoformat(item["updated"][:10])).days >= STALE_DAYS:
            item["votes"] -= 1


def settle(lessons):
    """0 票出局，票多的在前（同票保持原顺序），超过上限只留前 LESSON_CAP 条。"""
    alive = [item for item in lessons if item["votes"] > 0]
    alive.sort(key=lambda item: item["votes"], reverse=True)
    return alive[:LESSON_CAP]


def update_lessons(lessons, ops, today):
    updated = apply_ops(lessons, ops, today)
    if today.weekday() == SUNDAY:
        decay_stale(updated, today)
    return settle(updated)


def usable_plan(answer, vetter, say):
    """返回 {"ops", "summary"}；写手的结果不能用就返回 None（这次算失败）。"""
    if answer is None:
        return None
    if "skip" in answer:
        say(f"写手决定今天不改教训：{str(answer['skip'])[:120]}")
        return {"ops": [], "summary": ""}
    raw_ops = answer.get("ops")
    if not isinstance(raw_ops, list):
        say("复盘结果里没有 ops 列表，也没有 skip")
        return None
    ops, rejected = vet_ops(raw_ops, vetter)
    for raw, why in rejected:
        say(f"✗ 丢掉操作 {json.dumps(raw, ensure_ascii=False)[:160]}：{why}")
    if raw_ops and not ops:
        return None
    summary = answer.get("summary")
    summary = re.sub(r"^今天学到[：:]\s*", "", summary.strip()) if isinstance(summary, str) else ""  # 通知前缀由代码加
    return {"ops": ops, "summary": summary}


# ---------- 4. 发布 ----------

def describe_changes(ops):
    words = {"ADD": "added", "AGREE": "confirmed", "EDIT": "reworded", "REMOVE": "downvoted"}
    counts = Counter(op["op"] for op in ops)
    parts = [f"{counts[kind]} {words[kind]}" for kind in OP_ORDER if counts[kind]]
    return ", ".join(parts) if parts else "nothing new from today's posts"


def add_log_bullet(text, today):
    """在 LOG.md 今天那一节末尾加一条；今天还没有这一节就不加。"""
    lines = LOG_FILE.read_text().split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## {today.isoformat()}")), None)
    if start is None:
        return
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():  # 插在这一节最后一行内容之后，不是空行之后
        end -= 1
    bullet = f"- {text}"
    if bullet not in lines[start:end]:
        lines.insert(end, bullet)
        LOG_FILE.write_text("\n".join(lines))


def show_plan(ops, lessons, summary, say):
    say("提议的操作：" if ops else "提议的操作：无")
    for op in ops:
        say("  " + json.dumps(op, ensure_ascii=False))
    say("结果：")
    for line in render_numbered(lessons).splitlines():
        if line:
            say("  " + line)
    say(f"通知摘要：{summary or '（写手没给）'}")


def publish(ops, lessons, summary, day, today, hooks):
    write_lessons(lessons)
    add_log_bullet(f"Nightly reflection: {describe_changes(ops)}. The current list is in `LESSONS.md`.", today)
    hooks.git_publish([LESSONS_FILE, PUBLIC_LESSONS, LOG_FILE], f"chore(reflect): Day {day} lessons")
    hooks.notify(("今天学到：" + (summary or f"教训更新了，现在共 {len(lessons)} 条"))[:120])


# ---------- 主流程 ----------

def due(current, rstate, hooks):
    """每天一次：Day N 发完且过了 19:00，或者 21:00 以后不管 Day N 怎样。"""
    today = current.date()
    if rstate.get("done") == today.isoformat() or rstate.get("attempts", {}).get(today.isoformat(), 0) >= MAX_ATTEMPTS:
        return False
    if current.hour >= FALLBACK_HOUR:
        return True
    if current.hour < REFLECT_HOUR:
        return False
    day_n_path = QUEUE_DIR / f"day-{(today - DAY_ZERO).days:03d}.json"
    return day_n_path.exists() and hooks.fully_posted(day_n_path)


def run_reflection(live, state, hooks, force=False):
    """hooks: say, notify, notify_once(key, message), git_publish(paths, message), forbidden(), fully_posted(queue_path)。
    预览模式（不加 --live）照常评估、调写手、打印结果，但不写文件、不改状态。force=True 跳过时间窗口。
    返回新的教训列表；今天不该跑或这次没用上写手的结果时返回 None。"""
    rstate = state.setdefault("reflect", {}) if live else copy.deepcopy(state.get("reflect", {}))
    current = local_now()
    if not force and not due(current, rstate, hooks):
        return None
    today = current.date()
    day = (today - DAY_ZERO).days

    lessons, feedback = read_lessons(), read_feedback()[-FEEDBACK_SHOWN:]
    metrics_list = load_metrics(today, hooks.say)
    experiences = build_experiences(read_posted(), metrics_list)
    valid_ids = {row["queue_id"] for row in experiences["posts"]} | {event["id"] for event in feedback}
    vetter = OpVetter(lessons, valid_ids, hooks.forbidden(), allowed_numbers(metrics_list, experiences, feedback))

    attempts = rstate.get("attempts", {}).get(today.isoformat(), 0) + 1
    rstate["attempts"] = {today.isoformat(): attempts}  # 先记一次，中途崩了也算；只留今天的
    plan = usable_plan(ask_writer(day, today, experiences, feedback, lessons, hooks.say), vetter, hooks.say)
    if plan is None:
        if attempts >= MAX_ATTEMPTS:
            hooks.notify_once(f"reflect-gave-up:{today.isoformat()}", f"Day {day} 复盘试了 {MAX_ATTEMPTS} 次都不能用，今天不更新教训")
        return None

    new_lessons = update_lessons(lessons, plan["ops"], today)
    show_plan(plan["ops"], new_lessons, plan["summary"], hooks.say)
    if not live:
        return new_lessons
    rstate["done"] = today.isoformat()
    if new_lessons != lessons:
        publish(plan["ops"], new_lessons, plan["summary"], day, today, hooks)
    return new_lessons
