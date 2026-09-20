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
# Emergency kill-switch: forces EVERY post, from every path, to render as
# the text_card variant -- no photo or video ever attached, period, even
# one a human personally supplied. Was turned on after repeated real-
# world image mismatches damaged trust with actual followers, but the
# actual repeat offender turned out to be the automated stock-photo
# search fallback (removed from telegram_cycle() -- a missed reply now
# always goes text-only, no guessing), not human-curated photos. A photo
# or video the user personally replies with (or attaches to their own
# breaking-news link submission) is a fundamentally different, mismatch-
# free thing: a person looked at it and chose it. Back to False so that
# path works normally again. Flip back to True only if a real mismatch
# problem returns.
TEXT_ONLY_MODE = False


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
TELEGRAM_MIN_POST_GAP_HOURS = 3.0
# Analyzed a real competitor that reached 1M followers (@worldinlast24hrs):
# they post ~4x/day, not hourly, and their evidence is that a handful of
# carefully-selected, genuinely notable stories outperforms frequent-but-
# average ones -- Instagram's algorithm tracks engagement rate per post,
# so a pattern of low-value posts can actively suppress reach, not just
# leave upside on the table. This only gates the AUTOMATED fallback path
# (no human reply) -- a person choosing to reply with a real photo/video
# is itself a strong enough signal to trust regardless of this number.
#
# Raised 65 -> 75 -> 80 by explicit request: 65 wasn't actually selective
# (the account was hitting MAX_POSTS_PER_24H's ceiling nearly every day,
# ~22-24 posts/day, about triple the cadence of the account that actually
# reached 1M); 80 tightens further still, closer to only the standouts.
MIN_AUTO_POST_SCORE = 95

# By request: during India's waking hours only India news goes out; world/
# sports/business are allowed overnight, when the Indian audience is
# asleep anyway. Window is inclusive of the start hour and exclusive of
# the end hour, in IST (05:00-22:59 -> India only; 23:00-04:59 -> all).
INDIA_ONLY_START_HOUR_IST = 5
INDIA_ONLY_END_HOUR_IST   = 23
INDIA_ONLY_CATEGORIES     = ("INDIA NEWS",)

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
# posts/24h -- past that, posts just fail outright.
#
# Lowered from 22 -> 12: the account was hitting 22 nearly every single
# day, roughly triple the ~4x/day cadence of the account that actually
# reached 1M followers -- that's routine automated posts crowding out
# quality, not a technical ceiling worth maxing out.
MAX_POSTS_PER_24H = 8
# Reserved specifically for the user's OWN urgent breaking-news
# submissions (a link + their own photo/video, sent via Telegram).
# Without this, routine automated posts earlier in the day can use up
# the whole daily budget before the user's hand-picked urgent post even
# gets a chance -- exactly what happened twice in real testing. The
# regular queue and the automated breaking-news path stop this many
# short of the full ceiling; only the user's own urgent-submission path
# can use the full ceiling, including this reserve.
MAX_URGENT_RESERVED_SLOTS = 2

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
# ============================================================
# 10. INCIDENT PHOTO INTELLIGENCE
#     (src/incident_photos.py + src/photo_review.py + src/dashboard.py)
# ============================================================
# The rule this whole subsystem exists to enforce:
#     REAL INCIDENT PHOTO  >  GENERIC PHOTO
#     NO PHOTO             >  WRONG PHOTO
# and, kept strictly separate from both of those:
#     "is this really that event?"  !=  "may we republish it?"
#
# Measured on 14 live stories from this project's own feeds: 9 exact
# incident photos found, 0 wrong photos selected, 0 auto-publishable
# (every hit was publisher-copyrighted). Discovery is solved; rights are
# the wall. Everything below is aimed at that wall -- finding a DIFFERENT,
# legitimately reusable photograph of the SAME event.

# How many candidates to evaluate per story before giving up. Each one
# costs an HTTP request or two against free, keyless APIs, so this is a
# politeness/runtime budget, not a billing one. Search stops early the
# moment a genuinely reusable photo is verified -- no point spending the
# rest of the budget once the question is answered.
PHOTO_SEARCH_BUDGET = 12
# Deep alternative search (Step 7-10) only runs when the first verified
# photo turns out to be rights-restricted. If the first hit is already
# free to reuse there is nothing to look for.
PHOTO_DEEP_SEARCH = True

# Retry ladder, in minutes from first discovery, for stories that came
# back NO_VERIFIED_IMAGE. Incident photographs are very often published
# hours after the first text report -- a 10:00 "no image" is not a
# permanent verdict. Retries stop immediately once a verified REUSABLE
# photo is found (a verified copyrighted one keeps retrying, since the
# whole point is to find a usable alternative).
PHOTO_RETRY_LADDER_MIN = (10, 30, 60, 180, 360, 720)

# Default MUST stay True. False would allow a clearly-labelled generic
# illustration when nothing real exists -- off unless deliberately
# enabled, and never AI-generated imagery of a real incident under any
# setting. There is no setting that turns that on.
PHOTO_REAL_IMAGES_ONLY = True

# Where the candidate history lives. The .db is DERIVED and gitignored;
# photo_log.jsonl is the durable, git-mergeable record it is rebuilt
# from (see src/photo_db.py for why a committed binary would break the
# workflows' rebase-and-retry push loop).
PHOTO_DB_PATH  = "data/photos.db"
PHOTO_LOG_PATH = "data/photo_log.jsonl"
DASHBOARD_PATH = "public/dashboard/index.html"


# ============================================================
# 11. DAILY CAROUSEL  (one flagship multi-slide post, competitor-style)
# ============================================================
# By explicit request: the account posts far fewer automated pieces per
# day (see MIN_AUTO_POST_SCORE / MAX_POSTS_PER_24H above), but one of
# those slots is now a single multi-slide carousel -- several of the
# day's strongest stories as numbered slides within ONE post, matching
# the format of the reference competitor account (@worldinlast24hrs)
# rather than several separate single-story posts.
CAROUSEL_SLIDE_COUNT = 8
# Sent to Telegram for review at this IST time. Not a hard cron minute --
# the workflow polls every 15 min in the evening window and builds the
# carousel on the first run at/after this time each day.
CAROUSEL_PREVIEW_HOUR_IST = 20
CAROUSEL_PREVIEW_MINUTE_IST = 30
# How long the reviewer has to reject/replace slides in Telegram before
# whatever's left (un-rejected) auto-publishes. Same "guaranteed outcome,
# no reply needed" principle as the regular candidate queue.
CAROUSEL_REVIEW_GRACE_MINUTES = 75
# Counts as ONE of the automated slots in MAX_POSTS_PER_24H / the 6
# non-reserved automated slots -- it replaces one single-story post that
# day, it doesn't add on top of the daily ceiling.
CAROUSEL_COUNTS_AS_AUTOMATED_SLOT = True


# ============================================================
# 12. SUBJECT PORTRAITS  (src/subject_photos.py)
# ============================================================
# When a headline names a real, notable person and the story has no
# photo, fetch that person's lead portrait from Wikimedia Commons --
# labelled FILE PHOTO on the image and credited in the caption.
# Event photographs are NOT touched by this (see incident_photos.py).
SUBJECT_PHOTOS_ENABLED = True
# Wikipedia-language-page count a person needs to count as notable enough
# to resolve by name alone. Higher = fewer wrong-namesake risks, fewer
# matches. Tuned on live headlines; see tests/test_subject_photos.py.
SUBJECT_PHOTO_MIN_SITELINKS = 25
# CC BY-SA requires that adaptations (a crop + text overlay arguably is
# one) be shared under the same licence. Off by default; only CC0, public
# domain, CC BY and GODL-India are used unless this is switched on.
SUBJECT_PHOTO_ALLOW_SHARE_ALIKE = False


# ============================================================
# 13. IMAGE REQUIRED  (owner's rule: never publish an image-less news post)
# ============================================================
# A story with no photo -- yours from Telegram, a rights-cleared event photo,
# or a verified file portrait -- is held instead of being posted as a text
# card. Nothing is guessed or generated to fill the gap.
REQUIRE_IMAGE_TO_PUBLISH = True
# Regular Telegram candidates: how long an image-less one waits for your photo
# before it is dropped from the queue.
IMAGE_WAIT_HOURS = 12
# Daily carousel: once the review window ends, slides that still have no image
# are left out and the rest are published -- but only if at least this many
# slides have one. Fewer than that and the whole carousel is held (with a
# reminder) until you attach more.
CAROUSEL_MIN_IMAGE_SLIDES = 4
CAROUSEL_HOLD_REMINDER_HOURS = 2


# The daily roundup Reel is narrated in YOUR voice (reply to each story slide
# with a Telegram voice note). True = the Reel waits for those recordings and
# is not built with the synthetic Piper voice; False = falls back to Piper.
REEL_REQUIRE_OWNER_VOICE = True


# Narrate the Reel in a clone of the owner's voice (data/voice/owner_voice.pt,
# made once from a recorded sample -- see src/voice_clone.py). Used when the
# profile exists; per-slide voice notes still take priority over it.
REEL_CLONE_ENABLED = True
