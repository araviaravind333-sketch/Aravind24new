"""
Publisher
=========
Posts the finished image + caption to BOTH:
  - Instagram (via Graph API: create media container -> publish)
  - Facebook Page (via Graph API: /photos)
Adds location tag (India, or the detected foreign city).

IMPORTANT: Instagram Graph API needs a PUBLIC image URL. GitHub Actions
uploads the rendered image to the repo's own GitHub Pages / a release asset,
or to a free image host. Here we use GitHub Pages raw URL (see workflow).
"""

import os
import json
import time
import urllib.request
import urllib.parse

from config import settings

V = settings.GRAPH_VERSION
BASE = f"https://graph.facebook.com/{V}"


def _post(url, params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=60) as r:
        return json.load(r)


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=60) as r:
        return json.load(r)


# ---------- location search (IG requires a location page id) ----------
def _find_location_id(place):
    if not place:
        return None
    try:
        res = _get(f"{BASE}/pages/search", {
            "q": place,
            "type": "place",
            "access_token": settings.META_PAGE_ACCESS_TOKEN,
        })
        data = res.get("data", [])
        if data:
            return data[0]["id"]
    except Exception as e:
        print("location search failed:", e)
    return None


# ---------- Instagram ----------
def publish_instagram(image_url, caption, place=None):
    token = settings.META_PAGE_ACCESS_TOKEN
    ig_id = settings.IG_USER_ID
    params = {
        "image_url": image_url,
        "caption": caption,
        "access_token": token,
    }
    loc = _find_location_id(place)
    if loc:
        params["location_id"] = loc

    container = _post(f"{BASE}/{ig_id}/media", params)
    cid = container.get("id")
    if not cid:
        raise RuntimeError(f"IG container failed: {container}")

    # wait for container to be ready
    for _ in range(10):
        status = _get(f"{BASE}/{cid}", {
            "fields": "status_code", "access_token": token})
        if status.get("status_code") == "FINISHED":
            break
        time.sleep(5)

    pub = _post(f"{BASE}/{ig_id}/media_publish", {
        "creation_id": cid, "access_token": token})
    return pub


# ---------- Facebook Page ----------
def publish_facebook(image_url, caption, place=None):
    token = settings.META_PAGE_ACCESS_TOKEN
    page_id = settings.FB_PAGE_ID
    params = {
        "url": image_url,
        "caption": caption,          # FB uses 'caption'/'message'
        "message": caption,
        "access_token": token,
    }
    loc = _find_location_id(place)
    if loc:
        params["place"] = loc
    return _post(f"{BASE}/{page_id}/photos", params)


def publish_all(image_url, caption, geo):
    place = None
    if geo:
        place = "India" if geo.get("is_india") else geo.get("place")
    results = {}
    try:
        results["instagram"] = publish_instagram(image_url, caption, place)
        print("IG posted:", results["instagram"])
    except Exception as e:
        results["instagram_error"] = str(e)
        print("IG error:", e)
    try:
        results["facebook"] = publish_facebook(image_url, caption, place)
        print("FB posted:", results["facebook"])
    except Exception as e:
        results["facebook_error"] = str(e)
        print("FB error:", e)
    return results
