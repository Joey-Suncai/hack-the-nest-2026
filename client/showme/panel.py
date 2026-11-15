"""浮动面板（对话模式）：上面是步骤进度，中间是对话，底部输入框 + 麦克风 + 发送。

信号：
  message_submitted(str)      用户发了一句话（回车 / 发送按钮）
  mic_toggled()               点了麦克风
  input_focus_changed(bool)   输入框获得 / 失去焦点（控制器据此暂停画面检测）
  stop_requested / retry_requested
"""

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from showme import i18n
from showme.i18n import t
from showme.overlay import activate_app, apply_macos_window_settings

ACCENT = "#FF2D78"
PANEL_W, PANEL_H = 360, 600
BUBBLE_MAX_W = 268

STYLE = f"""
QWidget#panel {{ background: #1C1C1E; }}
QLabel {{ color: #E5E5EA; font-size: 13px; }}
QLabel#title {{ color: #FFFFFF; font-size: 16px; font-weight: 600; }}
QLabel#status {{ color: #8E8E93; font-size: 12px; }}
QLabel#steps {{ font-size: 13px; }}
QFrame#line {{ background: #3A3A3C; max-height: 1px; }}
QCheckBox {{ color: #E5E5EA; font-size: 13px; }}
QScrollArea, QWidget#chat {{ background: transparent; border: none; }}
QLabel#bubble_user {{ background: {ACCENT}; color: #FFFFFF; border-radius: 12px; padding: 8px 11px; }}
QLabel#bubble_bot {{ background: #2C2C2E; color: #F2F2F7; border-radius: 12px; padding: 8px 11px; }}
QLabel#bubble_step {{ background: #2C2C2E; color: #FFFFFF; border-left: 3px solid {ACCENT};
                      border-radius: 6px; padding: 8px 11px; font-weight: 600; }}
QLabel#bubble_error {{ background: #3A1F22; color: #FF8A8A; border-radius: 12px; padding: 8px 11px; }}
QLineEdit, QComboBox {{
  background: #2C2C2E; color: #FFFFFF; border: 1px solid #3A3A3C;
  border-radius: 8px; padding: 7px 9px; font-size: 13px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QPushButton {{
  background: #3A3A3C; color: #FFFFFF; border: none; border-radius: 8px;
  padding: 7px 12px; font-size: 13px;
}}
QPushButton:hover {{ background: #48484A; }}
QPushButton#send {{ background: {ACCENT}; font-weight: 600; }}
QPushButton#send:hover {{ background: #FF4D8D; }}
QPushButton#mic[recording="true"] {{ background: #FF3B30; }}
QPushButton#small {{ padding: 4px 8px; font-size: 12px; }}
QCheckBox#small {{ font-size: 12px; color: #C7C7CC; }}
QPushButton:disabled {{ color: #8E8E93; background: #2C2C2E; }}
"""


class _FocusWatcher(QObject):
    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.MouseButtonPress:
            activate_app()  # 辅助 App 不抢焦点：点输入框时主动激活，才能打字
        if ev.type() == QEvent.Type.FocusIn:
            self.on_change(True)
        elif ev.type() == QEvent.Type.FocusOut:
            self.on_change(False)
        return False


class Panel(QWidget):
    message_submitted = pyqtSignal(str)
    mic_toggled = pyqtSignal()
    input_focus_changed = pyqtSignal(bool)
    stop_requested = pyqtSignal()
    retry_requested = pyqtSignal()
    live_toggled = pyqtSignal(bool)  # 实时语音（ElevenLabs Agent）开 / 关
    filter_toggled = pyqtSignal(bool)  # 声纹过滤开 / 关
    tap_toggled = pyqtSignal(bool)  # 点按收音（点一下开始、再点一下结束）开 / 关
    enroll_requested = pyqtSignal(str)  # 录入谁的声音

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setWindowTitle("ShowMe")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setFixedSize(PANEL_W, PANEL_H)

        # ---- 顶部：标题 + 语音开关 + 语言 ----
        self.title = QLabel("ShowMe")
        self.title.setObjectName("title")
        self.voice_box = QCheckBox()
        self.voice_box.setChecked(True)
        self.lang_box = QComboBox()
        self.lang_box.addItem("English", "en")  # 第一项 = 默认语言
        self.lang_box.addItem("中文", "zh")
        self.lang_box.setCurrentIndex(self.lang_box.findData(i18n.DEFAULT_LANG))
        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.voice_box)
        top.addWidget(self.lang_box)

        # ---- 实时语音：开关 + 保底开关 + 录声纹 ----
        self.live_box = QCheckBox()
        self.filter_box = QCheckBox()
        self.tap_box = QCheckBox()
        for b in (self.live_box, self.filter_box, self.tap_box):
            b.setObjectName("small")
        self.live_box.setChecked(True)
        from showme.config import VOICE_FILTER_DEFAULT
        self.filter_box.setChecked(VOICE_FILTER_DEFAULT)
        from showme.config import TAP_TO_TALK_DEFAULT
        self.tap_box.setChecked(TAP_TO_TALK_DEFAULT)
        self.enroll_btn = QPushButton()
        self.enroll_btn.setObjectName("small")
        live_row = QHBoxLayout()
        live_row.addWidget(self.live_box)
        live_row.addWidget(self.filter_box)
        live_row.addWidget(self.tap_box)
        self.tap_box.hide()  # 现在固定是「按键说话」（右 Option / 麦克风按钮），不再需要这个开关
        live_row.addStretch()
        live_row.addWidget(self.enroll_btn)

        # ---- 步骤进度 ----
        self.steps_label = QLabel("")
        self.steps_label.setObjectName("steps")
        self.steps_label.setWordWrap(True)
        self.steps_label.setTextFormat(Qt.TextFormat.RichText)
        self.steps_label.hide()
        self.line = QFrame()
        self.line.setObjectName("line")
        self.line.hide()

        # ---- 对话 ----
        self.chat = QWidget()
        self.chat.setObjectName("chat")
        self.chat_layout = QVBoxLayout(self.chat)
        self.chat_layout.setContentsMargins(0, 4, 4, 4)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.chat)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)

        # ---- 输入 ----
        self.input = QLineEdit()
        self.mic_btn = QPushButton("🎤")
        self.mic_btn.setObjectName("mic")
        self.mic_btn.setFixedWidth(42)
        self.send_btn = QPushButton()
        self.send_btn.setObjectName("send")
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.mic_btn)
        row.addWidget(self.send_btn)

        self.stop_btn = QPushButton()
        self.retry_btn = QPushButton()
        self.stop_btn.setEnabled(False)
        self.retry_btn.setEnabled(False)
        ctl = QHBoxLayout()
        ctl.addWidget(self.stop_btn)
        ctl.addWidget(self.retry_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addLayout(live_row)
        lay.addWidget(self.steps_label)
        lay.addWidget(self.line)
        lay.addWidget(self.scroll, 1)
        lay.addWidget(self.status_label)
        lay.addLayout(row)
        lay.addLayout(ctl)

        self.input.returnPressed.connect(self._submit)
        self.send_btn.clicked.connect(self._submit)
        self.mic_btn.clicked.connect(self.mic_toggled)
        self.stop_btn.clicked.connect(self.stop_requested)
        self.retry_btn.clicked.connect(self.retry_requested)
        self._focus_watcher = _FocusWatcher(self.input_focus_changed.emit)
        self.input.installEventFilter(self._focus_watcher)
        self.live_box.toggled.connect(self.live_toggled)
        self.filter_box.toggled.connect(self.filter_toggled)
        self.tap_box.toggled.connect(self.tap_toggled)
        self.enroll_btn.clicked.connect(self._ask_enroll)
        self.lang_box.currentIndexChanged.connect(self._retranslate)
        self._retranslate()

        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 16, area.top() + 16)
        self.show()
        apply_macos_window_settings(self)

    # ---- 设置 ----
    def lang(self) -> str:
        return self.lang_box.currentData()

    def voice_enabled(self) -> bool:
        return self.voice_box.isChecked()

    def set_mock(self, mock: bool) -> None:
        self.title.setText("ShowMe · MOCK" if mock else "ShowMe")

    # ---- 对话 ----
    def add_message(self, role: str, text: str) -> None:
        """role: user / assistant / step / error"""
        if not text:
            return
        bubble = QLabel(text)
        bubble.setObjectName({"user": "bubble_user", "step": "bubble_step", "error": "bubble_error"}.get(role, "bubble_bot"))
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(BUBBLE_MAX_W)
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        if role == "user":
            wrap.addStretch()
            wrap.addWidget(bubble)
        else:
            wrap.addWidget(bubble)
            wrap.addStretch()
        self.chat_layout.addLayout(wrap)
        QTimer.singleShot(0, self._scroll_to_bottom)
        QTimer.singleShot(50, self._scroll_to_bottom)  # 换行后高度会变，再滚一次

    def messages(self) -> list[tuple[str, str]]:
        """对话里所有气泡 (role, text)，测试用。"""
        out = []
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            lay = item.layout() if item else None
            if not lay:
                continue
            for j in range(lay.count()):
                w = lay.itemAt(j).widget()
                if isinstance(w, QLabel):
                    out.append((w.objectName().removeprefix("bubble_"), w.text()))
        return out

    def add_step(self, n: int, instruction: str) -> None:
        self.add_message("step", t("step_msg", n=n, text=instruction))

    def set_error(self, msg: str) -> None:
        self.add_message("error", msg)
        self.retry_btn.setEnabled(True)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ---- 输入 ----
    def set_input(self, text: str) -> None:
        self.input.setText(text)

    def set_busy(self, busy: bool) -> None:
        """等 ShowMe 回复时不能再发；输入框还能打字。"""
        self.send_btn.setEnabled(not busy)
        self.mic_btn.setEnabled(not busy)

    def set_recording(self, recording: bool) -> None:
        self.mic_btn.setProperty("recording", "true" if recording else "false")
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)
        self.send_btn.setEnabled(not recording)
        self.input.setEnabled(not recording)
        self.set_status(t("listening") if recording else "")

    # ---- 实时语音 ----
    def set_live_state(self, on: bool) -> None:
        """连上 / 断开后同步开关（不再触发 live_toggled）。"""
        self.live_box.blockSignals(True)
        self.live_box.setChecked(on)
        self.live_box.blockSignals(False)
        self.mic_btn.setToolTip(t("talk_tip") if on else t("mic_tip"))
        self.stop_btn.setEnabled(on or self.stop_btn.isEnabled())

    def set_talking(self, on: bool) -> None:
        """点按收音模式下：正在收音时麦克风按钮变红。"""
        self.mic_btn.setProperty("recording", "true" if on else "false")
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)
        self.set_status(t("talk_on") if on else "")

    def set_enrolling(self, on: bool) -> None:
        self.enroll_btn.setEnabled(not on)
        self.live_box.setEnabled(not on)
        self.set_status(t("enrolling") if on else "")

    def _ask_enroll(self):
        name, ok = QInputDialog.getItem(self, t("enroll"), t("enroll_who"), ["Teresa", "Joey"], 0, True)
        if ok and name.strip():
            self.enroll_requested.emit(name.strip())

    # ---- 任务 ----
    def set_running(self, running: bool) -> None:
        # 实时语音模式下 Stop 任何时候都能点（Agent 可能在说话，即使没有任务）
        self.stop_btn.setEnabled(running or self.live_box.isChecked())
        if not running:
            self.retry_btn.setEnabled(False)

    def allow_retry(self, allow: bool = True) -> None:
        self.retry_btn.setEnabled(allow)

    def set_steps(self, done: list[str], current: str | None, upcoming: list[str]) -> None:
        """步骤列表：做完的打勾、当前这步高亮、后面的（来自大纲）灰色。"""
        rows = []
        n = 0
        for s in done:
            n += 1
            rows.append(f'<span style="color:#30D158">✓</span>&nbsp; <span style="color:#8E8E93">{n}. {_esc(s)}</span>')
        if current:
            n += 1
            rows.append(f'<span style="color:{ACCENT}">●</span>&nbsp; <b style="color:#FFFFFF">{n}. {_esc(current)}</b>')
        for s in upcoming:
            n += 1
            rows.append(f'<span style="color:#636366">○</span>&nbsp; <span style="color:#636366">{n}. {_esc(s)}</span>')
        self.steps_label.setText("<br>".join(rows))
        self.steps_label.setVisible(bool(rows))
        self.line.setVisible(bool(rows))

    # ---- 内部 ----
    def _submit(self):
        text = self.input.text().strip()
        if not text or not self.send_btn.isEnabled():
            return
        self.input.clear()
        self.message_submitted.emit(text)

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _retranslate(self, *_):
        i18n.set_lang(self.lang())
        self.input.setPlaceholderText(t("input_ph"))
        self.voice_box.setText(t("voice"))
        self.send_btn.setText(t("send"))
        self.stop_btn.setText(t("stop"))
        self.retry_btn.setText(t("retry"))
        self.mic_btn.setToolTip(t("mic_tip"))
        self.live_box.setText(t("live"))
        self.filter_box.setText(t("vfilter"))
        self.tap_box.setText(t("tap_talk"))
        self.enroll_btn.setText(t("enroll"))

    def mousePressEvent(self, e):
        activate_app()
        super().mousePressEvent(e)

    def closeEvent(self, e):
        QApplication.quit()
        super().closeEvent(e)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
