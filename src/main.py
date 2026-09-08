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

import json
import os
import sys
import time
import datetime as dt
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from src import news_engine, ai_writer, image_source, template, video, publisher, analytics, whatsapp

PENDING_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_pending.json")
WA_QUEUE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_pending.json")
WA_INBOX_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_inbox")


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
        fallback = ["BUSINESS NEWS", "SPORTS NEWS", "WORLD NEWS"]
    else:
        primary = "WORLD NEWS"
        fallback = ["BUSINESS NEWS", "INDIA NEWS", "SPORTS NEWS"]

    story = news_engine.pick_top_story(primary, fallback)
    if not story:
        print("No fresh story found. Exiting cleanly.")
        return None

    return _render_story(story, ist, is_reel)


def render_breaking():
    """Checked frequently (every 30 min, separate workflow) so a genuinely
    exceptional story gets posted immediately instead of waiting for the
    next fixed slot (up to ~3h away) — speed matters for reach on a story
    that's actually breaking. Deliberately strict and rate-limited so this
    doesn't quietly turn into extra posting frequency on a young account
    (the growth plan is explicit that over-posting risks a spam flag)."""
    gap = news_engine.hours_since_last_post()
    if gap < settings.BREAKING_MIN_GAP_HOURS:
        print(f"Only {gap:.1f}h since the last post (need "
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
    now_ist = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)

    resolved_idx, resolved_kind, inbox_path = None, None, None
    for i, entry in enumerate(queue):
        candidate_path = os.path.join(WA_INBOX_DIR, f"{entry['message_id']}.jpg")
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
            print("No photo within the grace period — posting text-only:", story["title"])
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


def _render_story(story, ist, is_reel, forced_image_path=None,
                   force_no_image=False, consumed_inbox_file=None):
    print("Selected:", story["title"], "| score:", story["score"])

    written = ai_writer.rewrite(story)
    print("Headline:", written["headline"])

    category_label = story["category"]
    if story["score"] >= 60 and story.get("hot_hit"):
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
    elif is_incident := story.get("is_incident", False):
        variant = "alert_card"
    else:
        variant = choose_variant(category_label)
    print("Template variant:", variant)

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

    out_dir = os.path.join(os.path.dirname(__file__), "..", "public")
    os.makedirs(out_dir, exist_ok=True)
    stamp = ist.strftime("%Y%m%d-%H%M")
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
    )
    print("Rendered:", out_path)

    video_name = None
    if is_reel:
        video_name = f"post-{stamp}.mp4"
        video_path = os.path.join(out_dir, video_name)
        try:
            video.render_reel(out_path, video_path)
            print("Rendered reel:", video_path)
        except Exception as e:
            print("Reel render failed, falling back to static image post:", e)
            is_reel = False
            video_name = None

    pending = {
        "story": {k: v for k, v in story.items() if k != "published"},
        "written": written,
        "category_label": category_label,
        "out_name": out_name,
        "is_reel": is_reel,
        "video_name": video_name,
        "template": variant,
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
                        is_reel=bool(video_url), template=pending.get("template"))
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
    elif phase == "publish":
        publish()
    else:
        render()
        publish()
