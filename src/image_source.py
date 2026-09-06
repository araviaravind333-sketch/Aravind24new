"""
Image Source
============
Gets a high-quality, LEGALLY SAFE photo for the story.
Order:
  1. AI-generated (Pollinations = free no-key; HuggingFace / Stability if keys set)
  2. Pexels stock (free key)
  3. Unsplash stock (free key)
All returned images are safe to post commercially. We NEVER scrape
publisher photos (copyright / account-ban risk).
"""

import os
import io
import urllib.request
import urllib.parse
import json

from config import settings

TMP = os.path.join(os.path.dirname(__file__), "..", "data", "tmp_image.jpg")


def _save(data, path=TMP):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _image_prompt(story):
    """Build a clean editorial prompt from the story."""
    t = story["title"]
    cat = story["category"].replace(" NEWS", "").lower()
    return (f"professional editorial news photograph representing: {t}. "
            f"{cat} theme, realistic, high detail, cinematic lighting, "
            f"no text, no watermark, news documentary style")


# ---------- 1. AI: Pollinations (free, no key) ----------
def _ai_pollinations(story):
    prompt = urllib.parse.quote(_image_prompt(story))
    url = (f"https://image.pollinations.ai/prompt/{prompt}"
           f"?width=1080&height=1350&nologo=true&enhance=true")
    req = urllib.request.Request(url, headers={"User-Agent": "AravindNews24/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) > 10000:
        return _save(data)
    raise RuntimeError("pollinations returned too-small image")


# ---------- 1b. AI: HuggingFace (free tier, needs token) ----------
def _ai_huggingface(story):
    token = settings.HUGGINGFACE_TOKEN
    if not token:
        raise RuntimeError("no HF token")
    model = "black-forest-labs/FLUX.1-schnell"
    body = json.dumps({"inputs": _image_prompt(story)}).encode()
    req = urllib.request.Request(
        f"https://api-inference.huggingface.co/models/{model}",
        data=body,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if data[:3] == b"\xff\xd8\xff" or len(data) > 10000:
        return _save(data)
    raise RuntimeError("HF returned non-image")


# ---------- 2. Pexels stock ----------
def _stock_pexels(story):
    key = settings.PEXELS_API_KEY
    if not key:
        raise RuntimeError("no pexels key")
    q = urllib.parse.quote(story["category"].replace(" NEWS", "") + " " +
                           " ".join(story["title"].split()[:3]))
    url = f"https://api.pexels.com/v1/search?query={q}&per_page=5&orientation=portrait"
    req = urllib.request.Request(url, headers={"Authorization": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    photos = res.get("photos", [])
    if not photos:
        raise RuntimeError("no pexels results")
    img_url = photos[0]["src"]["large2x"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


# ---------- 3. Unsplash stock ----------
def _stock_unsplash(story):
    key = settings.UNSPLASH_ACCESS_KEY
    if not key:
        raise RuntimeError("no unsplash key")
    q = urllib.parse.quote(story["category"].replace(" NEWS", ""))
    url = (f"https://api.unsplash.com/search/photos?query={q}"
           f"&orientation=portrait&per_page=5&client_id={key}")
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)
    results = res.get("results", [])
    if not results:
        raise RuntimeError("no unsplash results")
    img_url = results[0]["urls"]["regular"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


def get_image(story):
    """Try each source in order; return path to a saved image."""
    chain = []
    provider = settings.AI_IMAGE_PROVIDER
    if provider == "hf":
        chain = [_ai_huggingface, _ai_pollinations, _stock_pexels, _stock_unsplash]
    else:
        chain = [_ai_pollinations, _ai_huggingface, _stock_pexels, _stock_unsplash]

    for fn in chain:
        try:
            path = fn(story)
            print(f"image via {fn.__name__}")
            return path
        except Exception as e:
            print(f"{fn.__name__} failed: {e}")
    raise RuntimeError("ALL image sources failed")
