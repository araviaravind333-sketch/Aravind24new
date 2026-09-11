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
# 0. GLOBAL MODE
# ============================================================
# By explicit request, after repeated real-world image/news mismatches
# damaged trust with actual followers (friends/relatives specifically
# flagged it): EVERY post, from every path (scheduled, breaking, human-
# curated Telegram reply, automated fallback), renders as the text_card
# variant -- no photo or video ever gets attached to a post, period, even
# if a human replied with one. Enforced in a single place
# (main.py's _render_story()) so it can't be bypassed by any caller.
# Flip back to False to restore normal photo/video behavior.
TEXT_ONLY_MODE = True


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
# 1c. TELEGRAM (candidate-review queue — same idea as WhatsApp above,
#     but no template-approval queue and no webhook/Worker needed: this
#     is now the primary channel)
# ============================================================
TELEGRAM_BOT_TOKEN    = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID      = _env("TELEGRAM_CHAT_ID")
TELEGRAM_GRACE_MINUTES = 20
TELEGRAM_QUEUE_TARGET  = 5
# telegram_cycle() runs every 30 min and (with a short grace period and a
# warm queue) can resolve -- and therefore post -- on nearly every run.
# Left unchecked that's up to 48 posts/day stacked on TOP of the old fixed
# 8/day schedule (post.yml, now retired for this reason) -- Instagram's
# Graph API hard-caps publishing at 25 posts/24h, so anything past that
# just fails outright. This makes the Telegram queue self-pace to roughly
# hourly instead, using the same "time since last post" signal the
# breaking-news path already shares (news_engine.hours_since_last_post()).
TELEGRAM_MIN_POST_GAP_HOURS = 1.0
# Analyzed a real competitor that reached 1M followers (@worldinlast24hrs):
# they post ~4x/day, not hourly, and their evidence is that a handful of
# carefully-selected, genuinely notable stories outperforms frequent-but-
# average ones -- Instagram's algorithm tracks engagement rate per post,
# so a pattern of low-value posts can actively suppress reach, not just
# leave upside on the table. This only gates the AUTOMATED fallback path
# (no human reply) -- calibrated against a real live batch of candidates
# (scores clustered 51-79, with the standout stories at 71+); a person
# choosing to reply with a real photo/video is itself a strong enough
# signal to trust regardless of this number.
MIN_AUTO_POST_SCORE = 65

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
    # Added after analyzing 7 competitor accounts: wire-service breaking
    # news (what the other 4 categories above are) tops out at a modest
    # reach ceiling -- the highest ENGAGEMENT-RATE posts among competitors
    # (7-9%, vs ~1-2% on their hard news) were soft human-interest moments
    # that BBC/CNN/Hindu/NDTV's main feeds structurally don't carry. These
    # two feeds fill that specific gap.
    "HUMAN INTEREST": [
        "https://feeds.feedburner.com/ndtvnews-offbeat-news",
        "https://thebetterindia.com/rss",
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
# worth posting immediately rather than waiting for the Telegram queue's
# hourly pace -- speed matters for reach on something actually breaking.
# Rate-limited against hours_since_last_BREAKING_post specifically (its
# own clock, not the general one -- see news_engine.py), so it can't
# quietly starve out from sharing a clock with the hourly regular queue,
# which is exactly what was happening before this was split out.
BREAKING_MIN_GAP_HOURS = 1.5

# Shared safety valve across every posting path (regular queue + breaking
# fast path): Instagram's Graph API hard-caps content publishing at 25
# posts/24h -- past that, posts just fail outright. Kept a little under
# that hard cap as headroom.
MAX_POSTS_PER_24H = 22

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
    "HUMAN INTEREST": ["#GoodNews", "#HumanInterest", "#viral", "#AravindNews24"],
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
# True 9:16 Reels aspect ratio -- reels get their own dedicated templates
# (src/reel_template.py) rendered natively at this size, separate from the
# 4:5 feed card (src/template.py), which is still used unchanged for the
# Facebook photo post and for non-reel Instagram posts.
REEL_WIDTH      = 1080
REEL_HEIGHT     = 1920
# A reel built from a real submitted video clip (as opposed to a static
# image held still) uses the clip's own length as-is UNLESS it exceeds
# REEL_CLIP_TRIM_THRESHOLD_SEC, in which case it's trimmed down to
# REEL_CLIP_TRIM_TARGET_SEC -- not simply capped at the target, so a
# 55-second clip stays at 55s but a 90-second one gets trimmed to 50s.
REEL_CLIP_TRIM_THRESHOLD_SEC = 60
REEL_CLIP_TRIM_TARGET_SEC = 50

# ============================================================
# 9. ANALYTICS / GROWTH
# ============================================================
# After each post, we pull insights and log to /data/performance.csv
# The selector uses this history to prefer high-performing categories/times.
ANALYTICS_LOOKBACK_DAYS = 14
