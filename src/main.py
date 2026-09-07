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
from src import news_engine, ai_writer, image_source, template, video, publisher, analytics

PENDING_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_pending.json")


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

    print("Selected:", story["title"], "| score:", story["score"])

    written = ai_writer.rewrite(story)
    print("Headline:", written["headline"])

    category_label = story["category"]
    if story["score"] >= 60 and story.get("hot_hit"):
        category_label = "BREAKING NEWS"

    # A fresh incident (collapse/crash/disaster) almost never has a real,
    # legitimately-licensed photo available yet — forcing a generic stock
    # substitute into that slot is exactly what's been producing mismatches.
    # Use the honest no-photo alert card instead of guessing at one.
    is_incident = story.get("is_incident", False)
    img_path = None
    if not is_incident:
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

    variant = "alert_card" if is_incident else choose_variant(category_label)
    print("Template variant:", variant)

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
    print("=== Done ===")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase == "render":
        render()
    elif phase == "publish":
        publish()
    else:
        render()
        publish()
