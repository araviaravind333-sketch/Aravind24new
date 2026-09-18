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
import urllib.error

from config import settings

V = settings.GRAPH_VERSION
BASE = f"https://graph.facebook.com/{V}"


def _raise_with_body(e, url):
    """Bare urllib.error.HTTPError str()s down to 'HTTP Error 400: Bad
    Request' -- the actual reason (Meta's Graph API always explains what
    was wrong in the response body) was being thrown away everywhere this
    was called, which is how a real publish failure surfaced as nothing
    more useful than 'HTTP Error 400: Bad Request' with no way to
    diagnose it. Re-raises with the body attached."""
    try:
        body = e.read().decode("utf-8", errors="replace")
    except Exception:
        body = "<no response body>"
    raise RuntimeError(f"{e} for {url} -- {body}") from e


def _post(url, params):
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        _raise_with_body(e, url)


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        _raise_with_body(e, url)


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


# ---------- Instagram Reels ----------
def publish_instagram_reel(video_url, caption, place=None):
    token = settings.META_PAGE_ACCESS_TOKEN
    ig_id = settings.IG_USER_ID
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token,
    }
    loc = _find_location_id(place)
    if loc:
        params["location_id"] = loc

    container = _post(f"{BASE}/{ig_id}/media", params)
    cid = container.get("id")
    if not cid:
        raise RuntimeError(f"IG reel container failed: {container}")

    # video processing takes longer than a photo container
    status = {}
    for _ in range(30):
        status = _get(f"{BASE}/{cid}", {
            "fields": "status_code,status", "access_token": token})
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise RuntimeError(f"IG reel processing failed: {status}")
        time.sleep(10)
    else:
        raise RuntimeError(f"IG reel container never finished processing: {status}")

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


# ---------- Instagram carousel (daily flagship multi-slide post) ----------
def publish_instagram_carousel(children, caption, place=None):
    """children: list of {"type": "IMAGE"|"VIDEO", "url": <public url>},
    in the order they should appear as slides. Each becomes its own
    'carousel item' child container first; those ids then get bundled
    into one parent container and published as a single post -- this is
    a genuinely different Graph API flow from a normal single-media post
    (publish_instagram/publish_instagram_reel above), not an extension of
    it, per Meta's own carousel documentation."""
    token = settings.META_PAGE_ACCESS_TOKEN
    ig_id = settings.IG_USER_ID
    child_ids = []
    for child in children:
        params = {"is_carousel_item": "true", "access_token": token}
        if child["type"] == "VIDEO":
            params["media_type"] = "VIDEO"
            params["video_url"] = child["url"]
        else:
            params["image_url"] = child["url"]
        container = _post(f"{BASE}/{ig_id}/media", params)
        cid = container.get("id")
        if not cid:
            raise RuntimeError(f"IG carousel child container failed: {container}")
        if child["type"] == "VIDEO":
            status = {}
            for _ in range(30):
                status = _get(f"{BASE}/{cid}", {"fields": "status_code,status", "access_token": token})
                if status.get("status_code") == "FINISHED":
                    break
                if status.get("status_code") == "ERROR":
                    raise RuntimeError(f"IG carousel video child failed: {status}")
                time.sleep(10)
            else:
                raise RuntimeError(f"IG carousel video child never finished: {status}")
        child_ids.append(cid)

    parent = _post(f"{BASE}/{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
        "access_token": token,
    })
    pcid = parent.get("id")
    if not pcid:
        raise RuntimeError(f"IG carousel parent container failed: {parent}")

    for _ in range(10):
        status = _get(f"{BASE}/{pcid}", {"fields": "status_code", "access_token": token})
        if status.get("status_code") == "FINISHED":
            break
        time.sleep(5)

    return _post(f"{BASE}/{ig_id}/media_publish", {"creation_id": pcid, "access_token": token})


# ---------- Facebook multi-photo post (Facebook's nearest equivalent) ----------
def publish_facebook_carousel(image_urls, caption, place=None):
    """Facebook's Graph API has no true 'carousel' concept for organic
    Page posts, and its multi-photo flow (attached_media) only accepts
    IMAGES, not video children -- unlike Instagram. So this always
    receives plain image URLs: for any slide whose real content is a
    video, the caller passes that slide's static branded poster image
    instead (the same overlay graphic, just not the playing clip). This
    is a genuine, disclosed platform gap, not a bug -- Facebook viewers
    see a still frame for a video slide; Instagram viewers see the real
    video. Each photo is uploaded unpublished first, then attached
    together to one feed post."""
    token = settings.META_PAGE_ACCESS_TOKEN
    page_id = settings.FB_PAGE_ID
    photo_ids = []
    for url in image_urls:
        res = _post(f"{BASE}/{page_id}/photos", {
            "url": url, "published": "false", "access_token": token,
        })
        pid = res.get("id")
        if not pid:
            raise RuntimeError(f"FB unpublished photo upload failed: {res}")
        photo_ids.append(pid)

    attached = json.dumps([{"media_fbid": pid} for pid in photo_ids])
    params = {
        "message": caption,
        "attached_media": attached,
        "access_token": token,
    }
    loc = _find_location_id(place)
    if loc:
        params["place"] = loc
    return _post(f"{BASE}/{page_id}/feed", params)


def publish_carousel_all(children, caption, geo):
    """children: list of {"type": "IMAGE"|"VIDEO", "url": <ig url>,
    "fb_image_url": <always-a-static-image url for the FB fallback>}."""
    place = None
    if geo:
        place = "India" if geo.get("is_india") else geo.get("place")
    results = {}
    try:
        ig_children = [{"type": c["type"], "url": c["url"]} for c in children]
        results["instagram"] = publish_instagram_carousel(ig_children, caption, place)
        print("IG carousel posted:", results["instagram"])
    except Exception as e:
        results["instagram_error"] = str(e)
        print("IG carousel error:", e)
    try:
        fb_images = [c["fb_image_url"] for c in children]
        results["facebook"] = publish_facebook_carousel(fb_images, caption, place)
        print("FB carousel posted:", results["facebook"])
    except Exception as e:
        results["facebook_error"] = str(e)
        print("FB carousel error:", e)
    return results


def publish_all(image_url, caption, geo, video_url=None):
    place = None
    if geo:
        place = "India" if geo.get("is_india") else geo.get("place")
    results = {}
    try:
        if video_url:
            results["instagram"] = publish_instagram_reel(video_url, caption, place)
        else:
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
