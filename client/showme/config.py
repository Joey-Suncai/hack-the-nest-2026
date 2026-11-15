"""客户端配置。光圈样式都在下面「高亮样式」一节，改完重启客户端就生效。"""

import os

# ---- Demo 示例 ----
DEMO_APP = "VS Code"
DEMO_GOAL = "Clone the GitHub repo https://github.com/octocat/Hello-World"

# ---- 实时语音的麦克风过滤（voice_filter.py）----
VAD_THRESHOLD = 0.5  # Silero 人声概率超过多少算「有人在说话」
VOICE_THRESHOLD = 0.6  # 声纹余弦相似度超过多少算「是我们」。安静处录的声纹：本人 0.61–0.77（中位 0.72），外放回声连 VAD 都过不了；嘈杂处旁边人声曾到 0.58（旧声纹）→ 现场要再测
VOICE_CONFIRM_S = 0.5  # 累计多少秒人声后做一次声纹判断（这段时间先压低 ShowMe 的音量）
VOICE_END_S = 0.6  # 连续多少秒没人声算这段话结束
DUCK_VOLUME = 0.3  # 检测到人声时 ShowMe 播放音量压到多少
PTT_KEY = "space"  # 「按住说话」用的键
TAP_TO_TALK_DEFAULT = True
VOICE_FILTER_DEFAULT = False  # 按键说话模式下不做 VAD / 声纹；开关保留，默认关（实测陌生人 0.67 vs 我们 0.61，分不开）
TALK_MAX_S = 30  # 忘了按结束，最长收音 30 秒
# 实时语音用不用 macOS 语音处理（回声消除）。按键说话时 Agent 的声音在用户说话期间是静音的，没有回声，不需要；
# 而且联调发现：从「终端」启动时 AVAudioEngine 语音处理的麦克风一直没有数据（sounddevice 正常）
USE_AEC = False  # 默认「点按收音」：现场太吵，免提时 VAD / 声纹连我们自己的声音都挡（联调实测）

# ---- 语音输入 ----
MIC_RATE = 16000  # /api/transcribe 要 16 kHz 单声道 WAV
MIC_MAX_S = 30  # 最长录多久，到点自动结束

# ---- 调试记录：SHOWME_LOG=1 时，每一步的截图和后端返回存到 client/logs/<时间>/ ----
LOG = os.getenv("SHOWME_LOG") == "1"
DEBUG_DETECT = os.getenv("SHOWME_DEBUG_DETECT") == "1"  # 画面变化检测的详细日志（排查「点了检测不到」）

# ---- 后端 ----
API_BASE = os.getenv("SHOWME_API", "http://localhost:8000")
# 后端口令：每个请求（/api/health 除外）带请求头 X-ShowMe-Token。口令只从环境变量读，绝不写进代码
API_TOKEN = os.getenv("SHOWME_TOKEN") or None

# ---- Accessibility 控件：每次 /api/next-step 带上目标 App 能点的控件和精确框（ax_tree.py），SHOWME_AX=0 关掉 ----
AX_ELEMENTS = os.getenv("SHOWME_AX", "1") == "1"

# ---- 截图 ----
MAX_SIDE = 1280  # 发给后端的截图长边
JPEG_QUALITY = 70

# ---- 画面变化检测 ----
POLL_S = 0.15  # 等用户操作时，多久截一次图（0.15 s：点完按钮到出框要在 1 秒内）
PIXEL_DELTA = 0.08  # 64x64 灰度图里，一个像素亮度变化超过这个值才算「这个像素变了」
CHANGE_THRESHOLD = 0.004  # 变了的像素超过这个比例（约 16 / 4096）算「画面变了」；漏检就调小，误判就调大
STABLE_FRAMES = 1  # 变化后连续几帧不动，才算用户这一步做完了

# ---- 高亮样式（光圈）----
HIGHLIGHT_PAD = 12  # 框比目标元素四周大多少（逻辑像素）
CLICK_EXTRA_PAD = 8  # 判断「点在框里」时，画出来的框四周再放宽多少
# 这一步做完了（点击 / 画面变化 / 打字）→ 先等画面稳定再截图发给后端：每 SETTLE_POLL_S 截一张缩略图，
# 先等画面和做之前比变了，再连续 2 张 diff ≤ CHANGE_THRESHOLD 才截；至少等 SETTLE_MIN_S；画面一直没变最多等 SETTLE_NOCHANGE_MAX_S，开始变之后最多再等 SETTLE_MAX_S
SETTLE_POLL_S = 0.1
SETTLE_MIN_S = 0.3
SETTLE_MAX_S = 1.5  # 画面开始变之后最多再等多久（动画 / 视频一直动也照截）
# 点完 / 打完画面还一点没变：最多等这么久再截（Zoom「安排会议」窗口 >1.5 s 才弹出，太早截到旧画面 Gemini 会让再点一次）
SETTLE_NOCHANGE_MAX_S = 5.0
# 点在框里（或打完字再点别处 / 按 Tab）= 客户端确认做完了：画面没变最多只等这么久（不等 5 秒），请求里带 confirmed
CLICK_SETTLE_MAX_S = 0.8
# 复查：用户做完一步后这么多秒，固定再截一张缩略图，和发给 Gemini 的那张比。变了（软件响应得慢，
# 第一张截早了）→ 用最新画面重新问一次。期间用户又点 / 按键 / 说话就不复查。每步最多一次
RECHECK_AFTER_S = 1.5
RECHECK_MIN_GAP_S = 0.4  # 正式截图之后至少再过这么久才复查
# 复查判断「变没变」比轮询灵敏：深色模式里弹窗开 / 关的亮度差很小（日志 diff=0.0039 < 0.004 被当成没变）
RECHECK_PIXEL_DELTA = 0.03  # 一个像素亮度变化超过这个值就算变了（轮询用 PIXEL_DELTA 0.08）
RECHECK_CHANGE = 0.003  # 变了的像素超过这个比例 = 画面变了
RECHECK_AX_MIN = 3  # 或者：控件清单（角色 + 名字）至少这么多个不同……
RECHECK_AX_RATIO = 0.05  # ……而且占总数这么多以上
# 关键指标检查（keycheck.py，对所有 App 通用）：框显示着（WAITING）时每 KEYCHECK_INTERVAL_S 秒读一次这一步的 check；
# 用户点 / 按键后从那一刻重新计时（第一次在操作后 1.5 s）。成立 = 操作成功 → 这一步完成；不成立 = 还没生效 → 框留着
KEYCHECK_INTERVAL_S = 1.5
NEAR_CLICK_PT = 40  # 点在框外但离框不超过这么多逻辑点：也算完成（框画偏了；点错了下一步 Gemini 会纠正）
HIGHLIGHT_COLOR = "#FF2D78"  # 框和光晕的颜色
BORDER_WIDTH = 4  # 实线边框粗细
CORNER_RADIUS = 10  # 圆角
GLOW_SIZE = 16  # 光晕向外扩散多宽
BREATHE_MS = 1400  # 呼吸动画一个周期多少毫秒；0 = 不呼吸
BUBBLE_FONT_SIZE = 15  # 提示气泡字号
BUBBLE_BG = "#1C1C1E"  # 提示气泡背景
BUBBLE_FG = "#FFFFFF"  # 提示气泡文字

# ---- 字幕（屏幕底部居中）----
CAPTIONS = False  # 先不用字幕，只用 ElevenLabs 语音；改成 True 就重新显示
CAPTION_FONT_SIZE = 22
CAPTION_BOTTOM = 72  # 离屏幕底部多远
CAPTION_MAX_W = 0.6  # 最宽占屏幕宽度的比例
CAPTION_HOLD_S = 0.8  # 语音读完后字幕再停留多久

# 截图前隐藏高亮层后等多久（C0 实测 0.05 秒够）
HIDE_WAIT_S = 0.05
