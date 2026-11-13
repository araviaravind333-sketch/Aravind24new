"""
Reel Video Renderer -- Tests
=============================
Real ffmpeg, real (tiny, synthetic) image and audio files -- no mocks --
since ffmpeg is a preinstalled dependency of every workflow that calls
this module anyway, and the only thing worth verifying here is the
actual muxed output's duration and audio presence.

    python -m tests.test_video
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from src import video

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def _make_image(path, w=1080, h=1350):
    Image.new("RGB", (w, h), (40, 80, 160)).save(path)


def _make_tone(path, seconds):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-c:a", "pcm_s16le", path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)


def _has_audio_stream(path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
           "-of", "csv=p=0", path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return "audio" in result.stdout


def render_reel_tests(tmp):
    print("\nRENDER_REEL (still card, optionally narrated)")
    from config import settings
    card = os.path.join(tmp, "card.jpg")
    _make_image(card)

    out_silent = os.path.join(tmp, "silent.mp4")
    video.render_reel(card, out_silent)
    check("no audio_path -> old behavior, exactly REEL_DURATION_SEC, silent",
          abs(video.probe_duration(out_silent) - settings.REEL_DURATION_SEC) < 0.2,
          video.probe_duration(out_silent))
    check("the silent Reel genuinely has no audio stream",
          not _has_audio_stream(out_silent))

    short_tone = os.path.join(tmp, "short.wav")
    _make_tone(short_tone, 2.0)
    out_short = os.path.join(tmp, "narrated_short.mp4")
    video.render_reel(card, out_short, audio_path=short_tone)
    check("narration shorter than REEL_DURATION_SEC -- output matches the narration, not padded silent",
          abs(video.probe_duration(out_short) - 2.0) < 0.2,
          video.probe_duration(out_short))
    check("a narrated Reel actually carries an audio stream",
          _has_audio_stream(out_short))

    long_tone = os.path.join(tmp, "long.wav")
    _make_tone(long_tone, 10.0)
    out_long = os.path.join(tmp, "narrated_long.mp4")
    video.render_reel(card, out_long, audio_path=long_tone)
    check("narration longer than REEL_DURATION_SEC -- output stretches to the narration's length",
          abs(video.probe_duration(out_long) - 10.0) < 0.2,
          video.probe_duration(out_long))


def render_reel_reveal_tests(tmp):
    print("\nRENDER_REEL_REVEAL (headline animation, optionally narrated)")
    from config import settings
    card = os.path.join(tmp, "card2.jpg")
    _make_image(card)
    frames = [card]

    out_silent = os.path.join(tmp, "reveal_silent.mp4")
    video.render_reel_reveal(frames, out_silent)
    check("no audio_path -> old behavior, exactly REEL_DURATION_SEC, silent",
          abs(video.probe_duration(out_silent) - settings.REEL_DURATION_SEC) < 0.2,
          video.probe_duration(out_silent))
    check("the silent reveal genuinely has no audio stream",
          not _has_audio_stream(out_silent))

    long_tone = os.path.join(tmp, "long2.wav")
    _make_tone(long_tone, 10.0)
    out_long = os.path.join(tmp, "reveal_narrated.mp4")
    video.render_reel_reveal(frames, out_long, audio_path=long_tone)
    check("a narration longer than REEL_DURATION_SEC stretches the total (narration + 0.3s buffer)",
          abs(video.probe_duration(out_long) - 10.3) < 0.2,
          video.probe_duration(out_long))
    check("the stretched reveal actually carries the narration audio",
          _has_audio_stream(out_long))

    short_tone = os.path.join(tmp, "short2.wav")
    _make_tone(short_tone, 2.0)
    out_short = os.path.join(tmp, "reveal_narrated_short.mp4")
    video.render_reel_reveal(frames, out_short, audio_path=short_tone)
    check("a narration SHORTER than REEL_DURATION_SEC never shrinks the Reel below the normal default",
          abs(video.probe_duration(out_short) - settings.REEL_DURATION_SEC) < 0.2,
          video.probe_duration(out_short))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as t:
        render_reel_tests(t)
    with tempfile.TemporaryDirectory() as t:
        render_reel_reveal_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
