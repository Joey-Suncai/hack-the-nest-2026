"""旁白：一句一句地「读出来 + 显示字幕」，字幕和语音同步。

say(text)            排到队尾（比如开场白之后接第 1 步）
say(text, interrupt) 打断正在读的，清空队列（换到新的一步时用）

语音关掉 / 后端没配 ElevenLabs（204）/ 取语音失败 → 只显示字幕，按字数估算显示时长。
"""

from PyQt6.QtCore import QObject, QTimer

from showme import api, audio
from showme.config import CAPTION_HOLD_S


def estimate_seconds(text: str) -> float:
    """没有语音时字幕显示多久：中文约 4 字 / 秒，英文约 15 字符 / 秒。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return max(2.5, cjk / 4 + other / 15 + 1.0)


class _Item:
    def __init__(self, text: str):
        self.text = text
        self.ready = False
        self.mp3: bytes | None = None


class Narrator(QObject):
    def __init__(self, overlay, run_bg, voice_enabled, lang):
        """run_bg(fn, on_ok, on_err)：后台执行；voice_enabled() / lang()：从面板读设置。"""
        super().__init__()
        self.overlay, self.run_bg, self.voice_enabled, self.lang = overlay, run_bg, voice_enabled, lang
        self.queue: list[_Item] = []
        self.current: _Item | None = None
        self._gen = 0
        self.timer = QTimer(self)  # 没有语音时，字幕到点消失
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._finish_current)
        self.watch = QTimer(self)  # 有语音时，盯着 afplay 播完
        self.watch.setInterval(100)
        self.watch.timeout.connect(self._check_audio)

    def say(self, text: str, interrupt: bool = False) -> None:
        if not text:
            return
        if interrupt:
            self.stop()
        item = _Item(text)
        self.queue.append(item)
        gen = self._gen
        if self.voice_enabled():
            lang = self.lang()

            def got(mp3):
                if gen == self._gen:
                    item.mp3, item.ready = mp3, True
                    self._advance()

            def failed(_msg):
                if gen == self._gen:
                    item.ready = True  # 语音拿不到也照样显示字幕
                    self._advance()

            self.run_bg(lambda: api.speak(text, lang), got, failed)
        else:
            item.ready = True
        self._advance()

    def stop(self) -> None:
        self._gen += 1
        self.queue.clear()
        self.current = None
        self.timer.stop()
        self.watch.stop()
        audio.stop()
        self.overlay.clear_caption()

    # ---- 内部 ----
    def _advance(self):
        if self.current is not None or not self.queue or not self.queue[0].ready:
            return
        self.current = self.queue.pop(0)
        self.overlay.show_caption(self.current.text)
        if self.current.mp3:
            try:
                audio.play(self.current.mp3)
                self.watch.start()
                return
            except Exception as e:  # noqa: BLE001  放不出声音就只显示字幕
                print(f"[audio] 播放失败：{e}")
        self.timer.start(int(estimate_seconds(self.current.text) * 1000))

    def _check_audio(self):
        if not audio.is_playing():
            self.watch.stop()
            if audio.failed() and self.current is not None:  # 没放出声音：字幕按字数显示
                print("[audio] afplay 播放失败，只显示字幕")
                self.timer.start(int(estimate_seconds(self.current.text) * 1000))
            else:
                self.timer.start(int(CAPTION_HOLD_S * 1000))  # 读完再留一小会儿

    def _finish_current(self):
        self.current = None
        if self.queue:
            self._advance()
        else:
            self.overlay.clear_caption()
