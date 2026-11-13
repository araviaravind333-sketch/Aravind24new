"""
Owner Voice Clone -- Tests
===========================
Only ensure_deps()'s install-ordering logic is covered here: the real
clone needs a ~3GB model and the chatterbox-tts/torch packages, which
this test suite must never download. subprocess.run and the chatterbox/
torch imports are faked via sys.modules, the same pattern already used
elsewhere in this suite for yt-dlp.

    python -m tests.test_voice_clone
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import voice_clone

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def _pop_real(name):
    """Removes a real or previously-faked module from sys.modules so the
    next `import` re-evaluates against whatever this test just set up,
    and returns it for restoration."""
    return sys.modules.pop(name, None)


def ensure_deps_tests():
    print("\nENSURE_DEPS (install ordering: CPU torch before chatterbox-tts)")
    real_chatterbox = _pop_real("chatterbox")
    real_torch = _pop_real("torch")
    real_run = voice_clone.subprocess.run
    calls = []
    voice_clone.subprocess.run = lambda cmd, **k: calls.append(cmd)
    try:
        # Neither chatterbox nor torch is importable -- CPU torch must be
        # installed FIRST, so pip never resolves chatterbox-tts's own
        # torch dependency into the default (multi-GB CUDA) wheel.
        voice_clone.ensure_deps()
        check("exactly two pip install calls happen (torch, then chatterbox-tts)",
              len(calls) == 2, calls)
        check("torch is installed from the CPU-only index, pinned, before chatterbox-tts",
              "torch==2.6.0" in calls[0] and "--index-url" in calls[0]
              and "https://download.pytorch.org/whl/cpu" in calls[0], calls)
        check("chatterbox-tts is installed second, with no extra index (uses torch already present)",
              "chatterbox-tts" in calls[1] and "--index-url" not in calls[1], calls)

        # torch already importable (a fake module stands in) -- must skip
        # straight to installing chatterbox-tts, no CPU-torch reinstall.
        calls.clear()
        sys.modules["torch"] = types.ModuleType("torch")
        voice_clone.ensure_deps()
        check("torch already present -> only chatterbox-tts gets installed",
              len(calls) == 1 and "chatterbox-tts" in calls[0], calls)

        # chatterbox already importable -- nothing should be installed at all.
        calls.clear()
        sys.modules["chatterbox"] = types.ModuleType("chatterbox")
        voice_clone.ensure_deps()
        check("chatterbox already present -> no install attempted at all",
              calls == [], calls)
    finally:
        voice_clone.subprocess.run = real_run
        _pop_real("chatterbox")
        _pop_real("torch")
        if real_chatterbox is not None:
            sys.modules["chatterbox"] = real_chatterbox
        if real_torch is not None:
            sys.modules["torch"] = real_torch


if __name__ == "__main__":
    ensure_deps_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
