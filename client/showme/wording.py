"""开场白、结束语，以及朗读前的文字处理。想改说法直接改这里。"""

import re

_URL = re.compile(r"https?://\S+")


def speakable(text: str) -> str:
    """朗读用：网址读出来又长又难听，换成最后一段（https://github.com/octocat/Hello-World → Hello-World）。"""

    def short(m):
        url = m.group(0).rstrip(".,;:!?)」』”'\"")
        parts = [p for p in url.split("/")[2:] if p]
        tail = m.group(0)[len(url):]
        return (parts[-1] if len(parts) > 1 else parts[0]) + tail if parts else m.group(0)

    return _URL.sub(short, text)


def intro(app: str, goal: str, lang: str = "zh", n_steps: int | None = None) -> str:
    if lang == "en":
        g = goal.strip().rstrip(".!")
        if len(g) > 1 and g[1].islower():
            g = g[0].lower() + g[1:]  # 「Clone the …」接在冒号后面改成小写
        s = f"I'll walk you through it in {app}: {g}."
        if n_steps:
            s += f" About {n_steps} steps."
        return s + " Just follow the glowing box."
    s = f"我来带你在 {app} 里{_zh_goal(goal)}。"
    if n_steps:
        s += f"一共大约 {n_steps} 步，"
    return s + "跟着屏幕上的光圈点就行。"


def outro(app: str, goal: str, n_done: int, lang: str = "zh", done_text: str | None = None) -> str:
    if lang == "en":
        head = done_text or "All done!"
        return f"{head} You finished it in {n_done} steps. Next time you can do this in {app} on your own."
    head = done_text or "完成！"
    return f"{head}你一共做了 {n_done} 步。下次在 {app} 里{_zh_goal(goal)}，你自己就会了。"


def _zh_goal(goal: str) -> str:
    """「把数据做成柱状图」直接接在「在 X 里」后面；去掉结尾的句号。"""
    return goal.strip().rstrip("。.!！")
