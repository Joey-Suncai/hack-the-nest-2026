"""读 ShowMe 的运行日志，找出流程里最该优化的地方，输出一份 Markdown 报告。

数据来源：
- client/logs/<时间>/NN_next.json / NN_chat.json + 截图（SHOWME_LOG=1 才会存）
- 终端输出（make client 2>&1 | tee xxx.log）：[step] [speed] [sync] [timing] [agent] [voice] [detect] 这些打印
  只有终端日志里才有「完成触发 → 出框 → 语音」的计时；只有 JSON 的那几次只能看请求耗时和框

用法：
  uv run python tools/analyze_logs.py                       # 默认读 logs/ + logs/terminal_run.log + ~/Desktop/showme-run.log
  uv run python tools/analyze_logs.py --term a.log b.log --mark 01:52:40="框错了" --out ~/Desktop/showme-report.md

--mark HH:MM[:SS]=说明：跑的时候用户记下的问题时间，报告里会找出那个时间附近的步骤和日志。
报告旁边会建一个 <报告名>-files/ 目录，放画了框的截图（截图可能有隐私：不要提交，不要发群）。
"""

import argparse
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

# ---------- 终端日志 ----------

RE_DETECT_TIME = re.compile(r"^\[detect (\d\d:\d\d:\d\d)\]")
RE_DONE = re.compile(r"^\[step\] 完成（([a-z_]+)(?:/[^）]*)?）：(.*)$")
RE_BOX_MS = re.compile(r"^\[step\] (\S+) → 出框 (\d+) ms（(.*)）$")
RE_SHOWN = re.compile(r"^\[sync\] step (\d+) box shown t=([\d.]+) kind=(\w+) text=(.*)$")
RE_VOICE = re.compile(r"^\[sync\] (?:step (\d+) )?voice start t=([\d.]+) src=(\w+)(?: box→voice=\+([\d.]+)s)?")
RE_FIX = re.compile(r"^\[speed\] Gemini 更正：OCR 框 (\[[^\]]*\]) → Gemini 框 (\[[^\]]*\])")
RE_CONFIRM = re.compile(r"^\[speed\] Gemini 核对：和 OCR 框一致（Gemini 用了 (\d+) ms）")
RE_OCR_MISS = re.compile(r"^\[speed\] OCR 没找到 (.*)（(\d+) ms），等 Gemini")
RE_LOG = re.compile(r"^\[log\] (\d+)_(next|chat) ([\d.]+)s (\S+)")
RE_TIMING = re.compile(r"^\[timing\] 按结束键 → (?:转写 (\d+)ms → 回复文字 (\d+)ms → )?第一段声音 (\d+)ms（(?:转写 ([\d.]+|None)，回复 ([\d.]+|None)，)?tool：(.*)）")
RE_TOOL_MS = re.compile(r"^\[timing\] tool (\w+) (\d+)ms")
RE_USER = re.compile(r"^\[agent\] 🧑 转写：(.*)$")
RE_AGENT = re.compile(r"^\[agent\] 🤖 回复：(.*)$")
RE_TOOLCALL = re.compile(r"^\[agent\] 🛠 (\w+) \{")
RE_POLL = re.compile(r"poll diff=([\d.]+)")
STEP_WORDS = re.compile(r"\b(click|select|press|type|choose|open the|tap)\b", re.I)
WRONG_WORDS = re.compile(r"wrong|oops|not (that|this|the)|mistake|didn'?t work|错|不对", re.I)


def _q(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0] else s


@dataclass
class Step:
    file: str
    task: int
    n: int
    text: str
    kind: str  # ocr / gemini / correction / tool
    wall: str  # 大概的墙上时间（最近一行 [detect HH:MM:SS]）
    line: int
    trigger: str | None = None  # 上一步怎么完成的：click / typing / screen_change
    done_to_box_ms: int | None = None
    how: str | None = None  # OCR 秒出 / 等 Gemini
    box_to_voice_s: float | None = None
    voice_src: str | None = None
    corrected: tuple | None = None  # (OCR 框, Gemini 框)
    confirm_ms: int | None = None
    ocr_miss: str | None = None
    completed: str | None = None  # 这一步怎么完成的
    user_spoke_before_done: bool = False
    log_ids: list = field(default_factory=list)  # 这一步期间发出的 [log] NN
    ocr_then_status: str | None = None  # OCR 框出了，Gemini 却回 done / unsure


@dataclass
class Turn:
    file: str
    wall: str
    line: int
    user: str = ""
    transcript_ms: float | None = None
    first_audio_ms: int | None = None
    tools: str = ""
    reply: str = ""


@dataclass
class Term:
    path: Path
    session: str | None = None
    steps: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    events: list = field(default_factory=list)  # (line, wall, kind, text)
    tool_ms: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    logs: dict = field(default_factory=dict)  # NN -> (kind, seconds, status, line)


def parse_term(path: Path) -> Term:
    T = Term(path)
    T.lines = path.read_text(errors="replace").splitlines()
    wall, task, cur, pending = "?", 0, None, {}  # pending：上一步完成的触发，等下一步出框时填进去
    turn: Turn | None = None
    talking = False
    asked = False  # 上一句 Agent 回复之后用户说过话（回答问题时提到 click 是正常的）
    for i, raw in enumerate(T.lines, 1):
        ln = raw.strip()
        if m := RE_DETECT_TIME.match(ln):
            wall = m.group(1)
        if "[log] 每一轮对话" in ln:
            T.session = ln.rsplit("/", 1)[-1].strip()
        if m := RE_DONE.match(ln):
            if cur and not cur.completed:
                cur.completed = m.group(1)
            pending = {"trigger": m.group(1)}
            continue
        if m := RE_SHOWN.match(ln):
            n, kind, text = int(m.group(1)), m.group(3), _q(m.group(4))
            if cur and n == cur.n and kind == "correction":
                cur.kind = "correction"
                cur.text = text
                continue
            if n == 1 and kind == "tool":
                task += 1
                pending = {}
            cur = Step(path.name, task, n, text, kind, wall, i, trigger=pending.get("trigger"))
            pending = {}
            T.steps.append(cur)
            continue
        if m := RE_BOX_MS.match(ln):
            if cur and cur.done_to_box_ms is None and cur.kind != "tool":  # tool 出的框：客户端计时是上个任务留下的，不算
                cur.done_to_box_ms, cur.how = int(m.group(2)), m.group(3)
            continue
        if m := RE_VOICE.match(ln):
            if m.group(4) and cur and cur.box_to_voice_s is None:
                cur.box_to_voice_s, cur.voice_src = float(m.group(4)), m.group(3)
            if m.group(3) == "agent":
                T.events.append((i, wall, "agent_voice", ln))
            continue
        if m := RE_FIX.match(ln):
            if cur:
                cur.corrected = (json.loads(m.group(1)), json.loads(m.group(2)))
            continue
        if m := RE_CONFIRM.match(ln):
            if cur:
                cur.confirm_ms = int(m.group(1))
            continue
        if m := RE_OCR_MISS.match(ln):
            pending["ocr_miss"] = _q(m.group(1))
            continue
        if m := RE_LOG.match(ln):
            nn = int(m.group(1))
            T.logs[nn] = (m.group(2), float(m.group(3)), m.group(4), i)
            if cur:
                cur.log_ids.append(nn)
                if cur.kind == "ocr" and m.group(4) in ("done", "unsure") and cur.ocr_then_status is None and not cur.completed:
                    cur.ocr_then_status = m.group(4)
            continue
        if "[voice] 收音 开始" in ln:
            talking = True
            if cur and not cur.completed:
                cur.user_spoke_before_done = True
            turn = Turn(path.name, wall, i)
            T.turns.append(turn)
            continue
        if "[voice] 收音 结束" in ln:
            talking = False
            continue
        if m := RE_USER.match(ln):
            txt = _q(m.group(1))
            if txt and txt != "...":
                asked = True
                if turn and not turn.user:
                    turn.user = txt
                if WRONG_WORDS.search(txt):
                    T.events.append((i, wall, "user_wrong", txt))
            continue
        if m := RE_AGENT.match(ln):
            txt = _q(m.group(1))
            if turn and not turn.reply:
                turn.reply = txt
            if cur and not asked and STEP_WORDS.search(txt) and "still there" not in txt.lower():
                T.events.append((i, wall, "agent_says_step", txt))
            asked = False
            if "still there" in txt.lower():
                T.events.append((i, wall, "still_there", txt))
            continue
        if m := RE_TIMING.match(ln):
            if turn:
                tr = m.group(1) or m.group(4)
                turn.transcript_ms = float(tr) if tr and tr != "None" else None
                turn.first_audio_ms = int(m.group(3))
                turn.tools = m.group(6)
            continue
        if m := RE_TOOL_MS.match(ln):
            T.tool_ms.append((m.group(1), int(m.group(2))))
            continue
        for key, kind in (("agent audio dropped", "agent_dropped"), ("agent audio cleared", "agent_cleared"),
                          ("client step speech stopped", "client_stopped"), ("called show_next_step by itself", "agent_self_step"),
                          ("voice skipped (user is talking)", "voice_skipped"), ("点在框外", "click_outside"),
                          ("[agent] 连接断开", "disconnect"), ("退回打字模式", "fallback"), ("ERROR", "error"), ("Traceback", "error")):
            if key in ln:
                T.events.append((i, wall, kind, ln))
    return T


# ---------- JSON 记录 ----------

@dataclass
class Rec:
    session: str
    nn: int
    kind: str
    path: Path
    shot: Path
    seconds: float
    req: dict
    resp: dict
    mtime: datetime


def load_sessions(logs: Path) -> dict:
    out = {}
    for d in sorted(p for p in logs.iterdir() if p.is_dir() and re.match(r"\d{8}-\d{6}$", p.name)):
        recs = []
        for f in sorted(d.glob("[0-9][0-9]_*.json")):
            if " " in f.name:  # iCloud 复制出来的 "xx 2.json"
                continue
            try:
                d_ = json.loads(f.read_text())
            except Exception:  # noqa: BLE001
                continue
            nn, kind = f.stem.split("_", 1)
            recs.append(Rec(d.name, int(nn), kind, f, f.with_name(f"{f.stem}_screen.jpg"), d_.get("seconds", 0),
                            d_.get("request", {}), d_.get("response", {}), datetime.fromtimestamp(f.stat().st_mtime)))
        out[d.name] = recs
    return out


def draw(shot: Path, boxes: list, out: Path, title: str):
    """boxes：[(box[ymin,xmin,ymax,xmax] 0–1000, 颜色, 标签)]。截图是整屏，直接按图片尺寸换算。"""
    try:
        im = Image.open(shot).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    W, H = im.size
    g = ImageDraw.Draw(im)
    for box, color, label in boxes:
        if not box or len(box) != 4:
            continue
        y0, x0, y1, x1 = box
        r = [x0 * W / 1000, y0 * H / 1000, x1 * W / 1000, y1 * H / 1000]
        for k in range(4):
            g.rectangle([r[0] - k, r[1] - k, r[2] + k, r[3] + k], outline=color)
        g.text((r[0], max(0, r[1] - 14)), label, fill=color)
    g.rectangle([0, 0, W, 18], fill="black")
    g.text((4, 3), title[:200], fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=80)
    return out


# ---------- 报告 ----------

def stat(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "—"
    return f"平均 {statistics.mean(xs):.0f}，中位 {statistics.median(xs):.0f}，最慢 {max(xs):.0f}（n={len(xs)}）"


def ms(v):
    return "—" if v is None else f"{v:.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=str(ROOT / "logs"))
    ap.add_argument("--term", nargs="*", default=None)
    ap.add_argument("--out", default=str(Path.home() / "Desktop" / "showme-report.md"))
    ap.add_argument("--mark", nargs="*", default=[], help="HH:MM[:SS]=说明")
    a = ap.parse_args()

    logs, out = Path(a.logs), Path(a.out).expanduser()
    files = out.with_name(out.stem + "-files")
    terms_p = [Path(p).expanduser() for p in a.term] if a.term is not None else \
        [p for p in (logs / "terminal_run.log", Path.home() / "Desktop" / "showme-run.log") if p.exists()]
    sessions = load_sessions(logs)
    terms = [parse_term(p) for p in terms_p if p.exists()]
    steps = [s for T in terms for s in T.steps]
    turns = [t for T in terms for t in T.turns]
    R: list[str] = []
    w = R.append

    w(f"# ShowMe 日志分析报告\n\n生成时间 {datetime.now():%Y-%m-%d %H:%M}。数据：`{logs}`（{len(sessions)} 个目录），"
      f"终端日志 {', '.join(f'`{p}`' for p in terms_p) or '无'}。\n")
    w("> 截图里可能有私人窗口：这份报告和 `-files/` 里的图只发给 Joey（AirDrop），不要发群，不要提交。\n")

    # 1. 记录列表
    w("## 1. 有哪些记录\n")
    w("| 目录 | 时间 | next 请求 | chat 请求 | Gemini 平均 / 最慢 (s) | 结果 | 有终端日志 |")
    w("|---|---|---|---|---|---|---|")
    term_sessions = {T.session for T in terms}
    for name, recs in sessions.items():
        if not recs:
            w(f"| {name} | — | 0 | 0 | — | （空：启动后没发请求） | — |")
            continue
        nx = [r for r in recs if r.kind == "next"]
        secs = [r.seconds for r in recs]
        st = {}
        for r in nx:
            k = r.resp.get("status") or ("error" if r.resp.get("error") else "?")
            st[k] = st.get(k, 0) + 1
        w(f"| {name} | {recs[0].mtime:%H:%M}–{recs[-1].mtime:%H:%M} | {len(nx)} | {len(recs) - len(nx)} | "
          f"{statistics.mean(secs):.1f} / {max(secs):.1f} | {', '.join(f'{k} {v}' for k, v in st.items())} | "
          f"{'✅' if name in term_sessions else '—'} |")
    w("")

    # 2. 每一步时间线
    w("## 2. 每一步的时间线（只有终端日志的那几次）\n")
    w("「完成→出框」从检测到上一步完成开始算（点击的话含 0.4 s 等界面稳定）；「出框→出声」是客户端念这一步的第一段声音。\n")
    for T in terms:
        w(f"### {T.path.name}（目录 {T.session or '?'}）\n")
        w("| 任务 | 步 | 时间 | 上一步怎么完成 | 完成→出框 ms | 怎么出的框 | 出框→出声 s | 这一步怎么完成 | 指令 |")
        w("|---|---|---|---|---|---|---|---|---|")
        for s in T.steps:
            how = s.how or s.kind
            if s.corrected:
                how += "，**Gemini 更正了 OCR**"
            if s.ocr_then_status:
                how += f"，**OCR 框出了但 Gemini 说 {s.ocr_then_status}**"
            done = s.completed or "—"
            if s.user_spoke_before_done:
                done += "（**完成前用户开口了**）"
            v = "—" if s.box_to_voice_s is None else f"{s.box_to_voice_s:.2f}" + ("" if s.voice_src == "client" else f"（{s.voice_src}）")
            w(f"| {s.task} | {s.n} | {s.wall} | {s.trigger or ('（Agent 开始任务）' if s.kind == 'tool' else '—')} | "
              f"{ms(s.done_to_box_ms)} | {how} | {v} | {done} | {s.text[:70]} |")
        w("")

    # 3. 汇总
    w("## 3. 汇总\n")
    after = [s for s in steps if s.done_to_box_ms is not None]
    ocr = [s for s in after if (s.how or "").startswith("OCR")]
    gem = [s for s in after if not (s.how or "").startswith("OCR")]
    w(f"- 完成→出框（全部）：{stat([s.done_to_box_ms for s in after])} ms")
    w(f"  - OCR 秒出：{stat([s.done_to_box_ms for s in ocr])} ms")
    w(f"  - 等 Gemini：{stat([s.done_to_box_ms for s in gem])} ms")
    by_trig = {}
    for s in after:
        by_trig.setdefault(s.trigger or "?", []).append(s.done_to_box_ms)
    for k, v in by_trig.items():
        w(f"  - 触发 = {k}：{stat(v)} ms")
    bv = [s.box_to_voice_s * 1000 for s in steps if s.box_to_voice_s is not None and s.voice_src == "client"]
    w(f"- 出框→出声（客户端念）：{stat(bv)} ms；超过 500 ms 的 {sum(x > 500 for x in bv)} 次")
    w(f"- OCR 秒出比例：{len(ocr)}/{len(after)}" + (f"（{len(ocr) / len(after) * 100:.0f}%）" if after else ""))
    misses = sum("OCR 没找到" in ln for T in terms for ln in T.lines)
    w(f"- OCR 没找到 next_guess、只能等 Gemini：{misses} 次")
    w(f"- Gemini 更正 OCR 框：{sum(1 for s in steps if s.corrected)} 次；Gemini 确认 OCR 框：{sum(1 for s in steps if s.confirm_ms)} 次"
      f"（确认要 {stat([s.confirm_ms for s in steps if s.confirm_ms])} ms）")
    w(f"- OCR 框出了、Gemini 却说 done / unsure（念了一句错的步骤）：{sum(1 for s in steps if s.ocr_then_status)} 次")
    trig = {}
    for s in steps:
        if s.completed:
            trig[s.completed] = trig.get(s.completed, 0) + 1
    w(f"- 步骤完成方式：{', '.join(f'{k} {v}' for k, v in trig.items()) or '—'}")
    allsec = [r.seconds for recs in sessions.values() for r in recs if r.kind == "next"]
    w(f"- 所有 /api/next-step 请求耗时（JSON 里的 seconds，含上传截图）：{stat([x * 1000 for x in allsec])} ms")
    tms = [m for T in terms for m in T.tool_ms]
    for name in sorted({n for n, _ in tms}):
        w(f"- Agent tool `{name}`：{stat([d for n, d in tms if n == name])} ms")
    w("")

    # 4. 框错的步骤
    w("## 4. 可能框错的步骤\n")
    w("自动判断（不一定都错，看图确认）：① 下一次请求 Gemini 说 previous_ok=false；② 指令以 \"That didn't …\" 开头（Gemini 自己发现上一步没成）；"
      "③ 连续两步同一个目标；④ Gemini 更正了 OCR 框（红=OCR，绿=Gemini）；⑤ OCR 框出了但 Gemini 说 done/unsure；⑥ 用户说了 wrong / oops；⑦ --mark 标的时间。\n")
    shown = 0
    for name, recs in sessions.items():
        nx = [r for r in recs if r.kind == "next"]
        for j, r in enumerate(nx):
            why = []
            nxt = nx[j + 1] if j + 1 < len(nx) else None
            if nxt and nxt.resp.get("previous_ok") is False:
                why.append("下一次请求 previous_ok=false")
            ins = r.resp.get("instruction") or ""
            if re.match(r"that (didn'?t|did not|hasn'?t)", ins, re.I):
                why.append("Gemini 说上一步没成，重说一遍")
            if not why or not r.resp.get("box"):
                continue
            shown += 1
            img = draw(r.shot, [(r.resp.get("box"), "lime", r.resp.get("target_label") or "")],
                       files / f"{name}_{r.nn:02d}_box.jpg", f"{name} #{r.nn:02d} {ins}")
            w(f"- **{name} / {r.path.name}**（{r.mtime:%H:%M:%S}，{r.req.get('frontmost_app') or r.req.get('app')}，目标「{r.req.get('goal')}」）："
              f"{'；'.join(why)}\n  - 指令：{ins}\n  - 框 {r.resp.get('box')}，target_label={r.resp.get('target_label')!r}"
              f"\n  - 截图 `{r.shot.name}`" + (f" → 画框 `{img.relative_to(out.parent)}`" if img else ""))
    w("\n### 同一步说了好几遍（连续几次请求是同一个 target_label）\n")
    w("| 目录 | App | 目标 | 连着几次 | 第一次 → 最后一次 | 指令（第一次） |")
    w("|---|---|---|---|---|---|")
    runs = []
    for name, recs in sessions.items():
        nx = [r for r in recs if r.kind == "next" and r.resp.get("status") == "next"]
        j = 0
        while j < len(nx):
            k = j
            lab = (nx[j].resp.get("target_label") or "").lower()
            while k + 1 < len(nx) and lab and (nx[k + 1].resp.get("target_label") or "").lower() == lab:
                k += 1
            if k > j:
                runs.append((name, nx[j], nx[k], k - j + 1))
            j = k + 1
    for name, r0, r1, n in runs:
        w(f"| {name} | {r0.req.get('frontmost_app') or r0.req.get('app')} | {r0.resp.get('target_label')} | {n} | "
          f"#{r0.nn:02d} {r0.mtime:%H:%M:%S} → #{r1.nn:02d} {r1.mtime:%H:%M:%S} | {(r0.resp.get('instruction') or '')[:60]} |")
    if not runs:
        w("（没有）")
    w(f"\n合计 {len(runs)} 处，多出来的请求 {sum(n - 1 for *_, n in runs)} 次。\n")
    for T in terms:
        recs = {r.nn: r for r in sessions.get(T.session or "", [])}
        for s in T.steps:
            if not (s.corrected or s.ocr_then_status):
                continue
            rec = next((recs[n] for n in s.log_ids if n in recs and recs[n].kind == "next"), None)
            shown += 1
            if s.corrected:
                why = f"Gemini 更正：OCR {s.corrected[0]} → Gemini {s.corrected[1]}"
                boxes = [(s.corrected[0], "red", "OCR"), (s.corrected[1], "lime", "Gemini")]
            else:
                why = f"OCR 秒出的框「{s.text}」已经念了，Gemini 却说 {s.ocr_then_status}（next_guess 猜错）"
                boxes = []
            img = draw(rec.shot, boxes, files / f"{T.session}_{rec.nn:02d}_ocr.jpg", f"step {s.n}: {s.text}") if rec else None
            w(f"- **{T.session} 任务 {s.task} 第 {s.n} 步**（{s.wall}）：{why}\n  - 指令：{s.text}"
              + (f"\n  - 截图 `{rec.shot.name}` → 画框 `{img.relative_to(out.parent)}`" if img else ""))
    for T in terms:
        for i, wall, kind, txt in T.events:
            if kind == "user_wrong":
                shown += 1
                w(f"- 用户说「{txt}」（{T.path.name}:{i}，{wall}）——前一步可能框错 / 点错")
    if not shown:
        w("（没找到）")
    w("")

    if a.mark:
        w("### 用户跑的时候记下的时间\n")
        for mk in a.mark:
            hhmm, _, note = mk.partition("=")
            near = [s for s in steps if s.wall != "?" and s.wall[:len(hhmm)] <= hhmm] or steps[:1]
            s = near[-1] if near else None
            w(f"- {hhmm}「{note}」→ 最接近的步骤：" + (f"任务 {s.task} 第 {s.n} 步「{s.text}」（{s.how or s.kind}，"
              f"完成→出框 {ms(s.done_to_box_ms)} ms，{s.file}:{s.line}）" if s else "无"))
        w("")

    # 5. 没检测到完成
    w("## 5. 没检测到完成、用户开口了才继续的步骤\n")
    asked_mid = [s for s in steps if s.user_spoke_before_done and s.completed]
    w(f"（步骤进行中用户开口提问、之后照常检测到完成的有 {len(asked_mid)} 次，不算漏检，不列出。）\n")
    later = {id(a_) for T in terms for a_, b_ in zip(T.steps, T.steps[1:])}  # 后面还有别的步骤（用户换了做法 / 换了任务）
    stuck = [s for s in steps if not s.completed and not s.ocr_then_status and id(s) in later]
    if not stuck:
        w("（没有）")
    for s in stuck:
        T = next(T for T in terms if T.path.name == s.file)
        nxt = next((x for x in T.steps if x.line > s.line), None)
        end = nxt.line if nxt else len(T.lines)
        seg = T.lines[s.line - 1:end]
        diffs = [float(m.group(1)) for ln in seg if (m := RE_POLL.search(ln))]
        outside = [ln.strip() for ln in seg if "点在框外" in ln]
        talk = next((ln.strip() for ln in seg if RE_USER.match(ln.strip()) and "'...'" not in ln), "")
        w(f"- **任务 {s.task} 第 {s.n} 步「{s.text}」**（{s.wall}，{s.file}:{s.line}–{end}）完成方式：{s.completed or '一直没完成'}\n"
          f"  - 期间画面差最大 {max(diffs, default=0):.4f}（阈值 0.004），轮询 {len(diffs)} 次；框外点击 {len(outside)} 次"
          + (f"：{'; '.join(o.split('] ', 1)[-1] for o in outside[:3])}" if outside else "")
          + (f"\n  - 用户说：{talk.split('：', 1)[-1]}" if talk else ""))
    w("")

    # 6. Agent
    w("## 6. Agent\n")
    w("「说完」= 用户再按一下右 Option 的时刻。只有调用了 tool 的那几轮比较慢（tool 要截图 + 等 Gemini）。\n")
    w("| 时间 | 用户说 | 说完→转写 ms | 说完→第一段声音 ms | tool | Agent 回复 |")
    w("|---|---|---|---|---|---|")
    for t in turns:
        if t.user or t.first_audio_ms:
            w(f"| {t.wall} | {t.user[:50]} | {ms(t.transcript_ms)} | {ms(t.first_audio_ms)} | {t.tools} | {t.reply[:60]} |")
    w(f"\n- 说完→第一段声音：{stat([t.first_audio_ms for t in turns if t.tools == '无'])} ms（不调 tool）；"
      f"{stat([t.first_audio_ms for t in turns if t.tools and t.tools != '无'])} ms（调 tool）")
    ev = {}
    for T in terms:
        for _, _, k, _ in T.events:
            ev[k] = ev.get(k, 0) + 1
    names = {"agent_dropped": "客户端念步骤时丢掉的 Agent 话（防重叠生效）", "agent_cleared": "新步骤掐掉 Agent 正在说的",
             "client_stopped": "Agent 回答时停掉客户端在念的步骤", "agent_self_step": "Agent 没人说话就自己调 show_next_step",
             "agent_says_step": "Agent 回复里带「click / select …」（可能又念了一遍步骤）", "still_there": "Agent 说 \"Are you still there?\"",
             "voice_skipped": "用户正说话，新步骤没念",
             "click_outside": "点在框外", "disconnect": "Agent 断开", "fallback": "退回打字模式", "error": "报错"}
    for k, label in names.items():
        if ev.get(k):
            w(f"- {label}：{ev[k]} 次")
    says = [(T.path.name, i, txt) for T in terms for i, _, k, txt in T.events if k == "agent_says_step"]
    for f, i, txt in says[:8]:
        w(f"  - {f}:{i} 「{txt[:100]}」")
    w("- 声音重叠：日志只能看到「防重叠」动作有没有触发（上面几行），真的叠没叠要靠耳朵；听到叠的时间请用 --mark 标出来")
    w("")

    # 7. 前 5 个问题
    w("## 7. 对 demo 影响最大的问题（脚本按次数 × 影响估的，结论我核对后会写在聊天里）\n")
    cand = []
    slow = [s for s in gem if s.done_to_box_ms and s.done_to_box_ms > 2000]
    if slow:
        cand.append((len(slow) * 3, "点完 / 画面变了之后要等 2 s 以上才出下一个框",
                     f"{len(slow)} 次，等 Gemini 时 {stat([s.done_to_box_ms for s in gem])} ms；OCR 秒出只有 {len(ocr)}/{len(after)} 次",
                     "next_guess 没给 / 猜的是没字的图标 / 文字和屏幕上不完全一样，OCR 用不上，只能等 Gemini",
                     "后端：next_guess 尽量给屏幕上原样的文字标签；Gemini 换更快的模型或降截图分辨率。客户端：OCR 允许包含匹配（目前只认唯一完全一致）", "Joey + 我"))
    if tms:
        sl = [d for n, d in tms if n == "show_next_step"]
        if sl:
            cand.append((len(sl) * 2 + (3 if max(sl) > 6000 else 0), "开始任务时 Agent 调 show_next_step 很慢，用户干等",
                         f"{len(sl)} 次，{stat(sl)} ms", "第一步没 next_guess，只能等 Gemini（还有 /api/guide 大纲）",
                         "客户端：tool 一调用就念一句 \"Let me look at your screen.\"；后端：第一步请求提速", "我 + Joey"))
    bad = sum(1 for s in steps if s.ocr_then_status or s.corrected)
    if bad:
        cand.append((bad * 4, "OCR 秒出的框是错的（念了错的步骤，然后被更正或任务其实已经完成）", f"{bad} 次",
                     "next_guess 是 Gemini 上一轮预测的，界面实际变化和预测不同", "客户端：OCR 框先画不念，等 Gemini 确认再念；或者 done 的时候撤回", "我"))
    if stuck:
        cand.append((len(stuck) * 4, "没检测到步骤完成，用户只能开口说", f"{len(stuck)} 次（见第 5 节）",
                     "点在框外（框偏了）/ 变化太小 / 键盘操作", "看第 5 节的 diff 和框外点击坐标决定：框外点击多就放宽 CLICK_EXTRA_PAD，diff 小就降阈值", "我"))
    if ev.get("still_there"):
        cand.append((ev["still_there"] * 2, "Agent 没人说话也开口（\"Are you still there?\"）",
                     f"{ev['still_there']} 次（每次前面都有一条空转写 '...'：是 Agent 的静默提醒）",
                     "Agent 的静默超时提醒；按键说话期间不发声音，Agent 以为用户走了",
                     "后端：Agent 设置里关掉 / 调长 turn timeout 的提醒；客户端已经在步骤期间丢掉它的声音", "Joey"))
    if runs:
        cand.append((sum(n - 1 for *_, n in runs) * 2, "同一步说了好几遍（用户已经点了 / 画面在动，ShowMe 又让点同一个）",
                     f"{len(runs)} 处，多出 {sum(n - 1 for *_, n in runs)} 次请求（见第 4 节表格）",
                     "画面一变就重新截图：截到加载中 / 动画 / 视频预览，Gemini 以为还没点；或者框偏了用户点不中",
                     "客户端：画面变化后等画面稳定再截图（连续几帧不变，最多等 2 s）；视频区域多的 App 只认点击。后端：history 里同一步出现过就提示 Gemini 用户可能已经点了", "我 + Joey"))
    if says:
        cand.append((len(says) * 3, "Agent 自己又念步骤", f"{len(says)} 次", "prompt 没说清楚步骤由客户端念", "后端：Agent prompt 写明不要复述 SHOWN 后的步骤", "Joey"))
    ag = [t.first_audio_ms for t in turns if t.tools and t.tools != "无" and t.first_audio_ms]
    if ag:
        cand.append((len(ag) * 2, "问问题（look_at_screen）后要等很久才回答", f"{len(ag)} 次，{stat(ag)} ms",
                     "look_at_screen 要截图 + Gemini chat", "客户端：调 tool 时先念 \"Let me look.\"；后端：chat 提速", "我 + Joey"))
    cand.sort(key=lambda c: -c[0])
    for k, (score, what, n, cause, fix, who) in enumerate(cand[:5], 1):
        w(f"### {k}. {what}\n- 出现：{n}\n- 证据：见上面第 2–6 节\n- 怀疑原因：{cause}\n- 建议：{fix}\n- 谁的事：{who}\n")
    out.write_text("\n".join(R))
    print(f"报告：{out}\n画框截图：{files}")


if __name__ == "__main__":
    main()
