import subprocess
import tempfile
import wave

import numpy as np
import pytest

from showme import voice_filter as vf
from showme.voice_filter import ACCEPTED, CANDIDATE, IDLE, REJECTED, Gate

F = np.zeros(vf.FRAME, np.float32)


def run(gate, probs, speaking=True):
    return [gate.feed(F, p, speaking) for p in probs]


def test_no_voiceprint_accepts_after_confirm_and_flushes_buffer():
    g = Gate(voiceprint=None, confirm_s=0.1, end_s=0.1, log=lambda *a: None)  # 0.1 s ≈ 3 帧
    outs = run(g, [0.9, 0.9, 0.9])
    assert outs[0].duck is True and outs[0].send == []  # 先压低音量、不发
    assert outs[2].decision == "accept" and len(outs[2].send) == 3 and outs[2].duck is False and outs[2].stop_playback
    assert g.state == ACCEPTED
    assert len(g.feed(F, 0.9, True).send) == 1  # 之后的声音直接发


def test_voiceprint_reject_restores_volume_and_drops_until_speech_ends():
    g = Gate(scorer=lambda a: np.array([0.0, 1.0]), voiceprint=np.array([1.0, 0.0]), threshold=0.5,
             confirm_s=0.1, end_s=0.1, log=lambda *a: None)
    outs = run(g, [0.9, 0.9, 0.9])
    assert outs[2].decision == "reject" and outs[2].send == [] and outs[2].duck is False and outs[2].score == pytest.approx(0.0)
    assert g.state == REJECTED
    assert all(o.send == [] for o in run(g, [0.9, 0.9]))  # 这段话结束前都不发
    run(g, [0.0, 0.0, 0.0])
    assert g.state == IDLE


def test_voiceprint_accept():
    g = Gate(scorer=lambda a: np.array([1.0, 0.1]), voiceprint=np.array([1.0, 0.0]), threshold=0.5,
             confirm_s=0.1, end_s=0.1, log=lambda *a: None)
    assert run(g, [0.9, 0.9, 0.9])[2].decision == "accept"


def test_short_noise_is_dropped_and_volume_restored():
    g = Gate(voiceprint=None, confirm_s=0.3, end_s=0.1, log=lambda *a: None)
    outs = run(g, [0.9, 0.0, 0.0, 0.0])
    assert outs[0].duck is True and any(o.duck is False for o in outs[1:])
    assert all(o.send == [] for o in outs) and g.state == IDLE


def test_no_duck_when_showme_silent():
    g = Gate(voiceprint=None, confirm_s=0.1, log=lambda *a: None)
    assert g.feed(F, 0.9, False).duck is None and g.state == CANDIDATE


def test_filter_off_skips_voiceprint():
    g = Gate(scorer=lambda a: np.array([0.0, 1.0]), voiceprint=np.array([1.0, 0.0]), confirm_s=0.1, log=lambda *a: None)
    g.filter_enabled = False
    assert run(g, [0.9, 0.9, 0.9])[2].decision == "accept"


def test_push_to_talk_only_while_key_down():
    g = Gate(voiceprint=None, log=lambda *a: None)
    g.push_to_talk = True
    assert g.feed(F, 0.9, True).send == []
    g.key_down = True
    assert len(g.feed(F, 0.0, True).send) == 1  # 按着键就发，不看 VAD


# ---- 真模型（用 macOS say 生成的语音）----
needs_models = pytest.mark.skipif(not (vf.VAD_MODEL.exists() and vf.SPEAKER_MODEL.exists()), reason="没下载模型")


def say(text, voice):
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        subprocess.run(["say", "-v", voice, "-o", f.name, "--data-format=LEI16@16000", text], check=True)
        with wave.open(f.name) as w:
            return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


@needs_models
def test_real_vad_speech_vs_silence():
    vad = vf.SileroVAD()
    speech = say("Hello, please help me clone this repository from GitHub.", "Samantha")
    frames = [speech[i:i + vf.FRAME] for i in range(0, len(speech) - vf.FRAME, vf.FRAME)]
    sp = np.mean([vad.prob(f) > 0.5 for f in frames])
    vad.reset()
    quiet = np.mean([vad.prob(np.random.default_rng(0).normal(0, 0.003, vf.FRAME).astype(np.float32)) > 0.5 for _ in range(40)])
    assert sp > 0.5 and quiet < 0.1, (sp, quiet)


@needs_models
def test_real_speaker_same_voice_scores_higher_than_other_voice():
    m = vf.SpeakerModel()
    a1 = m.embed(say("Help me clone this repository from GitHub please.", "Samantha"))
    a2 = m.embed(say("What does the clone button do in this window?", "Samantha"))
    b = m.embed(say("What does the clone button do in this window?", "Daniel"))
    same, other = vf.cosine(a1, a2), vf.cosine(a1, b)
    print(f"同一个声音 {same:.3f}，不同声音 {other:.3f}")
    assert same > other + 0.1, (same, other)


def test_voiceprint_save_load(tmp_path):
    p = tmp_path / "vp.npy"
    v = vf.average_voiceprint([np.array([1.0, 0.0]), np.array([0.0, 1.0])])
    vf.save_voiceprint(v, p)
    assert np.allclose(vf.load_voiceprint(p), v) and abs(np.linalg.norm(v) - 1) < 1e-6
    assert vf.load_voiceprint(tmp_path / "missing.npy") is None


def test_resample_48k_to_16k():
    from showme.audio_engine import resample_to_16k

    x = np.sin(np.linspace(0, 2 * np.pi * 10, 4800)).astype(np.float32)
    y = resample_to_16k(x, 48000)
    assert len(y) == 1600 and np.max(np.abs(y)) > 0.9
    assert len(resample_to_16k(np.zeros(441, np.float32), 44100)) == 160
