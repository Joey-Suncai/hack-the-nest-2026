"""/api/chat 的 action 判断评测：每条测试消息调用真实 Gemini，统计 action 准确率和平均耗时。
运行：cd backend && uv run python eval/eval_chat.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app import chat, gemini  # noqa: E402
from eval_boxes import CASES, to_jpeg_b64  # noqa: E402

URL = "https://github.com/octocat/Hello-World"
GOAL = f"Clone the GitHub repo {URL}"


def task(step: int) -> dict:
    """VS Code clone 任务进行到第 step 步时的 task（step 从 1 开始）。"""
    steps = [
        ("Click Clone Git Repository under Start on the Welcome page.", "Clone Git Repository...", "A URL box appears at the top"),
        ("Click the input box at the top of the window.", "Provide repository URL or pick a repository source.", "You can type a URL"),
        ("Paste the URL, then click Clone from URL.", "Clone from URL", "A folder picker opens"),
        ("Click the blue Select as Repository Destination button.", "Select as Repository Destination", "VS Code starts cloning"),
    ]
    hist = [{"instruction": s[0], "ok": True} for s in steps[: step - 1]]
    ins, label, expect = steps[step - 1]
    return {"app": "VS Code", "goal": GOAL, "outline": None, "history": hist,
            "current": {"instruction": ins, "target_label": label, "box": None, "expect": expect}}


# (截图, lang, message, task, 期望 action[, frontmost_app])
TESTS = [
    ("vscode_clone_1.jpg", "en", f"Help me download this repo: {URL}", None, "start", "Code"),
    ("settings_1.jpg", "en", "How do I switch to light mode?", None, "start", "System Settings"),
    ("vscode_clone_1.jpg", "en", f"I want to clone {URL} in VS Code", None, "start"),
    ("vscode_clone_1.jpg", "zh", f"帮我把这个仓库 clone 下来 {URL}", None, "start"),
    ("vscode_clone_1.jpg", "en", "help me", None, "clarify"),
    ("vscode_clone_1.jpg", "en", "What is VS Code?", None, "resume"),
    ("vscode_clone_1.jpg", "en", "What does clone mean?", task(1), "resume"),
    ("vscode_clone_2.jpg", "en", "Okay, done.", task(2), "advance"),
    ("vscode_clone_2.jpg", "en", "Can we skip this step?", task(2), "advance"),
    ("vscode_clone_4.jpg", "en", "Oops, I think I clicked the wrong thing.", task(3), "advance"),
    ("vscode_clone_1.jpg", "en", "I can't find that button.", task(1), "advance"),
    ("vscode_clone_3.jpg", "en", "Stop, I'll do this later.", task(3), "stop"),
    ("vscode_clone_1.jpg", "en", "Actually, I just want to open a folder instead.", task(1), "start"),
    ("vscode_clone_4.jpg", "zh", "为什么要选一个文件夹？", task(4), "resume"),
    ("vscode_clone_2.jpg", "en", "Thanks!", task(2), "resume"),
]


def main():
    if gemini.MOCK:
        sys.exit("GEMINI_MOCK=1 时评测没有意义，用 GEMINI_MOCK=0 运行")
    imgs: dict[str, str] = {}
    hits, times = 0, []
    for file, lang, msg, t, want, *rest in TESTS:
        front = rest[0] if rest else None
        if file not in imgs:
            imgs[file] = to_jpeg_b64(CASES / file)[0]
        body = chat.ChatIn(lang=lang, message=msg, screenshot=imgs[file], task=t, frontmost_app=front)
        t0 = time.perf_counter()
        try:
            out = chat.chat(body)
            got, detail = out.action, f"reply=「{out.reply}」"
            if out.action == "start":
                detail += f" app={out.app!r} goal={out.goal!r} outline={len(out.outline or [])}条"
            if out.note:
                detail += f" note=「{out.note}」"
        except gemini.GeminiError as e:
            got, detail = "error", e.message
        dt = time.perf_counter() - t0
        times.append(dt)
        hits += got == want
        mark = "✅" if got == want else "❌"
        print(f"{mark} [{lang}] {msg[:44]:<46} 期望 {want:<8} 得到 {got:<8} {dt:4.1f}s\n     {detail}")
    n = len(TESTS)
    print(f"\n模型 {gemini.MODEL}（thinking={chat.THINKING or 'default'}）：action 准确率 {hits}/{n} = {hits / n:.0%}，平均耗时 {sum(times) / n:.1f}s，最慢 {max(times):.1f}s")


if __name__ == "__main__":
    main()
