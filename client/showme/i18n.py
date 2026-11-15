"""界面文字（中 / 英）。面板右上角的下拉框决定当前语言，默认 English（demo 全英文：VS Code + 英文声音）。"""

DEFAULT_LANG = "en"

TEXT = {
    "en": {
        "app_ph": "App, e.g. VS Code",
        "goal_ph": "What do you want to do? e.g. clone a GitHub repo",
        "voice": "Voice",
        "start": "Start",
        "stop": "Stop",
        "retry": "Retry",
        "need_goal": "Tell me what you want to do first",
        "connecting": "Connecting to {api} …",
        "connected": "Connected ({model}). Type a goal and press Start",
        "looking": "Looking at your screen…",
        "do_step": "Do this step and the box will move on by itself",
        "done": "Done!",
        "stopped": "Stopped",
        "unsure": "I can't tell what to click next",
        "unsure_hint": "I'll try again as soon as the screen changes",
        "capture_failed": "Screen capture failed: {e}",
        "client_error": "Something went wrong in the app: {e}",
        "err_timeout": "The server took too long. Press Retry",
        "err_connect": "Can't reach the server. Run make backend first",
        "err_http": "Server error (HTTP {code})",
        "err_generic": "Something went wrong — please try again.",
        "current_app": "the current app",
    },
    "zh": {
        "app_ph": "软件，比如 VS Code",
        "goal_ph": "你想做什么，比如 从 GitHub clone 一个 repo",
        "voice": "语音",
        "start": "开始",
        "stop": "停止",
        "retry": "重试",
        "need_goal": "先写下你想做什么",
        "connecting": "正在连接后端 {api} …",
        "connected": "已连接后端（{model}）。写下目标，点「开始」",
        "looking": "正在看你的屏幕…",
        "do_step": "做完这一步，框会自动跳到下一步",
        "done": "完成！",
        "stopped": "已停止",
        "unsure": "看不出下一步该点哪里",
        "unsure_hint": "画面一变化我就自动再看一次",
        "capture_failed": "截屏失败：{e}",
        "client_error": "客户端出错了：{e}",
        "err_timeout": "后端响应超时，请点「重试」",
        "err_connect": "连不上后端，请先运行 make backend",
        "err_http": "后端出错了（HTTP {code}）",
        "err_generic": "出错了，请再试一次",
        "current_app": "当前软件",
    },
}

_lang = DEFAULT_LANG


def set_lang(lang: str) -> None:
    global _lang
    _lang = lang if lang in TEXT else DEFAULT_LANG


def get_lang() -> str:
    return _lang


def t(key: str, **kw) -> str:
    s = TEXT[_lang].get(key) or TEXT[DEFAULT_LANG][key]
    return s.format(**kw) if kw else s
