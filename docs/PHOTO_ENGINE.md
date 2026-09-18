# Incident Photo Engine

Finding a real photograph of a specific news event, and separately
working out whether it can be republished.

## The two questions, kept apart

```
AUTHENTICITY   is this really a photo of THIS event?
REUSE RIGHTS   are we allowed to republish it?
```

These are independent and are never merged. A photo only auto-publishes
when **both** pass. An authentic photo with restricted rights goes to
human review. An unverified photo is never published however permissive
its licence.

```
REAL INCIDENT PHOTO  >  GENERIC PHOTO
NO PHOTO             >  WRONG PHOTO
```

## Files

| File | Does |
|---|---|
| `src/incident_photos.py` | Discovery, verification, licence classification, event identity |
| `src/photo_review.py` | Persistence, Telegram cards, button handling, retries, metrics |
| `src/photo_db.py` | SQLite schema + the append-only JSONL log it is rebuilt from |
| `src/dashboard.py` | Generates `public/dashboard/index.html` |
| `src/telegram_bot.py` | Inline keyboards, callback answers |
| `src/main.py` | Wiring; `photo-retry` and `dashboard` commands |
| `tests/test_incident_photos.py` | 6 critical failure tests + 50 others |
| `.github/workflows/photo-intel.yml` | Hourly retry, metrics, dashboard |

## Pipeline

```
story
  |
  +-- pass 1: the publisher's own og:image + a few CC candidates
  |             |
  |             +-- verify each: event / location / person / time / file-photo
  |             +-- classify licence from the source domain or explicit metadata
  |
  +-- pass 2 (only if pass 1 found nothing reusable):
  |             search for ANOTHER photo of the SAME event from a source
  |             whose material can actually be reused -- police, district
  |             administration, government release, Creative Commons
  |
  +-- pick: authentic AND reusable first, else highest-confidence authentic
  |
  +-- persist EVERY candidate, including the rejected ones
  +-- show the reviewer a card stating both verdicts separately
  +-- if nothing usable: put the story on the retry ladder
```

Pass 2 is the point. Discovery already works; rights are the wall.
Finding the same copyrighted photo a second time helps nobody.

## States

| Image status | Meaning |
|---|---|
| `VERIFIED_FREE_IMAGE` | Real photo of the event, CC or public domain |
| `VERIFIED_LICENSED_IMAGE` | Real photo from an official/government source |
| `VERIFIED_COPYRIGHTED_IMAGE` | Real photo of the event, publisher owns it |
| `VERIFIED_FILE_PHOTO` | Real photo, but the publisher says it is not this event |
| `UNCERTAIN_IMAGE` | Could not confirm it is this event |
| `NO_VERIFIED_IMAGE` | Nothing authentic found |
| `OWNER_MEDIA` | You shot it or chose it yourself |

Decisions: `AUTO_PUBLISH`, `MANUAL_REVIEW`, `TEXT_ONLY`, `REJECT`.

`AUTO_PUBLISH` requires authentic **and** reusable. The test suite
asserts this across the whole matrix, with no exceptions.

## Image age

`BREAKING` ≤1h · `VERY_RECENT` ≤6h · `SAME_DAY` ≤24h · `RECENT` ≤72h ·
`OLD` >72h · `UNKNOWN`

## Event identity

`IN-CHENNAI-FIRE-2026-09-16-001`

Two outlets reporting the same fire share an ID, so "The Hindu owns the
photo on article A, but the district administration published one of the
same event on article C" becomes a query rather than a coincidence. The
suffix is a sequence number, not a hash of the headline — hashing only
clusters outlets that happen to pick identical words, which they do not.

## Retry ladder

10m → 30m → 1h → 3h → 6h → 12h, then stop.

A 10:00 "no image" is not a permanent verdict; incident photographs are
routinely published hours after the first text report. A story leaves
the ladder as soon as a reusable photo is found, or once you press a
button on its card.

## Cost

| Source | Cost | Status |
|---|---|---|
| Article `og:image` | Free | In use — highest hit rate |
| GDELT 2.0 DOC API | Free, no key | In use — rate-limits on shared IPs |
| Openverse | Free, no key | In use — the only tier yielding reusable photos |
| GitHub Actions | Free tier | Compute + scheduler |
| SQLite + static HTML | Free | Database + dashboard |
| Bing Image Search | **Paid** (Azure AI) | Excluded — returns 401 |
| X/Twitter search API | **Paid**, from $100/mo | Excluded |
| Instagram/Facebook Graph | n/a | Cannot read third-party accounts' media |
| PIB photo gallery | Free | Removed — matched 0 of 25 real headlines |

Total running cost: nothing.

## What this system will not do

- Generate an AI image of a real incident. There is no state for it, no
  code path to it, and no setting that enables it.
- Offer a button that downloads and republishes copyrighted material.
  The buttons open the original or record a decision.
- Treat "found online" as "free to republish".
- Assume a government photo is public domain, or that a social-media
  photo is free.
- Publish a photo it cannot prove is the right event.

## Running it

```bash
python -m tests.test_incident_photos   # 56 tests, offline, deterministic
python -m src.main photo-retry         # retry + metrics + dashboard
python -m src.main dashboard           # dashboard only
```
