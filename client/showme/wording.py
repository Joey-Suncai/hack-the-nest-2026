"""开场白和结束语。想改说法直接改这里（会同时显示成字幕、读成语音）。"""


def intro(app: str, goal: str, lang: str = "zh", n_steps: int | None = None) -> str:
    if lang == "en":
        s = f"I'll walk you through it in {app}: {goal}."
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
