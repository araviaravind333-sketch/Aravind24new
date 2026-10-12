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
    """Holds the branded card as a Reel, completely still, silent -- the
    Ken Burns zoom effect tried here was reverted by request; it didn't
    look good in practice regardless of the algorithmic theory behind it.

    Keeps one real fix made alongside that experiment: this is sometimes
    called with the 4:5 (1080x1350) static feed card as the source (the
    no-photo text_card/alert_card fallback, which has no dedicated 9:16
    reel design) -- a plain scale=w:h used to stretch that 4:5 image
    vertically to fit the 9:16 reel frame instead of fitting it properly.
    Cover-fit (scale + center-crop) first, matching how every photo
    elsewhere in this pipeline is fit into its frame."""
    w, h = settings.REEL_WIDTH, settings.REEL_HEIGHT
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-vf", vf,
        # No -crf here used to mean libx264's default (23), which on a
        # single still frame held for several seconds produced a visibly
        # blurry/blocky Reel (~280 kbps for a 1080x1920 frame, a real
        # posted example) -- a low CRF costs almost nothing in file size
        # for unchanging content, since every frame after the first is a
        # near-empty P-frame, so there is no reason not to keep the one
        # frame that matters sharp.
        "-c:v", "libx264", "-tune", "stillimage", "-crf", "16",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", str(settings.REEL_DURATION_SEC),
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr[-2000:]}")
    return out_path


def render_reel_reveal(frame_paths, out_path, step_sec=0.28, end_frame=None, end_sec=1.1):
    """Animates the headline in, a line at a time, then holds the
    finished card for the rest of REEL_DURATION_SEC.

    Why this and not a Ken Burns push on the photo: that was tried here
    before and rejected on sight (see render_reel). A slow zoom moves
    the very thing the viewer is trying to read. This moves only the
    text, which is also the thing worth watching -- the headline arrives
    line by line instead of being fully present at frame 0, so there is
    a reason to still be watching at second two. Instagram ranks on
    watch time, and a card that is completely readable instantly gives a
    viewer no reason to stay; the measured symptom was Reels reaching
    300-800 non-followers and producing no likes, saves or follows.

    Each frame is the same card with one more headline line drawn, and
    all surrounding geometry is measured from the FULL headline (see
    feed_post.render_overlay_reel), so nothing reflows between frames --
    the words simply appear."""
    if not frame_paths:
        raise ValueError("render_reel_reveal needs at least one frame")
    total = settings.REEL_DURATION_SEC
    # The animation must never eat the whole Reel: the finished headline
    # has to stay up long enough to actually be read.
    step = min(step_sec, (total * 0.45) / max(1, len(frame_paths)))
    # The end card is carved OUT of the same total, never bolted on: a
    # longer Reel is a lower completion rate, and completion is the
    # metric this whole change exists to protect.
    tail = end_sec if end_frame else 0.0
    hold = max(0.5, total - tail - step * (len(frame_paths) - 1))

    seq = [(p, step) for p in frame_paths[:-1]]
    seq.append((frame_paths[-1], hold))
    if end_frame:
        seq.append((end_frame, tail))

    listing = out_path + ".frames.txt"
    with open(listing, "w", encoding="utf-8") as f:
        for p, dur in seq:
            f.write(f"file '{os.path.abspath(p)}'\nduration {dur:.3f}\n")
        # The concat demuxer only applies a duration to a frame that is
        # followed by another entry, so the last image is repeated.
        f.write(f"file '{os.path.abspath(seq[-1][0])}'\n")

    w, h = settings.REEL_WIDTH, settings.REEL_HEIGHT
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,"
               f"crop={w}:{h},fps=30,format=yuv420p",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-movflags", "+faststart", "-t", str(total),
        out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    finally:
        try:
            os.remove(listing)
        except OSError:
            pass
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
