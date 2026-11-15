"""旁白：一句一句地「读出来 + 显示字幕」，字幕和语音同步。

say(text)            排到队尾（比如开场白之后接第 1 步）
say(text, interrupt) 打断正在读的，清空队列（换到新的一步时用）

语音是边下边放的（speech.py）：/api/speak 的 mp3 流到一点就解码、播放，不等整段下完。
每句话在自己的线程里取语音；stop() 之后，还在路上 / 晚到的语音一律丢掉（item.dead，在锁里检查）。
语音关掉 / 后端没配 ElevenLabs（204）/ 取语音失败 → 只显示字幕，按字数估算显示时长。
"""

import threading

from PyQt6.QtCore import QObject, QTimer

from showme import api, audio, speech, sync
from showme.config import CAPTION_HOLD_S
from showme.wording import speakable


def estimate_seconds(text: str) -> float:
    """没有语音时字幕显示多久：中文约 4 字 / 秒，英文约 15 字符 / 秒。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return max(2.5, cjk / 4 + other / 15 + 1.0)


class _Item:
    def __init__(self, text: str, tag: int | None = None):
        self.text, self.tag = text, tag  # tag：第几步（[sync] 计时用），不是步骤为 None
        self.done = False  # 取语音结束了（成功 / 失败 / 204）
        self.had_audio = False
        self.chunks: list = []  # 还没轮到它时先存着的 PCM
        self.live = False  # 轮到它了：新解出来的 PCM 直接送去播放
        self.dead = False  # 被打断：后面到的一律丢掉
        self.fallback_mp3: bytes | None = None  # 解码不了：整段交给 afplay
        self.fallback_started = False


class Narrator(QObject):
    def __init__(self, overlay, run_bg, voice_enabled, lang, player=None, stream=None):
        """run_bg 保留不用（旧接口）；voice_enabled() / lang()：从面板读设置。
        player：PcmPlayer（测试传假的）；stream(text, lang)：mp3 字节块的迭代器（默认 api.speak_stream）。"""
        super().__init__()
        self.overlay, self.voice_enabled, self.lang = overlay, voice_enabled, lang
        self._player = player
        self.stream = stream or api.speak_stream
        self.queue: list[_Item] = []
        self._cache: dict[tuple[str, str], list] = {}  # (文字, 语言) → 解好的 PCM：常用短句启动时先生成（prefetch）
        self.muted = False  # 旧开关：True 时什么都不念（现在实时语音模式下也由客户端念步骤，默认不用）
        self.current: _Item | None = None
        self._lock = threading.Lock()
        self.on_voice_start = None  # (tag) → 客户端开始出声（音频线程里）
        self.timer = QTimer(self)  # 没有语音时，字幕到点消失；有语音时读完再留一会儿
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._finish_current)
        self.watch = QTimer(self)  # 盯着取语音 / 播放有没有结束
        self.watch.setInterval(50)
        self.watch.timeout.connect(self._check_audio)

    @property
    def player(self):
        if self._player is None:
            self._player = speech.player()
        return self._player

    def speaking(self) -> bool:
        return self.current is not None or bool(self.queue)

    def prefetch(self, texts: list[str]) -> None:
        """后台把几句常用短句（"Let me look."）先用 /api/speak 生成、解码好，之后 say() 直接播，不等网络。"""
        lang = self.lang()

        def run():
            for text in texts:
                try:
                    data = b"".join(self.stream(speakable(text), lang))
                    pcm = speech.decode_mp3(data) if data else None
                    if pcm is not None and len(pcm[0]):
                        self._cache[(text, lang)] = [pcm]
                        print(f"[audio] 缓存好了：{text!r}（{len(pcm[0]) / pcm[1]:.1f} s）", flush=True)
                except Exception as e:  # noqa: BLE001  缓存不了就照常现取
                    print(f"[audio] 缓存 {text!r} 失败：{getattr(e, 'message', e)}", flush=True)

        threading.Thread(target=run, name="speak-cache", daemon=True).start()

    def cached(self, text: str) -> bool:
        return (text, self.lang()) in self._cache

    def say(self, text: str, interrupt: bool = False, tag: int | None = None) -> None:
        if not text or self.muted:
            return
        if interrupt:
            self.stop()
        item = _Item(text, tag)
        self.queue.append(item)
        pcm = self._cache.get((text, self.lang())) if self.voice_enabled() else None
        if pcm is not None:  # 缓存好的：不用网络，马上能播
            try:
                self.player.warmup()
            except Exception:  # noqa: BLE001
                pass
            self._deliver(item, pcm)
            item.done = True
        elif self.voice_enabled():
            lang, spoken = self.lang(), speakable(text)
            threading.Thread(target=self._fetch, args=(item, spoken, lang), name="speak", daemon=True).start()
        else:
            item.done = True
        self._advance()

    def stop(self) -> None:
        with self._lock:
            for it in self.queue + ([self.current] if self.current else []):
                it.dead, it.live = True, False
            self.queue.clear()
            self.current = None
            if self._player is not None:
                self._player.clear()
        self.timer.stop()
        self.watch.stop()
        audio.stop()
        self.overlay.clear_caption()

    # ---- 取语音（后台线程）----
    def _fetch(self, item: _Item, text: str, lang: str):
        dec = speech.StreamDecoder()
        it = None
        try:
            try:
                self.player.warmup()  # 打开输出流的几十毫秒藏在等网络的时间里
            except Exception as e:  # noqa: BLE001
                print(f"[audio] 打不开扬声器：{e}", flush=True)
            it = iter(self.stream(text, lang))
            for chunk in it:
                if item.dead:
                    break
                self._deliver(item, dec.feed(chunk))
            if not item.dead:
                self._deliver(item, dec.finish())
                if not dec.decoded_ok and dec.data:
                    item.fallback_mp3 = bytes(dec.data)
        except Exception as e:  # noqa: BLE001  语音拿不到也照样显示字幕
            msg = getattr(e, "message", e)
            if not item.dead:
                print(f"[audio] 取语音失败：{msg}", flush=True)
        finally:
            if it is not None and hasattr(it, "close"):
                try:
                    it.close()  # 被打断：关掉连接
                except Exception:  # noqa: BLE001
                    pass
            item.done = True

    def _deliver(self, item: _Item, pcm: list):
        if not pcm:
            return
        with self._lock:
            if item.dead:
                return
            for x, sr in pcm:
                item.had_audio = True
                if item.live:
                    self._push(item, x, sr)
                else:
                    item.chunks.append((x, sr))

    def _push(self, item: _Item, x, sr):
        """在锁里调用。这句话的第一段带上 on_start：真正出声那一刻记 [sync]。"""
        first = not getattr(item, "_pushed", False)
        item._pushed = True
        cb = None
        if first:
            def cb(item=item):
                if item.dead:
                    return
                if item.tag is not None:
                    sync.voice(item.tag, "client")
                if self.on_voice_start:
                    self.on_voice_start(item.tag)
        try:
            self.player.push(x, sr, cb)
        except Exception as e:  # noqa: BLE001
            print(f"[audio] 播放失败：{e}", flush=True)

    # ---- 主线程 ----
    def _advance(self):
        if self.current is not None or not self.queue:
            return
        item = self.queue.pop(0)
        self.current = item
        self.overlay.show_caption(item.text)
        with self._lock:
            item.live = True
            for x, sr in item.chunks:
                self._push(item, x, sr)
            item.chunks.clear()
        self.watch.start()

    def _check_audio(self):
        cur = self.current
        if cur is None:
            self.watch.stop()
            return
        if cur.fallback_mp3 and not cur.fallback_started:
            cur.fallback_started = True
            try:
                audio.play(cur.fallback_mp3)
                if cur.tag is not None:
                    sync.voice(cur.tag, "client(afplay)")
            except Exception as e:  # noqa: BLE001
                print(f"[audio] 播放失败：{e}", flush=True)
            return
        if not cur.done or self.player_busy():
            return
        if cur.fallback_started and audio.is_playing():
            return
        self.watch.stop()
        if cur.had_audio or (cur.fallback_started and not audio.failed()):
            self.timer.start(int(CAPTION_HOLD_S * 1000))  # 读完再留一小会儿
        else:  # 没放出声音：字幕按字数显示
            self.timer.start(int(estimate_seconds(cur.text) * 1000))

    def player_busy(self) -> bool:
        return self._player is not None and self._player.playing()

    def _finish_current(self):
        self.current = None
        if self.queue:
            self._advance()
        else:
            self.overlay.clear_caption()
