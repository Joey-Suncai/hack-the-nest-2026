"""界面文字（中 / 英）。面板右上角的下拉框决定当前语言，默认 English（demo 全英文：VS Code + 英文声音）。"""

DEFAULT_LANG = "en"

TEXT = {
    "en": {
        "input_ph": "What do you want to do? e.g. make a bar chart in Numbers",
        "send": "Send",
        "mic_tip": "Click to talk, click again to stop",
        "listening": "Listening… click the mic again when you're done",
        "transcribing": "Turning your voice into text…",
        "thinking": "Thinking…",
        "step_msg": "Step {n}: {text}",
        "step_fixed": "Step {n} (updated): {text}",
        "click_label": "Click “{label}”",
        "welcome": "Hi! Open the app you want to learn, then tell me what you want to do. You can ask me questions anytime.",
        "voice_unavailable": "Voice input isn't set up — please type instead.",
        "not_heard": "I didn't catch that — please say it again.",
        "mic_failed": "Can't use the microphone ({e}). Allow it in System Settings → Privacy & Security → Microphone, or type instead.",
        "voice": "Voice",
        "start": "Start",
        "stop": "Stop",
        "retry": "Retry",
        "need_goal": "Tell me what you want to do first",
        "connecting": "Connecting to {api} …",
        "connected": "Connected ({model})",
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
        "err_token": "Wrong access token — check SHOWME_TOKEN",
        "current_app": "the current app",
    },
    "zh": {
        "input_ph": "你想做什么？比如：在 Numbers 里做一个柱状图",
        "send": "发送",
        "mic_tip": "点一下开始说话，再点一下结束",
        "listening": "正在听…说完再点一下麦克风",
        "transcribing": "正在把语音转成文字…",
        "thinking": "正在想…",
        "step_msg": "第 {n} 步：{text}",
        "step_fixed": "第 {n} 步（更正）：{text}",
        "click_label": "点击「{label}」",
        "welcome": "你好！先打开你想学的软件，再告诉我你想做什么。中途随时可以问我。",
        "voice_unavailable": "语音输入没有配置，请直接打字。",
        "not_heard": "没听清，再说一次吧。",
        "mic_failed": "用不了麦克风（{e}）。请在 系统设置 → 隐私与安全性 → 麦克风 里允许，或者直接打字。",
        "voice": "语音",
        "start": "开始",
        "stop": "停止",
        "retry": "重试",
        "need_goal": "先写下你想做什么",
        "connecting": "正在连接后端 {api} …",
        "connected": "已连接后端（{model}）",
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
        "err_token": "口令不对，请检查 SHOWME_TOKEN",
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
