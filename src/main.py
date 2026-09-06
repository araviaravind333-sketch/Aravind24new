"""
AravindNews24 — Main Orchestrator
=================================
Runs ONE post cycle. GitHub Actions calls this on the cron schedule.

Flow:
  1. Decide window (India-first 5am-11pm IST, World-first 11pm-5am IST)
  2. Pick top trending story (last 24h, de-duped)
  3. AI rewrite -> headline + accent + caption + hashtags
  4. Get image (AI first, stock fallback)
  5. Render branded post
  6. Save to /public (published by GitHub Pages -> gives public URL)
  7. Publish to IG + FB with geo tag
  8. Log for analytics
"""

import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from src import news_engine, ai_writer, image_source, template, publisher, analytics


def current_window_ist():
    utc = dt.datetime.now(dt.timezone.utc)
    ist = utc + dt.timedelta(hours=5, minutes=30)
    h = ist.hour
    if settings.INDIA_WINDOW_START_IST <= h < settings.INDIA_WINDOW_END_IST:
        return "india", ist
    return "world", ist


def build_caption(written, geo):
    caption = written["caption"].strip()
    tags = " ".join(written["hashtags"])
    loc_line = ""
    if geo and geo.get("place"):
        loc_line = f"\n📍 {geo['place']}"
    return f"{caption}{loc_line}\n\n{tags}\n\n{settings.BRAND_HANDLE}"


def run_once():
    window, ist = current_window_ist()
    print(f"=== Run at {ist:%Y-%m-%d %H:%M} IST | window={window} ===")

    if window == "india":
        primary = "INDIA NEWS"
        fallback = ["BUSINESS NEWS", "SPORTS NEWS", "WORLD NEWS"]
    else:
        primary = "WORLD NEWS"
        fallback = ["BUSINESS NEWS", "INDIA NEWS", "SPORTS NEWS"]

    story = news_engine.pick_top_story(primary, fallback)
    if not story:
        print("No fresh story found. Exiting cleanly.")
        return

    print("Selected:", story["title"], "| score:", story["score"])

    written = ai_writer.rewrite(story)
    print("Headline:", written["headline"])

    # category label on image: BREAKING if very high score
    category_label = story["category"]
    if story["score"] >= 60:
        category_label = "BREAKING NEWS"

    img_path = image_source.get_image(story)

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
    )
    print("Rendered:", out_path)

    # public URL via GitHub Pages (set GH_PAGES_BASE in env)
    pages_base = os.environ.get("GH_PAGES_BASE", "").rstrip("/")
    image_url = f"{pages_base}/{out_name}" if pages_base else None

    caption = build_caption(written, story.get("geo"))

    if image_url:
        results = publisher.publish_all(image_url, caption, story.get("geo"))
    else:
        print("No GH_PAGES_BASE set — skipping publish (dry run).")
        results = {"dry_run": True}

    news_engine.mark_posted(story["id"])
    analytics.log_post(story, written, category_label, results, ist)
    print("=== Done ===")


if __name__ == "__main__":
    run_once()
