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
from src import news_engine, ai_writer, image_source, template, publisher, analytics

PENDING_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_pending.json")


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
    window, ist = current_window_ist()
    print(f"=== Render at {ist:%Y-%m-%d %H:%M} IST | window={window} ===")

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

    pending = {
        "story": {k: v for k, v in story.items() if k != "published"},
        "written": written,
        "category_label": category_label,
        "out_name": out_name,
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
    caption = build_caption(written, story.get("geo"))

    if image_url:
        print("Waiting for image to go public:", image_url)
        if not _wait_until_public(image_url):
            print("WARNING: image never went public in time, publishing anyway (will likely fail).")
        results = publisher.publish_all(image_url, caption, story.get("geo"))
    else:
        print("No GH_PAGES_BASE set — skipping publish (dry run).")
        results = {"dry_run": True}

    news_engine.mark_posted(story["id"])
    analytics.log_post(story, written, pending["category_label"], results, ist)
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
