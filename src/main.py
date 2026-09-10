"""
AravindNews24 — Main Orchestrator
=================================
Runs ONE post cycle. GitHub Actions calls this on the cron schedule.

Split into two phases because Instagram/Facebook's API needs a PUBLIC image
URL, and the rendered image only becomes public after it's pushed to GitHub
(raw.githubusercontent.com serves it immediately after a push — no GitHub
Pages build delay, and no branch/build-type mismatch to worry about):

  render  -> pick story, AI rewrite, generate + render image, save to
             /public, stash post state in data/_pending.json (git commit +
             push happens in the workflow between the two phases)
  publish -> wait for the pushed image to actually be fetchable at its
             public URL, then publish to IG + FB, log analytics, mark posted

Running with no argument does both phases back-to-back (handy for local
testing; the image URL won't be reachable yet in that case, so publish
will fail — that's expected locally, not a bug).
"""

import glob
import json
import os
import re
import sys
import time
import datetime as dt
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from src import (news_engine, ai_writer, image_source, template, reel_template,
                  video, publisher, analytics, whatsapp, telegram_bot)

PENDING_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_pending.json")
WA_QUEUE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_pending.json")
WA_INBOX_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_inbox")
TG_QUEUE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "telegram_pending.json")
TG_INBOX_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "telegram_inbox")
TG_OFFSET_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "telegram_offset.json")

# If a candidate reply is a real video clip instead of a photo, the reel
# is built from the actual footage (src/video.py's render_reel_from_clip)
# instead of holding a still image -- detected purely by file extension
# since that's all a channel (WhatsApp/Telegram/email) attachment gives us.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm"}


def _safe_filename(message_id):
    """WhatsApp message IDs contain '/' and '=' — unsafe as a bare
    filename. The Cloudflare Worker that drops the received photo into
    whatsapp_inbox/ must apply this exact same transform so both sides
    agree on the filename."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", message_id)


def current_slot():
    """Look up which of the fixed daily slots this run corresponds to, by
    IST hour. Exact match for scheduled cron runs; falls back to the
    nearest slot for a manual "Run workflow" click at an odd time."""
    utc = dt.datetime.now(dt.timezone.utc)
    ist = utc + dt.timedelta(hours=5, minutes=30)
    hour = ist.hour
    exact = [s for s in settings.DAILY_SCHEDULE if s[0] == hour]
    if exact:
        return exact[0], ist
    nearest = min(
        settings.DAILY_SCHEDULE,
        key=lambda s: min(abs(s[0] - hour), 24 - abs(s[0] - hour)),
    )
    return nearest, ist


def choose_variant(category_label):
    """Pick which of the 3 templates to render with: whichever is actually
    performing best for this category once there's enough reach data,
    otherwise rotate evenly (least-used-so-far) so all 3 get a fair shot."""
    ranked = analytics.best_template(category_label)
    if ranked:
        return ranked[0]
    counts = analytics.template_counts(category_label)
    return min(counts, key=counts.get)


def choose_reel_variant(category_label):
    """Same idea as choose_variant, but for the 3 dedicated 9:16 reel
    cards (src/reel_template.py) -- a completely separate rotation/ranking
    from the feed card's, since they're different designs with their own
    reach performance."""
    ranked = analytics.best_template(category_label, column="reel_template",
                                      min_samples=3)
    if ranked:
        return ranked[0]
    counts = analytics.template_counts(category_label, column="reel_template",
                                        variants=reel_template.VARIANTS)
    return min(counts, key=counts.get)


def build_caption(written, geo):
    caption = written["caption"].strip()
    tags = " ".join(written["hashtags"])
    loc_line = ""
    if geo and geo.get("place"):
        loc_line = f"\n📍 {geo['place']}"
    return f"{caption}{loc_line}\n\n{tags}\n\n{settings.BRAND_HANDLE}"


def _wait_until_public(url, tries=10, delay=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            print(f"waiting for {url} to go public ({i+1}/{tries}): HTTP {e.code}")
        except Exception as e:
            print(f"waiting for {url} to go public ({i+1}/{tries}): {e}")
        time.sleep(delay)
    return False


def render():
    (slot_hour, window, post_type), ist = current_slot()
    is_reel = post_type == "reel"
    print(f"=== Render at {ist:%Y-%m-%d %H:%M} IST | slot={slot_hour}:00 "
          f"window={window} type={post_type} ===")

    if window == "india":
        primary = "INDIA NEWS"
        fallback = ["HUMAN INTEREST", "BUSINESS NEWS", "SPORTS NEWS", "WORLD NEWS"]
    else:
        primary = "WORLD NEWS"
        fallback = ["HUMAN INTEREST", "BUSINESS NEWS", "INDIA NEWS", "SPORTS NEWS"]

    story = news_engine.pick_top_story(primary, fallback)
    if not story:
        print("No fresh story found. Exiting cleanly.")
        return None

    return _render_story(story, ist, is_reel)


def render_breaking():
    """Checked frequently (every 30 min, separate workflow) so a genuinely
    exceptional story gets posted immediately instead of waiting for the
    Telegram queue's hourly pace -- speed matters for reach on a story
    that's actually breaking. Rate-limited against its OWN clock
    (hours_since_last_breaking_post), not the general posting clock --
    sharing that with the hourly Telegram queue meant this almost never
    fired once the queue reached steady hourly cadence (it kept finding
    well under 1.5h since ANY post and skipping every single check).
    A shared daily cap still guards against the two paths combining past
    Instagram's 25-posts/24h API limit."""
    if news_engine.posts_in_last_24h() >= settings.MAX_POSTS_PER_24H:
        print(f"Already at the {settings.MAX_POSTS_PER_24H}-post/24h cap — skipping breaking check.")
        return None

    gap = news_engine.hours_since_last_breaking_post()
    if gap < settings.BREAKING_MIN_GAP_HOURS:
        print(f"Only {gap:.1f}h since the last BREAKING post (need "
              f"{settings.BREAKING_MIN_GAP_HOURS}h) — skipping breaking check.")
        return None

    story = news_engine.find_breaking_story()
    if not story:
        print("No story clears the breaking-news bar right now. Skipping.")
        return None

    ist = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
    print(f"=== BREAKING at {ist:%Y-%m-%d %H:%M} IST: {story['title']} "
          f"(score {story['score']}) ===")
    return _render_story(story, ist, is_reel=True)  # Reels get more reach


def _load_wa_queue():
    if os.path.exists(WA_QUEUE_PATH):
        with open(WA_QUEUE_PATH) as f:
            return json.load(f)
    return []


def _save_wa_queue(queue):
    os.makedirs(os.path.dirname(WA_QUEUE_PATH), exist_ok=True)
    with open(WA_QUEUE_PATH, "w") as f:
        json.dump(queue, f)


def whatsapp_cycle():
    """Checked every ~30 min. Resolves at most ONE queued candidate per
    run (an image you've replied with takes priority over a grace-period
    text-only fallback) — deliberately one-at-a-time, since the render/
    publish split assumes a single in-flight post, and running this often
    drains a small queue quickly regardless. Then tops the queue back up
    to WHATSAPP_QUEUE_TARGET with fresh candidates."""
    queue = _load_wa_queue()
    # entry["sent_at"] is naive (plain "YYYY-MM-DD HH:MM" text), so now_ist
    # must be naive too before subtracting -- mixing an aware and a naive
    # datetime raises TypeError (same bug already fixed once in
    # news_engine.hours_since_last_post()).
    now_ist = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).replace(tzinfo=None)

    resolved_idx, resolved_kind, inbox_path = None, None, None
    for i, entry in enumerate(queue):
        candidate_path = os.path.join(WA_INBOX_DIR, f"{_safe_filename(entry['message_id'])}.jpg")
        if os.path.exists(candidate_path):
            resolved_idx, resolved_kind, inbox_path = i, "image", candidate_path
            break
    if resolved_idx is None:
        for i, entry in enumerate(queue):
            sent_at = dt.datetime.strptime(entry["sent_at"], "%Y-%m-%d %H:%M")
            age_min = (now_ist - sent_at).total_seconds() / 60
            if age_min >= settings.WHATSAPP_GRACE_MINUTES:
                resolved_idx, resolved_kind = i, "text_only"
                break

    if resolved_idx is not None:
        entry = queue.pop(resolved_idx)
        story = entry["story"]
        if resolved_kind == "image":
            print("Photo received via WhatsApp for:", story["title"])
            _render_story(story, now_ist, is_reel=True, forced_image_path=inbox_path,
                          consumed_inbox_file=inbox_path)
        else:
            # No human reply in time -- try the automated image pipeline
            # (image_source.py) before giving up to text-only. This was
            # deliberately NOT the fallback originally (guaranteed
            # text-only was the whole point of the human-review flow,
            # after repeated mismatched-photo posts eroded trust) -- but
            # that image pipeline has since been substantially hardened
            # (word-boundary entity matching, verified place/person
            # recognition, Wikimedia/Openverse metadata checks), so a
            # missed reply no longer has to mean losing the photo entirely.
            # If it STILL can't find a confident match, text-only remains
            # the guaranteed-safe fallback -- never a guessed stock photo.
            print("No photo within the grace period — trying an automated image match:", story["title"])
            result = _render_story(story, now_ist, is_reel=False)
            if result is None:
                print("No confident image match either — posting text-only:", story["title"])
                _render_story(story, now_ist, is_reel=False, force_no_image=True)

    if len(queue) < settings.WHATSAPP_QUEUE_TARGET:
        exclude_ids = {e["story"]["id"] for e in queue}
        needed = settings.WHATSAPP_QUEUE_TARGET - len(queue)
        for story in news_engine.top_candidates(needed, exclude_ids):
            message_id = whatsapp.send_candidate(story)
            if message_id:
                queue.append({
                    "message_id": message_id,
                    "story": {k: v for k, v in story.items() if k != "published"},
                    "sent_at": now_ist.strftime("%Y-%m-%d %H:%M"),
                })
                print("Sent to WhatsApp:", story["title"])

    _save_wa_queue(queue)


def _load_tg_queue():
    if os.path.exists(TG_QUEUE_PATH):
        with open(TG_QUEUE_PATH) as f:
            return json.load(f)
    return []


def _save_tg_queue(queue):
    os.makedirs(os.path.dirname(TG_QUEUE_PATH), exist_ok=True)
    with open(TG_QUEUE_PATH, "w") as f:
        json.dump(queue, f)


def _load_tg_offset():
    if os.path.exists(TG_OFFSET_PATH):
        with open(TG_OFFSET_PATH) as f:
            return json.load(f).get("offset", 0)
    return 0


def _save_tg_offset(offset):
    os.makedirs(os.path.dirname(TG_OFFSET_PATH), exist_ok=True)
    with open(TG_OFFSET_PATH, "w") as f:
        json.dump({"offset": offset}, f)


def _poll_telegram_replies(pending_ids):
    """Pulls any new Telegram updates since the last run and downloads a
    photo/video for each one that's a reply to a message_id we're still
    tracking. `pending_ids` are the message_ids of candidates currently
    sitting in the queue -- anything else (replies to old/already-resolved
    messages, unrelated chatter) is ignored."""
    offset = _load_tg_offset()
    updates = telegram_bot.get_new_updates(offset)
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message")
        if not msg or str(msg.get("chat", {}).get("id")) != str(settings.TELEGRAM_CHAT_ID):
            continue
        reply_to = msg.get("reply_to_message")
        if not reply_to or reply_to["message_id"] not in pending_ids:
            continue

        # Telegram RE-COMPRESSES anything sent as a regular Photo (re-
        # encoded down to ~1280px, lossy) before the bot ever sees it --
        # that compressed image then has to be scaled UP again to fill the
        # 1080x1350 card, compounding the quality loss. Sending as a File
        # (Telegram's "compression off" option) delivers the original,
        # uncompressed bytes instead, so prefer that whenever it's present.
        doc = msg.get("document")
        file_id = None
        if doc and doc.get("mime_type", "").startswith(("image/", "video/")):
            file_id = doc["file_id"]
        elif msg.get("video"):
            file_id = msg["video"]["file_id"]
        elif msg.get("photo"):
            file_id = msg["photo"][-1]["file_id"]  # last = highest resolution Telegram kept, still recompressed
        if not file_id:
            continue

        dest_no_ext = os.path.join(TG_INBOX_DIR, _safe_filename(str(reply_to["message_id"])))
        saved = telegram_bot.download_file(file_id, dest_no_ext)
        if saved:
            print("Saved Telegram reply media:", saved)
    _save_tg_offset(offset)


def telegram_cycle():
    """Checked every ~30 min -- same design as whatsapp_cycle() (grace
    period, one resolution per run, automated-image-fallback before
    text-only, queue refill), but over the Telegram Bot API: no template-
    approval queue to wait on, and no webhook/Worker needed since
    get_new_updates() is a pull -- GitHub Actions polls it directly."""
    queue = _load_tg_queue()
    now_ist = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).replace(tzinfo=None)

    _poll_telegram_replies({e["message_id"] for e in queue})

    resolved_idx, resolved_kind, inbox_path = None, None, None
    for i, entry in enumerate(queue):
        matches = glob.glob(os.path.join(TG_INBOX_DIR, f"{_safe_filename(str(entry['message_id']))}.*"))
        if matches:
            resolved_idx, resolved_kind, inbox_path = i, "media", matches[0]
            break
    if resolved_idx is None:
        for i, entry in enumerate(queue):
            sent_at = dt.datetime.strptime(entry["sent_at"], "%Y-%m-%d %H:%M")
            age_min = (now_ist - sent_at).total_seconds() / 60
            if age_min >= settings.TELEGRAM_GRACE_MINUTES:
                resolved_idx, resolved_kind = i, "text_only"
                break

    if resolved_idx is not None:
        # A resolved candidate (media received, or grace period expired)
        # still waits here for the shared post-gap gate -- without this,
        # a warm queue resolves on nearly every 30-min run, stacking up
        # to 48 posts/day against Instagram's 25-posts/24h hard cap. Left
        # in the queue (not popped) so it's picked up again once the gap
        # clears, rather than losing the human-provided photo/video.
        gap_h = news_engine.hours_since_last_post()
        at_daily_cap = news_engine.posts_in_last_24h() >= settings.MAX_POSTS_PER_24H
        if at_daily_cap:
            print(f"Candidate resolved but holding -- already at the "
                  f"{settings.MAX_POSTS_PER_24H}-post/24h cap.")
        elif gap_h < settings.TELEGRAM_MIN_POST_GAP_HOURS:
            print(f"Candidate resolved but holding -- last post was {gap_h:.2f}h ago, "
                  f"pacing to ~{settings.TELEGRAM_MIN_POST_GAP_HOURS}h between posts.")
        elif resolved_kind == "text_only" and queue[resolved_idx]["story"]["score"] < settings.MIN_AUTO_POST_SCORE:
            # A real competitor that reached 1M followers posts ~4x/day,
            # not hourly -- their evidence is that a few carefully-selected,
            # genuinely notable stories outperform frequent-but-average
            # ones (Instagram's algorithm tracks engagement rate per post,
            # so a pattern of low-value posts can suppress reach rather
            # than just leave upside on the table). This bar only applies
            # here, not to human-curated media -- someone taking the time
            # to reply with a real photo/video is itself a strong enough
            # signal to trust over the automated score.
            entry = queue.pop(resolved_idx)
            story = entry["story"]
            print(f"Skipping (score {story['score']} < {settings.MIN_AUTO_POST_SCORE}, no human "
                  f"curation to override it) -- not worth a post on its own:", story["title"])
            news_engine.mark_posted(story)
        else:
            entry = queue.pop(resolved_idx)
            story = entry["story"]
            if resolved_kind == "media":
                print("Photo/video received via Telegram for:", story["title"])
                _render_story(story, now_ist, is_reel=True, forced_image_path=inbox_path,
                              consumed_inbox_file=inbox_path)
            else:
                # is_reel=True here too (not just for human-submitted
                # media) -- Reels get more algorithmic reach than static
                # posts, and most candidates end up on this path since
                # replying to every single one isn't realistic, so
                # defaulting it to static was leaving reach on the table
                # for the majority of posts.
                print("No reply within the grace period — trying an automated image match:", story["title"])
                result = _render_story(story, now_ist, is_reel=True)
                if result is None:
                    print("No confident image match either — posting text-only:", story["title"])
                    _render_story(story, now_ist, is_reel=True, force_no_image=True)

    if len(queue) < settings.TELEGRAM_QUEUE_TARGET:
        exclude_ids = {e["story"]["id"] for e in queue}
        needed = settings.TELEGRAM_QUEUE_TARGET - len(queue)
        for story in news_engine.top_candidates(needed, exclude_ids):
            message_id = telegram_bot.send_candidate(story)
            if message_id:
                queue.append({
                    "message_id": message_id,
                    "story": {k: v for k, v in story.items() if k != "published"},
                    "sent_at": now_ist.strftime("%Y-%m-%d %H:%M"),
                })
                print("Sent to Telegram:", story["title"])

    _save_tg_queue(queue)


def _render_story(story, ist, is_reel, forced_image_path=None,
                   force_no_image=False, consumed_inbox_file=None):
    print("Selected:", story["title"], "| score:", story["score"])

    written = ai_writer.rewrite(story)
    print("Headline:", written["headline"])

    category_label = story["category"]
    # Was gated at score >= 60, far too low given the scoring changes made
    # this conversation (incident +20, national-impact +25, corroboration
    # up to +30 all stack) -- 57 of the last 64 real posts got branded
    # BREAKING NEWS this way, most of them routine stories (SC hearings,
    # an IPO listing) that just cleared a low bar. That dilutes the label
    # to the point it stops meaning anything, and buries the real category
    # variety (HUMAN INTEREST never once showed up as a result). Aligned
    # with the actual breaking-news bar (news_engine.BREAKING_SCORE_THRESHOLD)
    # so only genuinely major stories get branded this way.
    if story["score"] >= news_engine.BREAKING_SCORE_THRESHOLD and story.get("hot_hit"):
        category_label = "BREAKING NEWS"

    # A fresh incident (collapse/crash/disaster) almost never has a real,
    # legitimately-licensed photo available yet — forcing a generic stock
    # substitute into that slot is exactly what's been producing mismatches.
    # Use the honest no-photo alert card instead of guessing at one. Decide
    # the variant BEFORE fetching a photo: text_card can also be chosen by
    # the normal rotation for any story, and needs no photo either — no
    # point fetching (or risking failure on) one we won't use.
    if force_no_image:
        variant = "text_card"
    elif forced_image_path:
        # alert_card exists to route around exactly the case where no real,
        # legitimately-licensed photo of THIS specific incident exists yet.
        # Once a human has actually supplied one (via the WhatsApp/Telegram/
        # email review), that reason no longer applies -- use it like any
        # other photo story instead of silently discarding it.
        variant = choose_variant(category_label)
    elif is_incident := story.get("is_incident", False):
        variant = "alert_card"
    else:
        variant = choose_variant(category_label)
    print("Template variant:", variant)

    out_dir = os.path.join(os.path.dirname(__file__), "..", "public")
    os.makedirs(out_dir, exist_ok=True)
    stamp = ist.strftime("%Y%m%d-%H%M")

    # A human-submitted photo (or frame from a submitted video) hasn't been
    # pre-vetted for framing the way a stock photo search result has --
    # cover-crop can cut off ~40%+ of a landscape photo forced into the 4:5
    # card, which reads as an aggressive zoom. Blurred-letterbox instead,
    # but only for these, not for stock photos (template._load_photo only
    # deviates from cover-crop when the aspect mismatch is large anyway).
    smart_fit = forced_image_path is not None

    video_clip_path = None
    if forced_image_path and os.path.splitext(forced_image_path)[1].lower() in VIDEO_EXTENSIONS:
        video_clip_path = forced_image_path
        # the static post JPG still needs a real image, not the video file
        # itself -- pull one representative frame from the clip for it.
        frame_path = os.path.join(out_dir, f"frame-{stamp}.jpg")
        try:
            img_path = video.extract_frame(video_clip_path, frame_path)
        except Exception as e:
            print(f"Could not extract a frame from the submitted video ({e}). "
                  f"Falling back to text-only for this story.")
            video_clip_path = None
            img_path = None
            variant = "text_card"
    else:
        img_path = forced_image_path

    if img_path is None and variant not in ("alert_card", "text_card"):
        try:
            img_path = image_source.get_image(story)
        except Exception as e:
            print(f"No relevant image found ({e}). Skipping this cycle rather than posting a mismatched photo.")
            # mark it handled anyway, so the next cycle moves on to a
            # different story instead of re-picking (and re-failing) this one
            news_engine.mark_posted(story)
            return None

    out_name = f"post-{stamp}.jpg"
    out_path = os.path.join(out_dir, out_name)

    logo = os.path.join(os.path.dirname(__file__), "..", "assets", "logo", "logo.png")
    template.render_post(
        photo_path=img_path,
        category=category_label,
        headline=written["headline"],
        accent_word=written["accent_word"],
        out_path=out_path,
        footer=settings.BRAND_FOOTER,
        handle=settings.BRAND_HANDLE,
        logo_path=logo if os.path.exists(logo) else None,
        variant=variant,
        smart_fit=smart_fit,
    )
    print("Rendered:", out_path)

    if video_clip_path and img_path and os.path.exists(img_path) and img_path != forced_image_path:
        # the extracted-frame JPG was only ever needed to build out_path
        # above -- don't let it pile up in public/ forever like the real
        # published assets (out_name/video_name) are meant to.
        try:
            os.remove(img_path)
        except OSError:
            pass

    video_name = None
    reel_variant_used = None
    if is_reel:
        video_name = f"post-{stamp}.mp4"
        video_path = os.path.join(out_dir, video_name)
        try:
            if video_clip_path:
                # keep the clip's own length unless it's over the threshold,
                # then trim to the target -- NOT a blanket cap (a 90s clip
                # gets cut to 50s, but a 55s clip stays at 55s).
                duration = video.probe_duration(video_clip_path)
                if duration and duration > settings.REEL_CLIP_TRIM_THRESHOLD_SEC:
                    clip_duration = settings.REEL_CLIP_TRIM_TARGET_SEC
                else:
                    clip_duration = duration or settings.REEL_CLIP_TRIM_TARGET_SEC

                overlay_path = os.path.join(out_dir, f"overlay-{stamp}.png")
                reel_template.render_overlay_png(
                    category=category_label,
                    headline=written["headline"],
                    accent_word=written["accent_word"],
                    out_path=overlay_path,
                    footer=settings.BRAND_FOOTER,
                    handle=settings.BRAND_HANDLE,
                    logo_path=logo if os.path.exists(logo) else None,
                )
                video.render_reel_from_clip(video_clip_path, overlay_path, video_path,
                                             clip_duration)
                print(f"Rendered reel from submitted video clip ({clip_duration:.0f}s):", video_path)
                try:
                    os.remove(overlay_path)
                except OSError:
                    pass
            elif img_path is not None:
                reel_variant_used = choose_reel_variant(category_label)
                reel_card_path = os.path.join(out_dir, f"reel-card-{stamp}.jpg")
                reel_template.render_reel_card(
                    photo_path=img_path,
                    category=category_label,
                    headline=written["headline"],
                    accent_word=written["accent_word"],
                    out_path=reel_card_path,
                    footer=settings.BRAND_FOOTER,
                    handle=settings.BRAND_HANDLE,
                    logo_path=logo if os.path.exists(logo) else None,
                    variant=reel_variant_used,
                    smart_fit=smart_fit,
                )
                video.render_reel(reel_card_path, video_path)
                print(f"Rendered reel ({reel_variant_used}):", video_path)
                try:
                    os.remove(reel_card_path)
                except OSError:
                    pass
            else:
                # no-photo variant (text_card/alert_card) -- no dedicated
                # 9:16 equivalent exists for these, hold the already-
                # rendered 4:5 card as-is, same as before this feature.
                video.render_reel(out_path, video_path)
                print("Rendered reel (no-photo card, held as-is):", video_path)
        except Exception as e:
            print("Reel render failed, falling back to static image post:", e)
            is_reel = False
            video_name = None
            reel_variant_used = None

    pending = {
        "story": {k: v for k, v in story.items() if k != "published"},
        "written": written,
        "category_label": category_label,
        "out_name": out_name,
        "is_reel": is_reel,
        "video_name": video_name,
        "template": variant,
        "reel_template": reel_variant_used,
        "ist": ist.strftime("%Y-%m-%d %H:%M"),
        "consumed_inbox_file": consumed_inbox_file,
    }
    os.makedirs(os.path.dirname(PENDING_PATH), exist_ok=True)
    with open(PENDING_PATH, "w") as f:
        json.dump(pending, f)
    print("Saved pending state:", PENDING_PATH)
    return pending


def publish():
    if not os.path.exists(PENDING_PATH):
        print("No pending render — nothing to publish.")
        return
    with open(PENDING_PATH) as f:
        pending = json.load(f)

    story = pending["story"]
    written = pending["written"]
    out_name = pending["out_name"]
    ist = dt.datetime.strptime(pending["ist"], "%Y-%m-%d %H:%M")

    image_base = os.environ.get("GH_PAGES_BASE", "").rstrip("/")
    image_url = f"{image_base}/{out_name}" if image_base else None
    video_url = None
    if pending.get("is_reel") and pending.get("video_name") and image_base:
        video_url = f"{image_base}/{pending['video_name']}"
    caption = build_caption(written, story.get("geo"))

    if image_url:
        print("Waiting for image to go public:", image_url)
        if not _wait_until_public(image_url):
            print("WARNING: image never went public in time, publishing anyway (will likely fail).")
        if video_url:
            print("Waiting for video to go public:", video_url)
            if not _wait_until_public(video_url, tries=15):
                print("WARNING: video never went public in time, publishing anyway (will likely fail).")
        results = publisher.publish_all(image_url, caption, story.get("geo"), video_url=video_url)
    else:
        print("No GH_PAGES_BASE set — skipping publish (dry run).")
        results = {"dry_run": True}

    news_engine.mark_posted(story)
    analytics.log_post(story, written, pending["category_label"], results, ist,
                        is_reel=bool(video_url), template=pending.get("template"),
                        reel_template=pending.get("reel_template"))
    os.remove(PENDING_PATH)
    inbox_file = pending.get("consumed_inbox_file")
    if inbox_file and os.path.exists(inbox_file):
        os.remove(inbox_file)
    print("=== Done ===")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase == "render":
        render()
    elif phase == "render-breaking":
        render_breaking()
    elif phase == "whatsapp-check":
        whatsapp_cycle()
    elif phase == "telegram-check":
        telegram_cycle()
    elif phase == "publish":
        publish()
    else:
        render()
        publish()
