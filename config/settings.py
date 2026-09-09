"""
AravindNews24 — Central Configuration
=====================================
ALL secrets are read from environment variables (GitHub Actions Secrets).
NEVER hardcode keys here. This is the ONE file to edit when you change
schedule, hashtags, or behaviour.

To add a secret: GitHub repo -> Settings -> Secrets and variables ->
Actions -> New repository secret.
"""

import os


def _env(key, default=None, required=False):
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"Missing required secret: {key}")
    return val


# ============================================================
# 1. META / GRAPH API CREDENTIALS  (Instagram + Facebook Page)
# ============================================================
# One long-lived Page token covers BOTH the FB Page and the linked IG account.
META_PAGE_ACCESS_TOKEN = _env("META_PAGE_ACCESS_TOKEN")   # long-lived (60d, auto-refresh)
META_APP_ID            = _env("META_APP_ID")
META_APP_SECRET        = _env("META_APP_SECRET")
FB_PAGE_ID             = _env("FB_PAGE_ID")               # 61592722090173
IG_USER_ID             = _env("IG_USER_ID")               # numeric IG business account id
GRAPH_VERSION          = "v21.0"

# ============================================================
# 1b. WHATSAPP (candidate-review queue — human picks the photo)
# ============================================================
# Every ~30 min, up to WHATSAPP_QUEUE_TARGET candidate stories get sent to
# your own WhatsApp. Reply to one with a photo and that photo gets used
# for the post. No reply within WHATSAPP_GRACE_MINUTES -> posts anyway,
# text-only (no guessed stock photo).
WHATSAPP_ACCESS_TOKEN       = _env("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID    = _env("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_RECIPIENT          = _env("WHATSAPP_RECIPIENT")     # your own number, no '+' (e.g. 918838374404)
WHATSAPP_GRACE_MINUTES      = 45
WHATSAPP_QUEUE_TARGET       = 5
# A business-initiated message outside an active 24h customer-service
# session can ONLY be delivered as a pre-approved template -- free-form
# text still returns a message id from the API but is silently dropped.
# This template was submitted for review via the Graph API and must show
# status APPROVED (check: GET /{template_id}?fields=status) before sends
# will actually reach WhatsApp.
WHATSAPP_TEMPLATE_NAME      = "news_pipeline_status_v1"
WHATSAPP_TEMPLATE_LANGUAGE  = "en_US"

# ============================================================
# 2. IMAGE PIPELINE
# ============================================================
# Real stock photos only (keyword-searched from the headline) — no AI image
# generation, so what gets posted is always an actual photograph.
PEXELS_API_KEY         = _env("PEXELS_API_KEY")     # free, unlimited-ish
UNSPLASH_ACCESS_KEY    = _env("UNSPLASH_ACCESS_KEY")# free, 50/hr

# ============================================================
# 3. NEWS SOURCES  (RSS — free, legal to summarise facts)
# ============================================================
# We read RSS for FACTS, then AI REWRITES headline+caption in our voice.
# We never copy article text or use publishers' photos.
RSS_FEEDS = {
    "INDIA NEWS": [
        "https://www.thehindu.com/news/national/feeder/default.rss",
        "https://feeds.feedburner.com/ndtvnews-india-news",
        "https://www.indiatoday.in/rss/1206578",
    ],
    "WORLD NEWS": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.thehindu.com/news/international/feeder/default.rss",
        "http://rss.cnn.com/rss/edition_world.rss",
    ],
    "BUSINESS NEWS": [
        "https://www.thehindu.com/business/feeder/default.rss",
        "https://feeds.feedburner.com/ndtvprofit-latest",
    ],
    "SPORTS NEWS": [
        "https://www.thehindu.com/sport/feeder/default.rss",
        "https://feeds.bbci.co.uk/sport/rss.xml",
    ],
}

# ============================================================
# 4. SCHEDULE  (all times IST; GitHub cron is UTC — workflow handles offset)
# ============================================================
# Fixed daily plan: 6 India slots (4 static posts + 2 Reels) + 2 World slots
# (1 post + 1 Reel) = 8 posts/day, deterministic (not a random roll).
# Each entry here needs a matching cron line in .github/workflows/post.yml —
# keep them in sync if you edit this. (IST hour, window, "post"/"reel")
DAILY_SCHEDULE = [
    (2,  "world", "post"),
    (5,  "india", "post"),
    (8,  "india", "reel"),
    (11, "india", "post"),
    (14, "india", "post"),
    (17, "india", "reel"),
    (20, "india", "post"),
    (23, "world", "reel"),
]

# Checked every 30 min (separate workflow) for a story so exceptional it's
# worth posting immediately rather than waiting for the next fixed slot
# (up to ~3h away) — speed matters for reach on something actually
# breaking. Rate-limited so this can't quietly turn into extra posting
# frequency: the growth plan is explicit that over-posting on a young
# account risks a spam flag, so this only ever ADDS a post when the last
# one (scheduled or breaking) was genuinely a while ago.
BREAKING_MIN_GAP_HOURS = 1.5

# ============================================================
# 5. GEO-TAGGING
# ============================================================
DEFAULT_INDIA_LOCATION = "India"          # fallback IG location search term
# For foreign news, we detect the country from the story and tag its capital/city.

# ============================================================
# 6. HASHTAG STRATEGY  (auto-selected per category + reach tier)
# ============================================================
# 4 tags per post, mixed: 1 broad + 1 niche + 1 trending + 1 brand.
HASHTAG_BANK = {
    "INDIA NEWS":    ["#IndiaNews", "#BreakingNews", "#news", "#AravindNews24"],
    "WORLD NEWS":    ["#WorldNews", "#GlobalNews", "#news", "#AravindNews24"],
    "BREAKING NEWS": ["#BreakingNews", "#news", "#trending", "#AravindNews24"],
    "SPORTS NEWS":   ["#SportsNews", "#sports", "#news", "#AravindNews24"],
    "BUSINESS NEWS": ["#BusinessNews", "#economy", "#news", "#AravindNews24"],
}
BRAND_HASHTAG = "#AravindNews24"

# ============================================================
# 7. BRAND
# ============================================================
BRAND_HANDLE = "@aravindnews24"
BRAND_FOOTER = "For the latest news"
IG_PROFILE   = "https://www.instagram.com/aravindnews24/"

# ============================================================
# 8. REELS
# ============================================================
# Reel vs static is now fixed by DAILY_SCHEDULE above, not a random roll.
# The card is held still, silent, no zoom/pan/audio (by request).
REEL_DURATION_SEC = 6
REEL_WIDTH      = 1080   # matches the static card exactly (4:5) — no aspect
REEL_HEIGHT     = 1350   # mismatch/distortion risk from forcing 9:16 here

# ============================================================
# 9. ANALYTICS / GROWTH
# ============================================================
# After each post, we pull insights and log to /data/performance.csv
# The selector uses this history to prefer high-performing categories/times.
ANALYTICS_LOOKBACK_DAYS = 14
