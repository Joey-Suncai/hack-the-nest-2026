"""浮动控制面板：输入软件和目标，开始 / 停止 / 重试，显示当前步骤和错误。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from showme.overlay import apply_macos_window_settings

STYLE = """
QWidget#panel { background: #1C1C1E; }
QLabel { color: #E5E5EA; font-size: 13px; }
QLabel#title { color: #FFFFFF; font-size: 16px; font-weight: 600; }
QLabel#step { color: #FFFFFF; font-size: 15px; }
QLabel#error { color: #FF6B6B; }
QLineEdit, QComboBox {
  background: #2C2C2E; color: #FFFFFF; border: 1px solid #3A3A3C;
  border-radius: 6px; padding: 6px 8px; font-size: 13px;
}
QLineEdit:focus { border-color: #FF2D78; }
QPushButton {
  background: #3A3A3C; color: #FFFFFF; border: none; border-radius: 6px;
  padding: 7px 12px; font-size: 13px;
}
QPushButton:hover { background: #48484A; }
QPushButton#start { background: #FF2D78; font-weight: 600; }
QPushButton#start:hover { background: #FF4D8D; }
QPushButton:disabled { color: #8E8E93; background: #2C2C2E; }
"""


class Panel(QWidget):
    start_requested = pyqtSignal(str, str)  # app, goal
    stop_requested = pyqtSignal()
    retry_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setWindowTitle("ShowMe")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(340)

        self.title = QLabel("ShowMe")
        self.title.setObjectName("title")
        self.app_edit = QLineEdit(placeholderText="软件，比如 Numbers")
        self.goal_edit = QLineEdit(placeholderText="你想做什么，比如 把这列数据做成柱状图")
        self.lang_box = QComboBox()
        self.lang_box.addItem("中文", "zh")
        self.lang_box.addItem("English", "en")

        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("start")
        self.stop_btn = QPushButton("停止")
        self.retry_btn = QPushButton("重试")
        self.stop_btn.setEnabled(False)
        self.retry_btn.setEnabled(False)

        self.step_label = QLabel("")
        self.step_label.setObjectName("step")
        self.step_label.setWordWrap(True)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.error_label = QLabel("")
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.lang_box)
        btns = QHBoxLayout()
        btns.addWidget(self.start_btn, 2)
        btns.addWidget(self.stop_btn, 1)
        btns.addWidget(self.retry_btn, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.app_edit)
        lay.addWidget(self.goal_edit)
        lay.addLayout(btns)
        lay.addWidget(self.step_label)
        lay.addWidget(self.status_label)
        lay.addWidget(self.error_label)

        self.start_btn.clicked.connect(self._on_start)
        self.goal_edit.returnPressed.connect(self._on_start)
        self.stop_btn.clicked.connect(self.stop_requested)
        self.retry_btn.clicked.connect(self.retry_requested)

        self.adjustSize()
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 16, area.top() + 16)
        self.show()
        apply_macos_window_settings(self)

    # ---- 对外接口 ----
    def lang(self) -> str:
        return self.lang_box.currentData()

    def set_mock(self, mock: bool) -> None:
        self.title.setText("ShowMe · MOCK" if mock else "ShowMe")

    def set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.app_edit.setEnabled(not running)
        self.goal_edit.setEnabled(not running)
        if not running:
            self.retry_btn.setEnabled(False)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.error_label.setText("")
        self._fit()

    def set_step(self, n: int, instruction: str) -> None:
        self.step_label.setText(f"第 {n} 步：{instruction}")
        self.error_label.setText("")
        self.retry_btn.setEnabled(False)
        self._fit()

    def set_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.retry_btn.setEnabled(True)
        self._fit()

    def allow_retry(self, allow: bool = True) -> None:
        self.retry_btn.setEnabled(allow)

    # ---- 内部 ----
    def _on_start(self):
        app, goal = self.app_edit.text().strip(), self.goal_edit.text().strip()
        if not goal:
            self.set_error("先写下你想做什么")
            return
        self.step_label.setText("")
        self.start_requested.emit(app or "当前软件", goal)

    def _fit(self):
        self.adjustSize()

    def closeEvent(self, e):
        QApplication.quit()
        super().closeEvent(e)
