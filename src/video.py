"""
Reel Video Renderer
====================
Turns the finished branded post image into a short Ken Burns-style motion
clip (slow zoom on the whole card) for Instagram Reels — Reels get 5-10x
the reach of a static image post right now, per the growth plan.

No paid service, no extra Python deps: uses ffmpeg, which is preinstalled
on GitHub Actions' ubuntu-latest runners.

v1 has no audio (silent Reels still play fine; picking real trending audio
automatically isn't something Meta's API exposes safely, so that stays a
manual/future step, not something to fake).
"""

import os
import subprocess

from config import settings


def render_reel(image_path, out_path, duration=None, fps=None, max_zoom=1.12):
    duration = duration or settings.REEL_DURATION_SEC
    fps = fps or settings.REEL_FPS
    w, h = settings.REEL_WIDTH, settings.REEL_HEIGHT
    total_frames = int(duration * fps)

    vf = (
        f"scale=w={w*3}:h=-2,"
        f"zoompan=z='min(zoom+0.0012,{max_zoom})':"
        f"d={total_frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-vf", vf,
        "-t", str(duration),
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr[-2000:]}")
    return out_path


if __name__ == "__main__":
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo_img = os.path.join(BASE, "demo_output.jpg")
    render_reel(demo_img, os.path.join(BASE, "demo_reel.mp4"), duration=6, fps=30)
    print("rendered demo reel")
