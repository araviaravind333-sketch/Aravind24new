"""
Daily Carousel — Build, Review, Publish
========================================
Owns the one flagship multi-slide post per day: selects the day's
strongest stories, renders each as a numbered slide (src/carousel.py),
sends the whole set to Telegram for review around 20:30 IST, applies
whatever the reviewer did (reject a slide / replace its image or video),
and publishes what's left once the grace period elapses.

Same non-negotiable rule as everywhere else in this pipeline:

    REAL PHOTO (rights-cleared) > NO PHOTO > WRONG PHOTO

so an auto-selected slide only gets a photo when incident_photos'
AUTO_PUBLISH decision says it may lawfully be used. Otherwise the slide
renders text-only until/unless the reviewer supplies their own photo or
video for it -- exactly the same human-in-the-loop pattern the rest of
this project already uses, just applied per-slide instead of per-post.
"""

import datetime as dt
import json
import os

from config import settings
from src import ai_writer, carousel, incident_photos, news_engine, telegram_bot

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(_ROOT, "data", "carousel_pending.json")
SLIDES_DIR_ROOT = os.path.join(_ROOT, "public", "carousel")
INBOX_DIR = os.path.join(_ROOT, "data", "carousel_inbox")

REJECT_WORDS = ("not good", "remove", "delete", "skip", "drop this")


def _load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _one_line(text, max_chars=95):
    text = " ".join((text or "").split())
    return text[:max_chars].rstrip() + ("…" if len(text) > max_chars else "")


def due_for_preview(now_ist):
    """True once, on the first poll at/after the configured preview time,
    for a day that hasn't been built yet. CAROUSEL_FORCE_BUILD=true (set
    by the workflow's force_build manual-dispatch input) bypasses the
    time gate for testing/manual re-runs -- it still won't rebuild a day
    that's already been sent, so it can't clobber a real preview
    mid-review."""
    state = _load_state()
    if state and state.get("date") == now_ist.strftime("%Y-%m-%d"):
        return False
    if os.environ.get("CAROUSEL_FORCE_BUILD", "").lower() == "true":
        return True
    target = now_ist.replace(hour=settings.CAROUSEL_PREVIEW_HOUR_IST,
                              minute=settings.CAROUSEL_PREVIEW_MINUTE_IST,
                              second=0, microsecond=0)
    return now_ist >= target


def build_carousel(now_ist):
    """Selects stories, discovers/renders each slide, persists state, and
    returns it. Does not send anything to Telegram -- see send_preview."""
    date_str = now_ist.strftime("%Y-%m-%d")
    slides_dir = os.path.join(SLIDES_DIR_ROOT, date_str)
    stories = news_engine.top_candidates(settings.CAROUSEL_SLIDE_COUNT)

    slides = []
    for i, story in enumerate(stories, start=1):
        written = ai_writer.rewrite(story)
        subhead = _one_line(story.get("summary") or written.get("caption", ""))
        headline = written["headline"]
        category = story["category"]

        photo_path = None
        try:
            result = incident_photos.find_incident_photo(story)
            if result.get("decision") == "AUTO_PUBLISH" and result.get("image_url"):
                # Rendering our own branded slide over a rights-cleared
                # photo -- never the raw discovered file verbatim -- so
                # the slide fetches it once, locally, to composite.
                dl_path = os.path.join(slides_dir, f"src_{i:02d}.jpg")
                os.makedirs(slides_dir, exist_ok=True)
                photo_path = _download(result["image_url"], dl_path)
        except Exception as e:
            print(f"carousel slide {i}: image discovery failed, going text-only: {e}")

        out_path = os.path.join(slides_dir, f"slide_{i:02d}.jpg")
        carousel.render_carousel_slide(
            photo_path, category, headline, subhead, i, len(stories), out_path,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE,
            smart_fit=False,
        )
        slides.append({
            "index": i,
            "story": {k: v for k, v in story.items() if k != "published"},
            "headline": headline,
            "subhead": subhead,
            "category": category,
            "message_id": None,
            "media_kind": "image",
            "media_source": "auto" if photo_path else "text_only",
            "rendered_image_path": out_path,
            "video_path": None,
            "status": "pending",
        })

    caption = _build_carousel_caption(slides, now_ist)
    state = {
        "date": date_str,
        "status": "collecting",
        "caption": caption,
        "preview_sent_at": None,
        "slides": slides,
    }
    _save_state(state)
    return state


def _build_carousel_caption(slides, now_ist):
    lines = [f"What happened today — {now_ist.strftime('%d %b %Y')}", ""]
    for s in slides:
        lines.append(f"{s['index']:02d}. {s['headline']}")
    lines += ["", "Swipe for the full roundup →", settings.BRAND_HANDLE]
    return "\n".join(lines)


def _download(url, dest_path):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)
    return dest_path


def send_preview(state, now_ist):
    """Sends one summary card, then one photo per slide (reply to any of
    them with a photo/video to replace that slide, or 'not good' to drop
    it). Records each slide's message_id so replies can be matched back."""
    n = len(state["slides"])
    summary = (
        f"\U0001F4F0 Daily carousel ready — {n} slides for {state['date']}\n\n"
        f"Reply to any slide below with a photo/video to use it for that slide, "
        f"or reply \"not good\" to drop it.\n\n"
        f"Auto-publishes in {settings.CAROUSEL_REVIEW_GRACE_MINUTES} min if you don't touch it."
    )
    telegram_bot.send_message(summary)

    for slide in state["slides"]:
        caption = (
            f"{slide['index']:02d}/{n} — {slide['category']}\n{slide['headline']}\n\n"
            + ("(no real photo found — text-only unless you add one)"
               if slide["media_source"] == "text_only" else "")
        )
        # Telegram's sendPhoto needs either a public URL or a direct file
        # upload -- the render is only local at this point (not yet
        # pushed to GitHub Pages), so this always uploads the file
        # directly rather than trying to guess a not-yet-public URL.
        buttons = telegram_bot.build_keyboard([[
            ("❌ NOT GOOD — DROP THIS SLIDE", "cb", f"CDROP|{slide['index']}"),
        ]])
        message_id = telegram_bot.send_photo_file(
            slide["rendered_image_path"], caption, buttons=buttons)
        slide["message_id"] = message_id

    state["status"] = "preview_sent"
    state["preview_sent_at"] = now_ist.strftime("%Y-%m-%d %H:%M")
    _save_state(state)
    return state


def handle_reply(msg, state):
    """One incoming Telegram message during the review window. Matches it
    to a slide by reply_to_message.message_id."""
    reply_to = msg.get("reply_to_message")
    if not reply_to:
        return False
    mid = reply_to.get("message_id")
    slide = next((s for s in state["slides"] if s["message_id"] == mid), None)
    if not slide:
        return False

    text = (msg.get("caption") or msg.get("text") or "").lower()
    if any(w in text for w in REJECT_WORDS):
        slide["status"] = "rejected"
        telegram_bot.reply_to_message(msg["message_id"],
                                       f"Dropped slide {slide['index']:02d} from today's carousel.")
        _save_state(state)
        return True

    file_id = _extract_media_file_id(msg)
    if file_id:
        _apply_owner_media(slide, file_id, msg)
        telegram_bot.reply_to_message(msg["message_id"],
                                       f"Updated slide {slide['index']:02d} with your media.")
        _save_state(state)
        return True

    return False


def handle_callback(cb, state):
    data = cb.get("data") or ""
    if not data.startswith("CDROP|"):
        return False
    idx = int(data.split("|", 1)[1])
    slide = next((s for s in state["slides"] if s["index"] == idx), None)
    if not slide:
        telegram_bot.answer_callback(cb["id"], "Slide not found.")
        return False
    slide["status"] = "rejected"
    _save_state(state)
    telegram_bot.answer_callback(cb["id"], f"Dropped slide {idx:02d}.")
    msg = cb.get("message") or {}
    if msg.get("message_id") and msg.get("chat", {}).get("id"):
        try:
            telegram_bot.edit_caption(msg["chat"]["id"], msg["message_id"],
                                       (msg.get("caption") or "") + "\n\n❌ DROPPED")
        except Exception:
            pass
    return True


def _extract_media_file_id(msg):
    if msg.get("document"):
        return msg["document"]["file_id"]
    if msg.get("video"):
        return msg["video"]["file_id"]
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    return None


def _apply_owner_media(slide, file_id, msg):
    os.makedirs(INBOX_DIR, exist_ok=True)
    dest_no_ext = os.path.join(INBOX_DIR, f"slide_{slide['index']:02d}")
    saved = telegram_bot.download_file(file_id, dest_no_ext)
    if not saved:
        return
    ext = os.path.splitext(saved)[1].lower()
    is_video = ext in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm")
    slides_dir = os.path.dirname(slide["rendered_image_path"])

    if is_video:
        overlay_path = os.path.join(slides_dir, f"overlay_{slide['index']:02d}.png")
        carousel.render_carousel_overlay_png(
            slide["category"], slide["headline"], slide["subhead"],
            slide["index"], slide["index"], overlay_path,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE)
        video_out = os.path.join(slides_dir, f"video_{slide['index']:02d}.mp4")
        carousel.render_carousel_video_slide(saved, overlay_path, video_out)
        # A video slide still needs a static poster image for Facebook's
        # multi-photo carousel, which has no video-child support -- reuse
        # the same overlay, composited on the branded gradient background
        # rather than a raw video frame, so FB's version still looks
        # intentional rather than like a random freeze-frame.
        poster_out = os.path.join(slides_dir, f"slide_{slide['index']:02d}.jpg")
        carousel.render_carousel_slide(
            None, slide["category"], slide["headline"], slide["subhead"],
            slide["index"], slide["index"], poster_out,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE)
        slide["media_kind"] = "video"
        slide["video_path"] = video_out
        slide["rendered_image_path"] = poster_out
    else:
        out_path = slide["rendered_image_path"]
        carousel.render_carousel_slide(
            saved, slide["category"], slide["headline"], slide["subhead"],
            slide["index"], slide["index"], out_path,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE,
            smart_fit=True)
        slide["media_kind"] = "image"
        slide["video_path"] = None

    slide["media_source"] = "owner"
    slide["status"] = "pending"


def ready_to_publish(state, now_ist):
    """CAROUSEL_FORCE_PUBLISH=true (the workflow's force_publish
    manual-dispatch input) bypasses the grace-period wait -- this is a
    REAL publish to Instagram + Facebook the moment it returns True, so
    the workflow only ever sets that input on an explicit human action,
    never automatically."""
    if state["status"] != "preview_sent":
        return False
    if os.environ.get("CAROUSEL_FORCE_PUBLISH", "").lower() == "true":
        return True
    sent_at = dt.datetime.strptime(state["preview_sent_at"], "%Y-%m-%d %H:%M")
    age_min = (now_ist - sent_at).total_seconds() / 60
    return age_min >= settings.CAROUSEL_REVIEW_GRACE_MINUTES


def surviving_slides(state):
    """Kept in original order, renumbered 1..N after drops so the posted
    carousel has no gaps in its badge numbers."""
    kept = [s for s in state["slides"] if s["status"] != "rejected"]
    for i, s in enumerate(kept, start=1):
        s["final_index"] = i
    return kept


def _wait_until_public(url, tries=10, delay=5):
    """Same check as main.py's helper for a regular post -- duplicated
    rather than imported to avoid a circular import (main.py imports this
    module). The carousel's images/videos must actually be reachable at
    their GitHub Pages URL before Graph API can fetch them."""
    import time
    import urllib.error
    import urllib.request
    for i in range(tries):
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            print(f"waiting for {url} to go public ({i + 1}/{tries}): {e}")
        time.sleep(delay)
    return False


def finalize_and_publish(state, now_ist):
    """Publishes whatever survived review: rejected slides dropped,
    everything else in its original order. Every slide's rendered file
    must already be pushed to GitHub Pages (the workflow's git-push step,
    same two-phase render -> push -> publish pattern as every other post
    in this pipeline) before this runs -- that is why this only fires on
    a LATER poll, never in the same run that just rendered anything."""
    from src import publisher

    image_base = os.environ.get("GH_PAGES_BASE", "").rstrip("/")
    kept = surviving_slides(state)
    if not kept:
        state["status"] = "published"
        state["publish_note"] = "no slides survived review -- nothing posted"
        _save_state(state)
        telegram_bot.send_message("Today's carousel had every slide dropped -- nothing was posted.")
        return None

    if not image_base:
        print("No GH_PAGES_BASE set -- skipping carousel publish (dry run).")
        return None

    children = []
    public_root = os.path.join(_ROOT, "public")
    for s in kept:
        # rendered paths are already under public/carousel/<date>/... --
        # build the public URL relative to the repo's public/ root.
        image_rel = os.path.relpath(s["rendered_image_path"], public_root).replace("\\", "/")
        image_url = f"{image_base}/{image_rel}"
        _wait_until_public(image_url)

        if s["media_kind"] == "video" and s.get("video_path"):
            video_rel = os.path.relpath(s["video_path"], public_root).replace("\\", "/")
            video_url = f"{image_base}/{video_rel}"
            _wait_until_public(video_url, tries=15)
            children.append({"type": "VIDEO", "url": video_url, "fb_image_url": image_url})
        else:
            children.append({"type": "IMAGE", "url": image_url, "fb_image_url": image_url})

    results = publisher.publish_carousel_all(children, state["caption"], geo=None)
    state["status"] = "published"
    state["publish_results"] = dict(results)
    _save_state(state)

    ok = "instagram" in results and "facebook" in results
    telegram_bot.send_message(
        f"Daily carousel published — {len(kept)} slide(s)."
        if ok else f"Daily carousel publish had errors: {results}")
    return results
