"""
Reel Video Renderer
====================
Turns the finished branded post image into a Reel: the static card held
still for a few seconds, silent, no zoom/pan/audio — by request.

No paid service: ffmpeg (preinstalled on GitHub Actions runners) encodes
the still image as a short silent video.
"""

import os
import subprocess

from config import settings


def render_reel(image_path, out_path, **_ignored):
    w, h = settings.REEL_WIDTH, settings.REEL_HEIGHT
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-vf", f"scale={w}:{h}",
        "-c:v", "libx264", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", str(settings.REEL_DURATION_SEC),
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr[-2000:]}")
    return out_path


if __name__ == "__main__":
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo_img = os.path.join(BASE, "demo_output.jpg")
    render_reel(demo_img, os.path.join(BASE, "demo_reel.mp4"))
    print("rendered demo reel")
