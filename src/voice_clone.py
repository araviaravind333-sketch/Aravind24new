"""
Owner Voice Clone (Reel narration)
==================================
Narrates the daily roundup Reel in the OWNER'S OWN voice, from one recorded
sample, using Chatterbox (Resemble AI, MIT licence, runs on CPU).

  * One-time: `python -m src.main make-voice-profile <sample audio>` turns the
    sample into a small voice profile (data/voice/owner_voice.pt). Only the
    profile is committed -- never the raw recording.
  * Every day: the Reel's lines are synthesised from that profile.

Consent: this clones the account owner's own voice from their own recording,
and is used only for this account's narration.

Speed: roughly 12x slower than real time on a CPU, so a ~1 minute Reel takes
~12-15 minutes to synthesise. The model (~3 GB) and the `chatterbox-tts`
package are installed/downloaded on first use only, never for a run that
does not build a Reel.
"""

import os
import subprocess
import sys
import wave

from config import settings

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_PATH = os.path.join(_ROOT, "data", "voice", "owner_voice.pt")

# Chosen by the owner from listening tests (variant "D"): more expressive
# than the defaults (0.5) and cfg_weight 0 so the pacing is not tied to the
# reference clip -- the default settings sounded robotic.
# cfg_weight is 0.02, not 0: an exact 0 crashes chatterbox 0.1.7 in t3.inference
# (batch-size mismatch), so the "free pacing" variant is approximated with ~0.
GEN_KWARGS = dict(exaggeration=0.7, cfg_weight=0.02, temperature=0.8)


def profile_exists(path=None):
    return os.path.exists(path or PROFILE_PATH)


def clone_available():
    return bool(settings.REEL_CLONE_ENABLED and profile_exists())


def ensure_deps():
    try:
        import chatterbox  # noqa: F401
    except ImportError:
        print("voice_clone: installing chatterbox-tts (first use)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "chatterbox-tts"], check=True)


def _load_model():
    """Same as ChatterboxTTS.from_pretrained("cpu"), but each weights file is
    freed as soon as it is loaded. The stock loader keeps the 2 GB text-model
    weights alive while it builds the next model, which needs ~6 GB peak and
    crashed (access violation) on a 7 GB PC; this peaks near 4 GB."""
    ensure_deps()
    import gc
    from pathlib import Path
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file
    from chatterbox import tts as cb

    for name in ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]:
        local = hf_hub_download(repo_id=cb.REPO_ID, filename=name)
    ckpt = Path(local).parent

    ve = cb.VoiceEncoder()
    ve.load_state_dict(load_file(ckpt / "ve.safetensors"))
    ve.eval()

    t3 = cb.T3()
    state = load_file(ckpt / "t3_cfg.safetensors")
    if "model" in state.keys():
        state = state["model"][0]
    t3.load_state_dict(state)
    del state
    gc.collect()
    t3.eval()

    s3gen = cb.S3Gen()
    state = load_file(ckpt / "s3gen.safetensors")
    s3gen.load_state_dict(state, strict=False)
    del state
    gc.collect()
    s3gen.eval()

    tokenizer = cb.EnTokenizer(str(ckpt / "tokenizer.json"))
    return cb.ChatterboxTTS(t3, s3gen, ve, tokenizer, "cpu", conds=None)


def _write_wav(tensor, sr, path):
    import numpy as np
    pcm = (tensor.squeeze(0).clamp(-1, 1).numpy() * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _clean_sample(sample_path, out_wav):
    """Any recording (m4a / mp3 / wav / ogg) -> mono 24 kHz wav, silence
    trimmed from both ends."""
    from src import roundup_reel
    exe = roundup_reel.ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg not available")
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1"
    res = subprocess.run([exe, "-y", "-i", sample_path, "-vn", "-af", f"{trim},areverse,{trim},areverse",
                          "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", out_wav],
                         capture_output=True, text=True, timeout=180)
    if res.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError("could not read the voice sample: " + res.stderr[-300:])
    with wave.open(out_wav, "rb") as w:
        seconds = w.getnframes() / w.getframerate()
    if seconds < 5:
        raise RuntimeError(f"the sample is only {seconds:.1f}s of speech -- record at least 15-20 seconds")
    return seconds


def make_profile(sample_path, out_path=None):
    import tempfile
    out_path = out_path or PROFILE_PATH
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    clean = os.path.join(tempfile.mkdtemp(prefix="voice_"), "sample.wav")
    seconds = _clean_sample(sample_path, clean)
    print(f"voice_clone: sample is {seconds:.1f}s of speech")
    model = _load_model()
    model.prepare_conditionals(clean)
    model.conds.save(out_path)
    print("voice_clone: profile saved to", out_path)
    return out_path


def make_clone_synth(profile_path=None):
    """Returns synth(text, wav_path), the same interface as the Piper synth."""
    from chatterbox.tts import Conditionals
    model = _load_model()
    model.conds = Conditionals.load(profile_path or PROFILE_PATH, map_location="cpu").to("cpu")

    def synth(text, wav_path):
        _write_wav(model.generate(text, **GEN_KWARGS), model.sr, wav_path)

    return synth
