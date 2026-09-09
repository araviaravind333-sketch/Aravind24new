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


def probe_duration(video_path):
    """Real length of a submitted video clip, via ffprobe -- used to decide
    the trim rule in settings (REEL_CLIP_TRIM_THRESHOLD_SEC /
    REEL_CLIP_TRIM_TARGET_SEC): keep a clip's own length unless it's over
    the threshold, in which case trim to the target. Returns None if the
    file's duration can't be read (caller falls back to a safe default)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


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


def extract_frame(video_path, out_path, at_sec=0.3):
    """Grabs one still frame from a real video clip (e.g. a video submitted
    in reply to a candidate instead of a photo) to stand in for the normal
    static post image -- the reel itself uses the actual clip via
    render_reel_from_clip, this frame is only for the accompanying static
    JPG. Seeks slightly past 0 so it doesn't land on a black/transition
    opening frame."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(at_sec), "-i", video_path,
        "-frames:v", "1", "-q:v", "2",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(out_path):
        # very short clips can have nothing at at_sec -- retry from the start
        cmd[2] = "0"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"frame extraction failed ({result.returncode}): {result.stderr[-1000:]}")
    return out_path


def render_reel_from_clip(clip_path, overlay_png_path, out_path, max_duration_sec):
    """Builds a Reel from a real submitted video clip instead of holding a
    static image still -- the clip is scaled/center-cropped to fill the
    reel frame exactly like a photo would be (_fit_cover's video
    equivalent), then the same branded graphic used on static posts
    (category pill, headline, footer -- a transparent PNG from
    template.render_overlay_png) is composited on top via ffmpeg's overlay
    filter, so a video-based reel still looks like an AravindNews24 post
    and not just raw footage. Muted like every other reel -- silent was an
    explicit design decision, not a limitation of this path."""
    w, h = settings.REEL_WIDTH, settings.REEL_HEIGHT
    filter_complex = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}[bg];[bg][1:v]overlay=0:0[outv]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-loop", "1", "-i", overlay_png_path,
        "-t", str(max_duration_sec),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
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
