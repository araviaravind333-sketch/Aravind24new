"""
Reel Video Renderer
====================
Turns the finished branded post image into a Reel: the static card, held
still (no zoom/pan — by request), with a spoken voiceover reading out the
actual news as the audio track.

Why a voiceover instead of "trending audio": Meta's API has no endpoint to
browse or attach an actual trending Instagram Reels sound — that catalog is
only accessible from inside the app, by a human. Faking it by guessing a
track would just be silence with extra steps. A real spoken narration of
the story is the honest, automatable version of "audio based on the news" —
it needs no licensing, and it's a format that plenty of real news accounts
use for exactly this reason.

No paid service: ffmpeg (preinstalled on GitHub Actions runners) muxes
video + audio; gTTS (free, no API key) generates the voiceover.
"""

import os
import subprocess

from gtts import gTTS

from config import settings


def _make_voiceover(text, out_path):
    tts = gTTS(text=text, lang="en", tld="co.in")  # co.in = Indian English accent
    tts.save(out_path)
    return out_path


def render_reel(image_path, out_path, narration_text, max_duration=None):
    max_duration = max_duration or settings.REEL_MAX_DURATION_SEC
    w, h = settings.REEL_WIDTH, settings.REEL_HEIGHT
    audio_path = out_path.rsplit(".", 1)[0] + "_voice.mp3"

    have_audio = False
    try:
        _make_voiceover(narration_text, audio_path)
        have_audio = os.path.exists(audio_path) and os.path.getsize(audio_path) > 500
    except Exception as e:
        print("Voiceover generation failed, rendering a silent reel instead:", e)

    if have_audio:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-i", audio_path,
            "-vf", f"scale={w}:{h}",
            "-c:v", "libx264", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-shortest", "-t", str(max_duration),
            out_path,
        ]
    else:
        # silent fallback — still a valid Reel, just no narration
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", f"scale={w}:{h}",
            "-c:v", "libx264", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-t", str(settings.REEL_MIN_DURATION_SEC),
            out_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if os.path.exists(audio_path):
        os.remove(audio_path)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr[-2000:]}")
    return out_path


if __name__ == "__main__":
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo_img = os.path.join(BASE, "demo_output.jpg")
    render_reel(
        demo_img, os.path.join(BASE, "demo_reel.mp4"),
        narration_text="RBI cuts repo rate to boost economy. The central bank lowered rates to support growth.",
    )
    print("rendered demo reel")
