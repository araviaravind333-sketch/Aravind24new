"""
Daily Roundup Reel
==================
Turns the day's reviewed carousel into a vertical 9:16 Reel with a
voice-over: intro, one slide per story with its headline read aloud, and
a follow call-to-action.

WHY THIS EXISTS
---------------
Measured on the live account: feed posts reached 1-9 people each, and the
one spike (8K views) came from a shoutout. Reels are the only format
Instagram deliberately shows to people who don't follow you yet, so for a
7-follower page they are where reach can actually come from. The Reels
posted before this were 6-second SILENT still cards -- no sound, no
motion, no hook -- which is about the weakest thing that can be posted as
a Reel. A voice-over gives it sound and gives viewers a reason to stay
past the first second.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
 * No stock, borrowed or generated imagery -- the visuals are exactly the
   slides that were already reviewed (text cards, your own photos,
   verified licensed portraits). Same "no image beats wrong image" rule.
 * No zoom / pan (removed from this project by request).
 * No copyrighted music. The only audio is speech synthesised by Piper
   with the LJ Speech voice, whose training data is public domain (stated
   in the voice's own model card).

Nothing new needs reviewing: the reel is built from the survivors of the
carousel review, after it has been published.
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave

from PIL import Image, ImageDraw

from config import settings
from src.template import (ACCENT, ANTON, ARCHIVO, MUTED, NEAR_BLACK, WHITE,
                          _font, _hex_to_rgb, _vertical_gradient)

REEL_W, REEL_H = 1080, 1920
GAP_SEC = 0.25            # breathing room after each spoken line
# Piper's length_scale: <1 is faster. 0.88 keeps the voice natural but takes
# roughly a tenth off a ~60s reel -- retention on a news roundup falls off
# hard past the first 45s or so.
SPEECH_LENGTH_SCALE = 0.88
MIN_TAIL_SEC = 1.6        # a slide never flashes by faster than this

VOICE = "en_US-ljspeech-high"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high/"
# Outside the repo on purpose: the model is ~110 MB and several workflows
# run `git add data/`, which would try to commit it and exceed GitHub's
# 100 MB file limit -- breaking every pipeline that pushes.
MODEL_DIR = os.path.join(tempfile.gettempdir(), "aravindnews24_tts")


# --------------------------------------------------------------- narration

_DANGLING = {
    "a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "from", "in", "on",
    "at", "by", "as", "after", "before", "over", "under", "into", "that", "this", "its",
    "their", "his", "her", "is", "are", "was", "were", "will", "says", "said", "amid",
    "against", "about", "tried", "tries", "attempted", "seeks", "plans", "wants",
    "likely", "set", "than", "has", "have",
}

_NUM_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
              "nine", "ten", "eleven", "twelve"]


def speakable(text):
    """Headline text -> something a TTS voice reads correctly. Currency
    symbols, 'Rs', percent signs and stray punctuation are the things
    that make synthesised news sound wrong if left as they are."""
    t = (text or "").strip()
    t = re.sub(r"\.{2,}|…", " ", t)
    t = re.sub(r"[‘’]", "'", t)
    t = re.sub(r"(?:₹|Rs\.?|INR)\s?([\d][\d,\.]*)(\s?(?:crore|lakh|million|billion|thousand))?",
               lambda m: f"{m.group(1)}{m.group(2) or ''} rupees", t, flags=re.I)
    t = t.replace("%", " percent").replace("&", " and ")
    t = re.sub(r"\bvs\.?\b", "versus", t, flags=re.I)
    t = re.sub(r"\bLIVE\b:?", "live", t)
    t = re.sub(r"[|#@*_~^\[\]{}<>]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" :-–—")
    words = t.split()
    if len(words) > 20:                       # keep each story to a few seconds
        words = words[:20]
    # never read a broken ending aloud ("...Houthi rebels tried.")
    while len(words) > 3 and words[-1].lower().strip(",.;:") in _DANGLING:
        words.pop()
    return " ".join(words)


def script_for(headlines, brand_spoken="Aravind News twenty four"):
    """(intro, [story lines], outro) as spoken text."""
    intro = "Here is what happened in the last twenty four hours."
    stories = []
    for i, h in enumerate(headlines, start=1):
        n = _NUM_WORDS[i] if i < len(_NUM_WORDS) else str(i)
        line = speakable(h)
        stories.append(f"Number {n}. {line}." if line else f"Number {n}.")
    outro = f"Follow {brand_spoken}, for the daily roundup, every evening."
    return intro, stories, outro


# --------------------------------------------------------------------- TTS

def ensure_voice():
    """Downloads the voice once into MODEL_DIR; returns the .onnx path."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    for name in (VOICE + ".onnx", VOICE + ".onnx.json"):
        path = os.path.join(MODEL_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            continue
        last = None
        for attempt in range(3):
            try:
                urllib.request.urlretrieve(VOICE_URL + name + "?download=true", path)
                break
            except Exception as e:
                last = e
        else:
            raise RuntimeError(f"could not download voice file {name}: {last}")
    model = os.path.join(MODEL_DIR, VOICE + ".onnx")
    if os.path.getsize(model) < 50 * 1024 * 1024:
        raise RuntimeError("voice model download looks truncated")
    json.load(open(model + ".json", encoding="utf-8"))
    return model


def _import_piper():
    try:
        from piper import PiperVoice
        return PiperVoice
    except ImportError:
        # Installed on demand, and only when a reel is actually being
        # built: every workflow shares requirements.txt, and this adds
        # ~15 MB of onnxruntime that the other jobs never need.
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "piper-tts"])
        from piper import PiperVoice
        return PiperVoice


def make_piper_synth(length_scale=None):
    """Returns synth(text, wav_path) backed by the real voice."""
    length_scale = SPEECH_LENGTH_SCALE if length_scale is None else length_scale
    voice = _import_piper().load(ensure_voice())
    try:
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=length_scale)
    except Exception:                              # older piper: default speed
        cfg = None

    def synth(text, wav_path):
        with wave.open(wav_path, "wb") as w:
            if hasattr(voice, "synthesize_wav"):
                if cfg is not None:
                    voice.synthesize_wav(text, w, syn_config=cfg)
                else:
                    voice.synthesize_wav(text, w)
            else:                                  # older piper releases
                voice.synthesize(text, w)
    return synth


def _wav_info(path):
    with wave.open(path, "rb") as r:
        return r.getnchannels(), r.getsampwidth(), r.getframerate(), r.getnframes()


def _concat_wavs(parts, out_path):
    """parts: [(wav_path, silence_sec_after)] -> one wav; returns the
    duration of each part INCLUDING its trailing silence."""
    ch, sw, rate, _ = _wav_info(parts[0][0])
    durations = []
    with wave.open(out_path, "wb") as out:
        out.setnchannels(ch)
        out.setsampwidth(sw)
        out.setframerate(rate)
        for path, gap in parts:
            with wave.open(path, "rb") as r:
                frames = r.readframes(r.getnframes())
                n = r.getnframes()
            silence = int(rate * gap)
            out.writeframes(frames)
            out.writeframes(b"\x00" * silence * ch * sw)
            durations.append((n + silence) / rate)
    return durations


# ------------------------------------------------------------------ frames

def _bar(draw, current, total, y=200):
    left, right, gap = 70, REEL_W - 70, 8
    w = (right - left - gap * (total - 1)) / total
    for i in range(total):
        x0 = left + i * (w + gap)
        fill = (255, 255, 255) if i <= current else (70, 70, 70)
        draw.rounded_rectangle([x0, y, x0 + w, y + 8], radius=4, fill=fill)


def render_frame(slide_path, current, total, date_label):
    """A 1080x1920 frame: brand header + progress bar (which story we are
    on), the reviewed 4:5 slide unchanged in the middle, and a follow line.
    Everything important stays inside Instagram's Reels safe area."""
    canvas = _vertical_gradient(REEL_W, REEL_H, (16, 16, 18), (6, 6, 8)).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    f_brand = _font(ANTON, 58)
    draw.rectangle([70, 108, 90, 156], fill=(224, 30, 30))
    draw.text((108, 100), "ARAVIND NEWS 24", font=f_brand, fill=WHITE)
    f_date = _font(ARCHIVO, 34)
    dw = draw.textlength(date_label, font=f_date)
    draw.text((REEL_W - 70 - dw, 116), date_label, font=f_date, fill=MUTED)
    _bar(draw, current, total)

    slide = Image.open(slide_path).convert("RGB")
    if slide.width != REEL_W:
        slide = slide.resize((REEL_W, int(slide.height * REEL_W / slide.width)), Image.LANCZOS)
    canvas.paste(slide, (0, 270))

    f_cta = _font(ANTON, 44)
    cta = f"FOLLOW {settings.BRAND_HANDLE.upper()} FOR DAILY NEWS"
    cw = draw.textlength(cta, font=f_cta)
    draw.text(((REEL_W - cw) / 2, 1660), cta, font=f_cta, fill=_hex_to_rgb(ACCENT))
    return canvas


def render_end_frame(total, date_label):
    """Last card: a plain, unmissable follow ask (spoken over it too)."""
    canvas = _vertical_gradient(REEL_W, REEL_H, (22, 22, 26), (6, 6, 8)).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    _bar(draw, total, total)
    f_big = _font(ANTON, 150)
    for i, line in enumerate(["FOLLOW", "FOR THE", "DAILY", "ROUNDUP"]):
        w = draw.textlength(line, font=f_big)
        draw.text(((REEL_W - w) / 2, 380 + i * 170), line, font=f_big,
                  fill=_hex_to_rgb(ACCENT) if i == 0 else WHITE)
    f_h = _font(ANTON, 76)
    handle = settings.BRAND_HANDLE
    hw = draw.textlength(handle, font=f_h)
    draw.rounded_rectangle([(REEL_W - hw) / 2 - 40, 1120, (REEL_W + hw) / 2 + 40, 1230],
                           radius=14, fill=(224, 30, 30))
    draw.text(((REEL_W - hw) / 2, 1128), handle, font=f_h, fill=WHITE)
    f_s = _font(ARCHIVO, 38)
    sub = "Every evening · 8:30 PM IST · India + world"
    sw_ = draw.textlength(sub, font=f_s)
    draw.text(((REEL_W - sw_) / 2, 1290), sub, font=f_s, fill=MUTED)
    f_d = _font(ARCHIVO, 34)
    dw = draw.textlength(date_label, font=f_d)
    draw.text((REEL_W - 70 - dw, 116), date_label, font=f_d, fill=MUTED)
    return canvas


# ---------------------------------------------------------------- assemble

def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def build_reel(slide_paths, headlines, out_path, date_label, synth=None, work_dir=None):
    """slide_paths[0] is the cover; slide_paths[1:] are the story slides in
    order, and `headlines` holds one headline per story slide. `synth` is
    injectable so the assembly can be tested without the real voice.
    Returns {"path", "duration", "segments"}."""
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg not available")
    assert len(slide_paths) == len(headlines) + 1, "one headline per story slide, cover excluded"
    synth = synth or make_piper_synth()
    work = work_dir or tempfile.mkdtemp(prefix="reel_")
    os.makedirs(work, exist_ok=True)

    intro, stories, outro = script_for(headlines)
    lines = [intro] + stories + [outro]
    total = len(slide_paths)                 # cover + stories (end card sits after the bar)

    parts, frames = [], []
    for i, text in enumerate(lines):
        wav = os.path.join(work, f"line_{i:02d}.wav")
        synth(text, wav)
        parts.append((wav, GAP_SEC))
        png = os.path.join(work, f"frame_{i:02d}.png")
        if i < len(slide_paths):
            render_frame(slide_paths[i], i, total, date_label).save(png)
        else:
            render_end_frame(total, date_label).save(png)
        frames.append(png)

    narration = os.path.join(work, "narration.wav")
    durations = [max(d, MIN_TAIL_SEC) for d in _concat_wavs(parts, narration)]

    listing = os.path.join(work, "frames.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for png, dur in zip(frames, durations):
            f.write(f"file '{png.replace(chr(92), '/')}'\nduration {dur:.3f}\n")
        f.write(f"file '{frames[-1].replace(chr(92), '/')}'\n")   # concat needs the last frame twice

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [exe, "-y", "-f", "concat", "-safe", "0", "-i", listing, "-i", narration,
           "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
           "-tune", "stillimage", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
           "-shortest", "-movflags", "+faststart", out_path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({res.returncode}): {res.stderr[-1500:]}")
    return {"path": out_path, "duration": sum(durations), "segments": len(lines)}


def reel_caption(n_stories, seconds, now_ist=None):
    return "\n".join([
        f"\U0001F4F0 Everything that happened in the last 24 hours — {n_stories} stories in "
        f"{int(round(seconds))} seconds.",
        "",
        f"\U0001F449 Follow {settings.BRAND_HANDLE} — daily India + world roundup, every evening at 8:30 PM IST",
        "\U0001F4AC Which story matters most to you? Comment below",
        "\U0001F501 Share with someone who misses the news",
        "",
        "#IndiaNews #WorldNews #NewsUpdate #Trending #Reels #AravindNews24",
    ])
