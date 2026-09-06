# AravindNews24 — Fully Automated News Poster

Zero-cost, no-human-intervention pipeline that posts trending India + World
news to Instagram (@aravindnews24) and your Facebook Page every 4 hours,
using GitHub Actions as the free scheduler.

**What it does each cycle**
1. Picks the top trending story (last 24h) — India-first 5AM–11PM IST, World-first 11PM–5AM IST
2. Rewrites the headline + caption in your voice using AI (no plagiarism)
3. Generates a high-quality image (AI first, free stock fallback)
4. Renders a branded post (category pill, condensed headline, accent word, logo, footer)
5. Publishes to IG + FB with location tag + 4 auto-chosen hashtags
6. Logs performance so it keeps learning

Everything you'll ever change lives in **`config/settings.py`** and your
GitHub Secrets. That's the "one separate file" for keys/config you asked for.

---

## One-time setup (about 60–90 minutes)

### Step 1 — Create the PRIVATE GitHub repo
1. github.com → New repository → name it `aravindnews24-auto` → **Private** → Create.
2. Upload every file from this folder (keep the structure).

### Step 2 — Meta (Instagram + Facebook) API access
You need your IG account to be a **Business/Creator** account linked to your FB Page.
1. developers.facebook.com → Create App → type **Business**.
2. Add products: **Instagram Graph API** + **Facebook Login**.
3. Get a **long-lived Page access token** (covers both IG + FB).
4. Note down: `META_APP_ID`, `META_APP_SECRET`, `FB_PAGE_ID` (61592722090173),
   `IG_USER_ID` (your IG business account numeric id), and the token.

> Guide: search "Instagram Content Publishing API get started" on Meta docs.

### Step 3 — Free API keys
- **Anthropic** (headline/caption rewrite): console.anthropic.com → API key.
  Cheap: ~₹0.5–1 per post. Or leave blank to use the free rule-based writer.
- **Pexels** (stock fallback): pexels.com/api → free key.
- **Unsplash** (stock fallback): unsplash.com/developers → free key.
- **HuggingFace** (optional better AI images): huggingface.co → Settings → Access Tokens.
- AI images via **Pollinations** need NO key (default).

### Step 4 — Add secrets to GitHub
Repo → Settings → Secrets and variables → Actions → **New repository secret**.
Add each key from `.env.example`. (These are encrypted; never visible again.)

Then under the **Variables** tab (not secrets), add:
- `AI_IMAGE_PROVIDER` = `pollinations` (or `hf`)
- `GH_PAGES_BASE` = `https://<your-github-username>.github.io/aravindnews24-auto`

### Step 5 — Enable GitHub Pages (gives images a public URL)
Repo → Settings → Pages → Source: **GitHub Actions**. Save.
(Instagram's API needs a public image URL; Pages provides it for free.)

### Step 6 — Add your logo (optional but recommended)
Drop a transparent PNG at `assets/logo/logo.png` (max ~300x120).
If absent, the built-in text lockup "ARAVIND NEWS24" is used.

### Step 7 — Test
Repo → Actions → "AravindNews24 Auto Poster" → **Run workflow** (manual).
Watch the log. First real post should appear on IG + FB.

### Step 8 — Token stays alive forever
Add a fine-grained PAT as secret `GH_PAT` (with *Secrets: write* on this repo).
The monthly `refresh-token` workflow renews your Meta token automatically.
Add `pynacl` note: it's installed on demand in that workflow.

---

## Changing the schedule
Edit the `cron:` lines in `.github/workflows/post.yml`.
To move to **every 30 minutes** later, replace the schedule block with:
```yaml
- cron: '*/30 * * * *'
```
and set `POST_INTERVAL_HOURS = 0.5` in `settings.py`. Note: posting every
30 min on a new account looks spammy — ramp up gradually (see business plan).

## Safety / security
- Repo is **private**; no key is ever in code (all via encrypted Secrets).
- `.gitignore` blocks `.env` and any key files.
- Only your own rendered images (in `/public`) are ever committed.
- No publisher photos are scraped → no copyright strikes.

## File map
```
config/settings.py     <- THE config file (schedule, hashtags, sources)
src/main.py            <- orchestrator (one post cycle)
src/news_engine.py     <- fetch + score + de-dupe + geo
src/ai_writer.py       <- headline/caption rewrite
src/image_source.py    <- AI image + stock fallback
src/template.py        <- branded post renderer
src/publisher.py       <- IG + FB Graph API publishing
src/analytics.py       <- performance logging
src/refresh_token.py   <- monthly Meta token refresh
.github/workflows/     <- cron scheduler + token refresh
```
