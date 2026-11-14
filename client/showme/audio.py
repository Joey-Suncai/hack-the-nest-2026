"""播放 mp3：用系统自带的 afplay。同一时间只放一段。"""

import os
import subprocess
import tempfile

_proc: subprocess.Popen | None = None
_path: str | None = None


def _suffix(data: bytes) -> str:
    """afplay 按扩展名认格式，扩展名不对就放不出来，所以按文件头判断。"""
    if data[:4] == b"FORM":
        return ".aiff"
    if data[:4] == b"RIFF":
        return ".wav"
    if data[4:8] == b"ftyp":
        return ".m4a"
    return ".mp3"


def play(mp3: bytes) -> None:
    """先结束上一段，再播放这一段（不阻塞）。"""
    global _proc, _path
    stop()
    fd, _path = tempfile.mkstemp(suffix=_suffix(mp3))
    with os.fdopen(fd, "wb") as f:
        f.write(mp3)
    _proc = subprocess.Popen(["afplay", _path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_playing() -> bool:
    return _proc is not None and _proc.poll() is None


def failed() -> bool:
    """上一段是不是没放出来（afplay 出错退出）。"""
    return _proc is not None and _proc.poll() not in (None, 0)


def stop() -> None:
    global _proc, _path
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
    _proc = None
    if _path:
        try:
            os.unlink(_path)
        except OSError:
            pass
        _path = None
