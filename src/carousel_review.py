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
from src import (ai_writer, carousel, incident_photos, news_engine, subject_photos,
                 telegram_bot, video)

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


def _all_category_candidates():
    """Every RSS category, unfiltered by time of day -- deliberately NOT
    news_engine.top_candidates()/allowed_categories(), which restrict to
    India-only during India's waking hours for the regular single-story
    posts. The carousel is a "last 24 hours, world + India" roundup by
    request, closer to the reference competitor's own format, so it
    always scans every category regardless of what hour it runs at."""
    out = []
    for category in settings.RSS_FEEDS:
        out.extend(news_engine.fetch_candidates(category))
    return sorted(out, key=lambda s: s["score"], reverse=True)


def _dedupe_by_event(candidates, n):
    """Same event-clustering used for a normal India-hours pull -- see
    the module-level note on why this compares against every keyword set
    SEEN so far, not just survivors (same_event() isn't transitive)."""
    picked, seen_kw, seen_ids = [], [], set()
    for story in candidates:
        if len(picked) >= n:
            break
        if story["id"] in seen_ids:
            continue
        entities = incident_photos.extract_entities(story.get("title", ""), story.get("summary", ""))
        kw = incident_photos.event_keywords(entities)
        if any(incident_photos.same_event(kw, sk) for sk in seen_kw):
            seen_kw.append(kw)
            continue
        seen_ids.add(story["id"])
        story["geo"] = news_engine.detect_geo(story["title"], story["category"])
        picked.append(story)
        seen_kw.append(kw)
    return picked


def _select_distinct_stories(n):
    """news_engine.top_candidates() only dedupes by exact story id, which
    is fine for a single-story post (only the top one gets used) but not
    for a multi-slide roundup: measured on the first live test of this
    feature, 3 of 8 slides turned out to be the same real event (a TMC
    symbol/name dispute) from three different RSS sources, each of which
    independently cleared the score bar and got its own corroboration
    bonus. Overfetches and dedupes by real-world event -- see
    _dedupe_by_event.

    Also guarantees at least one INDIA NEWS story: pulling from every
    category by score alone can produce an all-world (or, as originally
    built, an accidentally all-India) mix -- the account's core audience
    is India-based, so a pure world roundup with zero India content isn't
    actually the ask. If nothing India-related survived on merit, the
    single best India story bumps out the weakest pick rather than being
    silently absent."""
    candidates = _all_category_candidates()
    picked = _dedupe_by_event(candidates, n)

    if not any(s["category"] == "INDIA NEWS" for s in picked):
        india = sorted(news_engine.fetch_candidates("INDIA NEWS"),
                        key=lambda s: s["score"], reverse=True)
        if india:
            top_india = india[0]
            top_india["geo"] = news_engine.detect_geo(top_india["title"], top_india["category"])
            if picked:
                picked[-1] = top_india   # weakest slot gives way, not a random one
            else:
                picked.append(top_india)
    return picked


def build_carousel(now_ist):
    """Selects stories, discovers/renders each slide, persists state, and
    returns it. Does not send anything to Telegram -- see send_preview."""
    date_str = now_ist.strftime("%Y-%m-%d")
    slides_dir = os.path.join(SLIDES_DIR_ROOT, date_str)
    stories = _select_distinct_stories(settings.CAROUSEL_SLIDE_COUNT)

    slides = []
    collage_source_photos = []
    story_categories = []
    for i, story in enumerate(stories, start=1):
        written = ai_writer.rewrite(story)
        subhead = _one_line(story.get("summary") or written.get("caption", ""))
        headline = written["headline"]
        category = story["category"]
        story_categories.append(category)

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
                collage_source_photos.append(dl_path)
        except Exception as e:
            print(f"carousel slide {i}: image discovery failed, going text-only: {e}")

        # No rights-cleared photo of the event -- if the headline names a
        # real, notable person, use their licensed Wikimedia Commons
        # portrait, labelled FILE PHOTO. Otherwise the slide stays a text
        # card. See src/subject_photos.py for the checks that gate this.
        portrait = None
        if photo_path is None:
            try:
                portrait = subject_photos.find_subject_photo(
                    story["title"], story.get("summary", ""), dest_dir=slides_dir)
            except Exception as e:
                print(f"carousel slide {i}: subject portrait lookup failed: {e}")
            if portrait:
                photo_path = portrait["path"]
                collage_source_photos.append(photo_path)

        out_path = os.path.join(slides_dir, f"slide_{i:02d}.jpg")
        carousel.render_carousel_slide(
            photo_path, category, headline, subhead, i, len(stories), out_path,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE,
            file_photo=bool(portrait),
        )
        slides.append({
            "index": i,
            "story": {k: v for k, v in story.items() if k != "published"},
            "headline": headline,
            "subhead": subhead,
            "category": category,
            "message_id": None,
            "media_kind": "image",
            "media_source": ("subject_portrait" if portrait
                             else "auto" if photo_path else "text_only"),
            "rendered_image_path": out_path,
            "video_path": None,
            "status": "pending",
            "is_cover": False,
            "raw_photo_path": photo_path,
            "photo_credit": portrait["attribution"] if portrait else None,
            "photo_subject": portrait["subject"] if portrait else None,
            "rendered_number": i,
        })

    cover_path = os.path.join(slides_dir, "cover.jpg")
    fallback_colors = [carousel.CATEGORY_COLORS.get(c, carousel.ACCENT) for c in story_categories]
    carousel.render_cover_slide(
        "BREAKING", "Have a look at what happened in the world in the last 24 hours",
        now_ist.strftime("%d %b").upper(), collage_source_photos[:4], cover_path,
        footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE,
        fallback_colors=fallback_colors,
    )
    cover_slide = {
        "index": 0,
        "story": None,
        "headline": "", "subhead": "", "category": "",
        "message_id": None, "media_kind": "image", "media_source": "auto",
        "rendered_image_path": cover_path, "video_path": None,
        "status": "pending", "is_cover": True, "raw_photo_path": None,
    }
    slides.insert(0, cover_slide)

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


CAROUSEL_HASHTAGS = "#IndiaNews #WorldNews #NewsUpdate #Trending #AravindNews24"


def _build_carousel_caption(slides, now_ist):
    """Built from the slides that will actually be posted, numbered the way
    they are numbered on the images. The first line is the hook -- it is
    all a viewer sees before tapping 'more' -- and the follow / comment /
    share asks are explicit because a page this small gets almost no
    algorithmic reach without engagement signals, and people rarely act
    without being asked."""
    story_slides = [s for s in slides if not s.get("is_cover")
                    and s.get("status") != "rejected"]
    n = len(story_slides)
    lines = [f"\U0001F4F0 {n} stories the world woke up to today \u2014 {now_ist.strftime('%d %b %Y')}", ""]
    for s in story_slides:
        lines.append(f"{s.get('final_index', s['index']):02d}. {s['headline']}")
    lines += [
        "",
        f"\U0001F449 Follow {settings.BRAND_HANDLE} \u2014 the daily roundup lands every evening at 8:30 PM IST",
        "\U0001F4AC Which story matters most to you? Tell us below",
        "\U0001F501 Send this to someone who misses the news",
    ]
    credits = [f"{s.get('final_index', s['index']):02d} {s['photo_subject']}: {s['photo_credit']}"
               for s in story_slides if s.get("photo_credit")]
    if credits:
        lines += ["", "\U0001F4F7 File photos \u2014 " + " | ".join(credits)]
    lines += ["", CAROUSEL_HASHTAGS]
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
        if slide.get("is_cover"):
            # The cover always runs -- no drop button, since a carousel
            # with a numbered story slide 03 but no opening slide would
            # look broken, not curated. Still replaceable with your own
            # image the same way as any other slide.
            caption = "Cover slide (opens the carousel — reply with a photo to replace it)"
            message_id = telegram_bot.send_photo_file(slide["rendered_image_path"], caption)
            slide["message_id"] = message_id
            continue
        caption = (
            f"{slide['index']:02d}/{n} — {slide['category']}\n{slide['headline']}\n\n"
            + ("(no real photo found — text-only unless you add one)"
               if slide["media_source"] == "text_only" else
               f"(File photo of {slide.get('photo_subject')} — {slide.get('photo_credit')}. "
               f"Reply with your own photo to replace it, or Not good to drop.)"
               if slide["media_source"] == "subject_portrait" else "")
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
        renumber_survivors(state)
        telegram_bot.reply_to_message(msg["message_id"],
                                       f"Dropped slide {slide['index']:02d} from today's carousel.")
        _save_state(state)
        return True

    file_id = _extract_media_file_id(msg)
    if file_id:
        _apply_owner_media(slide, file_id, msg, state)
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
    if slide.get("is_cover"):
        # No drop button is ever sent for the cover, but refuse it here
        # too rather than trust that alone.
        telegram_bot.answer_callback(cb["id"], "The cover slide can't be dropped -- reply with a photo to replace it instead.")
        return False
    slide["status"] = "rejected"
    renumber_survivors(state)
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


def _regenerate_cover(state):
    """Rebuilds the cover's collage from whatever real photos actually
    exist right now (yours, since discovery found none at build time --
    that's why the cover started as a plain gradient). Skips it if you've
    already replied to the cover itself with your own photo -- that
    explicit choice is never overwritten by an automatic rebuild."""
    cover = next((s for s in state["slides"] if s.get("is_cover")), None)
    if not cover or cover.get("media_source") == "owner":
        return
    photos = [s["raw_photo_path"] for s in state["slides"]
              if not s.get("is_cover") and s.get("raw_photo_path")
              and s.get("status") != "rejected"][:4]
    if not photos:
        return
    carousel.render_cover_slide(
        "BREAKING", "Have a look at what happened in the world in the last 24 hours",
        dt.datetime.now().strftime("%d %b").upper(), photos,
        cover["rendered_image_path"],
        footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE)


def _apply_owner_media(slide, file_id, msg, state):
    os.makedirs(INBOX_DIR, exist_ok=True)
    dest_no_ext = os.path.join(INBOX_DIR, f"slide_{slide['index']:02d}")
    saved = telegram_bot.download_file(file_id, dest_no_ext)
    if not saved:
        return
    ext = os.path.splitext(saved)[1].lower()
    is_video = ext in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm")
    slides_dir = os.path.dirname(slide["rendered_image_path"])

    if slide.get("is_cover"):
        # The cover is a graphic (title text + photo band), not a
        # numbered story card -- it has no headline/category to render,
        # and it's a still image by design, so a video reply becomes its
        # single collage photo via a frame grab rather than a playable
        # clip. Re-uses render_cover_slide with the same wording as build,
        # single-photo collage.
        photo_for_cover = saved
        if is_video:
            frame_path = os.path.join(slides_dir, "cover_frame.jpg")
            video.extract_frame(saved, frame_path)
            photo_for_cover = frame_path
        carousel.render_cover_slide(
            "BREAKING", "Have a look at what happened in the world in the last 24 hours",
            dt.datetime.now().strftime("%d %b").upper(), [photo_for_cover],
            slide["rendered_image_path"],
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE)
        slide["media_kind"] = "image"
        slide["video_path"] = None
        slide["raw_photo_path"] = photo_for_cover
        slide["media_source"] = "owner"
        slide["status"] = "pending"
        return

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
        slide["raw_video_path"] = saved
        slide["rendered_image_path"] = poster_out
    else:
        out_path = slide["rendered_image_path"]
        carousel.render_carousel_slide(
            saved, slide["category"], slide["headline"], slide["subhead"],
            slide["index"], slide["index"], out_path,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE,
            # A full crop, not the blurred-letterbox fit -- found from a
            # live review that smart_fit's letterbox produces a visibly
            # ugly, flat blurred band for a real supplied photo whose
            # aspect ratio doesn't match the 4:5 frame (a portrait phone
            # photo, for instance). A clean crop is the standard look for
            # this format (matches the reference post too) and never
            # produces that artefact.
            smart_fit=False)
        slide["media_kind"] = "image"
        slide["video_path"] = None
        slide["raw_photo_path"] = saved

    slide["media_source"] = "owner"
    slide["status"] = "pending"
    slide["photo_credit"] = None
    slide["photo_subject"] = None
    if not slide.get("is_cover") and not is_video:
        # The cover started as a plain gradient because no rights-cleared
        # photo existed at build time -- now that a real photo exists
        # (yours), rebuild the cover's collage from it rather than leaving
        # it blank. Skipped for the cover's OWN reply (already handled
        # above) and for video slides (no still frame worth collaging).
        _regenerate_cover(state)


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


def _rerender_slide(slide, number):
    """Redraws one slide with a new badge number, from the inputs it was
    built from (its raw photo, or none for a text card; the original
    video for a video slide)."""
    out = slide["rendered_image_path"]
    photo = slide.get("raw_photo_path")
    if slide.get("media_kind") == "video" and slide.get("raw_video_path"):
        slides_dir = os.path.dirname(out)
        overlay = os.path.join(slides_dir, f"overlay_{slide['index']:02d}.png")
        carousel.render_carousel_overlay_png(
            slide["category"], slide["headline"], slide["subhead"], number, number, overlay,
            footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE)
        carousel.render_carousel_video_slide(slide["raw_video_path"], overlay, slide["video_path"])
        photo = None   # the poster for a video slide is always a text card
    carousel.render_carousel_slide(
        photo, slide["category"], slide["headline"], slide["subhead"], number, number, out,
        footer=settings.BRAND_FOOTER, handle=settings.BRAND_HANDLE,
        file_photo=slide.get("media_source") == "subject_portrait")


def renumber_survivors(state):
    """After slides are dropped, the badge numbers baked into the images
    must close the gap (01, 02, 03, 05 -> 01, 02, 03, 04) and match the
    caption. Only slides whose number actually changed are redrawn. This
    runs in the same poll that handled the drop, i.e. BEFORE the
    workflow's push step, so the redrawn files are public by publish."""
    kept = surviving_slides(state)
    for s in kept:
        if s.get("is_cover"):
            continue
        n = s["final_index"]
        if s.get("rendered_number") == n:
            continue
        try:
            _rerender_slide(s, n)
            s["rendered_number"] = n
        except Exception as e:
            print(f"could not renumber slide {s['index']} to {n}: {e}")


def surviving_slides(state):
    """Kept in original order, cover always first and always kept
    (never rejectable, see handle_callback), story slides renumbered
    1..N after drops so the posted carousel has no gaps in its badge
    numbers. The cover itself keeps final_index 0 -- it was never
    rendered with a number badge, so it needs none."""
    cover = [s for s in state["slides"] if s.get("is_cover")]
    stories = [s for s in state["slides"] if not s.get("is_cover") and s["status"] != "rejected"]
    for s in cover:
        s["final_index"] = 0
    for i, s in enumerate(stories, start=1):
        s["final_index"] = i
    return cover + stories


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
    missing = []
    public_root = os.path.join(_ROOT, "public")
    for s in kept:
        # rendered paths are already under public/carousel/<date>/... --
        # build the public URL relative to the repo's public/ root.
        image_rel = os.path.relpath(s["rendered_image_path"], public_root).replace("\\", "/")
        image_url = f"{image_base}/{image_rel}"
        if not _wait_until_public(image_url):
            # Found live: a slide's file can go missing from GitHub
            # entirely (a git-add step that silently failed to stage it)
            # while carousel_pending.json still claims it's ready --
            # sending that URL to Meta anyway just produces a confusing
            # 400 from their fetcher. Fail this slide loudly and skip
            # publishing rather than let that happen again.
            missing.append(image_rel)
            continue

        if s["media_kind"] == "video" and s.get("video_path"):
            video_rel = os.path.relpath(s["video_path"], public_root).replace("\\", "/")
            video_url = f"{image_base}/{video_rel}"
            if not _wait_until_public(video_url, tries=15):
                missing.append(video_rel)
                continue
            children.append({"type": "VIDEO", "url": video_url, "fb_image_url": image_url})
        else:
            children.append({"type": "IMAGE", "url": image_url, "fb_image_url": image_url})

    if missing:
        telegram_bot.send_message(
            "Carousel publish stopped -- these files never became public, "
            f"so nothing was sent to Instagram/Facebook: {', '.join(missing)}")
        return None

    # Rebuilt from what is actually being posted -- the caption written at
    # build time still listed slides dropped during review (seen on the
    # first live post: a dropped story's headline was in the caption).
    caption = _build_carousel_caption(kept, now_ist)
    state["caption"] = caption
    results = publisher.publish_carousel_all(children, caption, geo=None)
    any_succeeded = "instagram" in results or "facebook" in results
    # A total failure (both platforms errored) must NOT be marked
    # "published" -- that would permanently block retrying and misreport
    # what actually happened. Only advance status once at least one
    # platform genuinely posted; a full failure leaves review state
    # intact so the exact same reviewed slides can be retried.
    state["status"] = "published" if any_succeeded else "preview_sent"
    state["publish_results"] = dict(results)
    _save_state(state)

    both_ok = "instagram" in results and "facebook" in results
    if both_ok:
        telegram_bot.send_message(f"Daily carousel published — {len(kept)} slide(s).")
    elif any_succeeded:
        telegram_bot.send_message(f"Daily carousel posted to one platform only: {results}")
    else:
        telegram_bot.send_message(
            f"Daily carousel publish FAILED on both platforms, nothing went live: {results}")
    return results
