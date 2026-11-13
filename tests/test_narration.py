"""
Reel Narration Wiring -- Tests
===============================
2026-11-13, by explicit request: every Reel now gets a voice-over in
the owner's cloned voice when the clone is set up (settings.
REEL_CLONE_ENABLED + data/voice/owner_voice.pt). These tests cover the
two new, isolated pieces in src/main.py -- _narrate() and
_render_reveal_reel()'s audio_path threading -- with voice_clone mocked
out entirely, since the real clone needs a ~3GB model this test suite
must never depend on.

    python -m tests.test_narration
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main
from src import voice_clone

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def narrate_tests():
    print("\n_NARRATE (synthesises narration, never blocks a post on failure)")
    real_available = voice_clone.clone_available
    real_synth = voice_clone.make_clone_synth

    voice_clone.clone_available = lambda: False
    try:
        check("clone not set up -> returns None, no synth attempted",
              main._narrate("Some headline") is None)
    finally:
        voice_clone.clone_available = real_available

    captured = {}
    voice_clone.clone_available = lambda: True

    def fake_synth(text, wav_path):
        captured["text"] = text
        with open(wav_path, "wb") as f:
            f.write(b"fake-wav-bytes")
    voice_clone.make_clone_synth = lambda: fake_synth
    try:
        path = main._narrate("RBI cuts repo rate")
        check("clone available -> returns a real path to synthesised audio",
              path and os.path.exists(path), path)
        check("headline alone is narrated as-is, no subhead appended",
              captured["text"] == "RBI cuts repo rate", captured)

        captured.clear()
        path2 = main._narrate("RBI cuts repo rate", "Markets react positively")
        check("headline + subhead are joined into one narrated line",
              captured["text"] == "RBI cuts repo rate. Markets react positively", captured)
        if path:
            os.remove(path)
        if path2:
            os.remove(path2)
    finally:
        voice_clone.clone_available = real_available
        voice_clone.make_clone_synth = real_synth

    voice_clone.clone_available = lambda: True

    def boom_synth():
        raise RuntimeError("model failed to load")
    voice_clone.make_clone_synth = boom_synth
    try:
        check("synthesis failure returns None instead of raising -- never worth losing a post over",
              main._narrate("Some headline") is None)
    finally:
        voice_clone.clone_available = real_available
        voice_clone.make_clone_synth = real_synth


def render_reveal_reel_audio_threading_tests():
    print("\n_RENDER_REVEAL_REEL (audio_path reaches both the animation and its fallback)")
    import tempfile
    real_reveal = main.video.render_reel_reveal
    real_still = main.video.render_reel
    calls = []

    def fake_render_card(path, reveal):
        with open(path, "wb") as f:
            f.write(b"fake-image-bytes")

    with tempfile.TemporaryDirectory() as tmp:
        video_path = os.path.join(tmp, "out.mp4")
        card_path = os.path.join(tmp, "card.jpg")

        # 1. the animation path succeeds -- audio_path must reach it
        main.video.render_reel_reveal = lambda *a, **k: calls.append(("reveal", k.get("audio_path"))) or video_path
        main.video.render_reel = lambda *a, **k: calls.append(("still", k.get("audio_path")))
        try:
            main._render_reveal_reel(video_path, card_path, fake_render_card, 2,
                                      audio_path="/fake/narration.wav")
            check("the reveal animation receives audio_path",
                  ("reveal", "/fake/narration.wav") in calls, calls)
        finally:
            main.video.render_reel_reveal = real_reveal
            main.video.render_reel = real_still

        # 2. the animation path fails -- the still fallback must ALSO get audio_path,
        #    not silently drop the narration just because the animation broke
        calls.clear()
        main.video.render_reel_reveal = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg exploded"))
        main.video.render_reel = lambda *a, **k: calls.append(("still", k.get("audio_path")))
        try:
            main._render_reveal_reel(video_path, card_path, fake_render_card, 2,
                                      audio_path="/fake/narration.wav")
            check("the still-Reel fallback still carries the narration after an animation failure",
                  ("still", "/fake/narration.wav") in calls, calls)
        finally:
            main.video.render_reel_reveal = real_reveal
            main.video.render_reel = real_still

        # 3. no narration at all -- both paths must pass audio_path=None, not omit it
        calls.clear()
        main.video.render_reel_reveal = lambda *a, **k: calls.append(("reveal", k.get("audio_path"))) or video_path
        try:
            main._render_reveal_reel(video_path, card_path, fake_render_card, 2)
            check("no audio_path given -> explicitly passed through as None, old silent behavior",
                  ("reveal", None) in calls, calls)
        finally:
            main.video.render_reel_reveal = real_reveal
            main.video.render_reel = real_still


if __name__ == "__main__":
    narrate_tests()
    render_reveal_reel_audio_threading_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
