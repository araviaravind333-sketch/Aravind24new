"""
AI Writer
=========
Rewrites the raw RSS headline into:
  - a punchy branded headline (simple English, stop-scroll)
  - the accent word/phrase to color orange on the image
  - a short descriptive caption with a personal touch + CTA
  - 4 hashtags chosen for reach

Uses Gemini (Google AI Studio, free tier) if GEMINI_API_KEY is set, else
Anthropic if ANTHROPIC_API_KEY is set. Falls back to rule-based rewriting
if no key / call fails, so the pipeline NEVER stalls.
"""

import json
import os
import re
import time
import urllib.request
import urllib.error

from config import settings

RETRY_DELAYS = (3, 8)  # seconds — transient 429/503 "model overloaded" errors
                       # are common on free-tier LLM APIs and clear up fast,
                       # so retry a couple times before giving up to the
                       # much weaker rule-based fallback

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")


PROMPT = """You are the editor of AravindNews24, a fast-growing India + world news page \
on Instagram and Facebook. Your audience is pan-India, mostly men 25-34.

Rewrite the raw news below into JSON with these keys:
- "headline": 7-12 words, simple English, present tense, stop-the-scroll. It MUST be a \
COMPLETE, self-contained sentence that a reader with zero background understands on its own. \
Hard rules: never end on a dangling word (to / after / with / and / says / over / against); \
never cut a thought short; never use an abbreviation or insider name the average reader \
wouldn't know (write "child-safety body" not "NCMEC", "central agency" not "ED", unless the \
abbreviation is genuinely household-level like BJP, RBI, ISRO, SC). Say plainly WHO did WHAT. \
Create curiosity through specific real numbers/names/stakes from the story itself, NOT vague \
teasers ("you won't believe..."), and NEVER a claim that isn't true.
- "accent_word": the 1-2 word phrase inside the headline that matters most (to highlight). \
It must appear in the headline EXACTLY as written there.
- "caption": 3-4 short sentences in simple English that actually EXPLAIN the story to someone \
who knows nothing about it. Sentence 1: what happened, plainly. Sentence 2: the key specifics \
— who is involved, where, when, how many, how much. Sentence 3: why it matters or what happens \
next. Spell out any abbreviation the first time. Then ONE real engagement question (not \
"comment X"). If -- and only if -- the story is genuinely shocking, divisive, or the kind of \
thing people forward to a friend (not routine news), end with a short natural share nudge \
instead of the question, e.g. "Tag someone who needs to see this" -- shares are weighted \
heavily by Instagram's algorithm, but a share nudge on a routine story reads as desperate, so \
use real judgment. Keep every fact accurate. Do not invent details that aren't in the source.
- "hashtags": array of exactly 4 hashtags, mixing broad + niche + trending + "#AravindNews24".

Raw category: {category}
Raw headline: {title}
Raw summary: {summary}

Return ONLY the JSON, nothing else."""


# Words a headline must never end on -- chopping right before/after one of
# these is what produced real posted headlines like "Meta says will report
# child safety cases directly to" and "...after Supreme Court cancels",
# which read as broken fragments because the object of the sentence got
# cut off.
_DANGLING_TAIL = {
    "a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "from",
    "in", "on", "at", "by", "as", "after", "before", "over", "under", "into",
    "that", "this", "these", "those", "its", "their", "his", "her", "our",
    "is", "are", "was", "were", "be", "been", "will", "says", "said", "amid",
    "against", "about", "up", "off", "out", "per", "via", "not",
}


def _trim_headline(title, max_words=13):
    """Shorten a raw RSS title WITHOUT leaving a dangling fragment. The
    template already shrinks the font to fit, so a slightly longer but
    complete headline always beats a short broken one. Prefers cutting at
    a real clause boundary (comma/colon/dash); otherwise drops trailing
    connector words so the headline still ends on a complete thought."""
    title = title.strip().rstrip(" ,;:-–—")
    words = title.split()
    if len(words) <= max_words:
        return title

    # 1. a clause boundary inside the limit is the cleanest cut
    head = " ".join(words[:max_words])
    m = re.match(r"^(.*[^\s])\s*[,;:–—-]\s", head + " ")
    if m and len(m.group(1).split()) >= 5:
        return m.group(1).rstrip(" ,;:-–—")

    # 2. otherwise cut at the limit, then walk back past any trailing
    #    connector -- including one sitting a word or two from the end
    #    ("...directly to NCMEC and law" -> drop "and law", not just "law").
    cut = words[:max_words]
    while len(cut) > 4:
        tail = [w.lower().strip(",;:.") for w in cut[-3:]]
        hit = next((i for i in range(len(tail) - 1, -1, -1) if tail[i] in _DANGLING_TAIL), None)
        if hit is None:
            break
        cut = cut[: len(cut) - (len(tail) - hit)]
    return " ".join(cut).rstrip(" ,;:-–—") if len(cut) >= 4 else " ".join(words[:max_words])


def _rule_based(story):
    """Deterministic fallback so the bot always produces something clean.
    Uses the RSS summary (a real sentence about what happened), not just
    boilerplate, so even this fallback actually explains the news."""
    title = story["title"]
    words = title.split()
    accent = " ".join(words[:2])
    cat = story["category"]
    tags = settings.HASHTAG_BANK.get(cat, settings.HASHTAG_BANK["INDIA NEWS"])
    # Shares are one of the most heavily-weighted signals in Instagram's
    # distribution algorithm, more than likes -- but a share nudge on a
    # routine story reads as desperate, so only use it for stories that
    # already cleared a genuinely high bar (matches the score-65 gate
    # telegram_cycle() uses to decide what's even worth posting on its
    # own) or a fresh incident, where it actually fits.
    if story.get("score", 0) >= 65 or story.get("is_incident"):
        q = "Tag someone who needs to see this."
    else:
        q = "What's your take on this?"

    summary = re.sub(r"\s+", " ", story.get("summary", "")).strip()
    detail = ""
    if summary:
        # first real sentence of the summary, not the whole (often long) blob
        m = re.match(r"(.{20,220}?[.!?])(\s|$)", summary)
        detail = m.group(1) if m else summary[:200]

    caption = f"{title}. {detail} {q}".strip() if detail else f"{title}. {q}"
    return {
        "headline": _trim_headline(title),
        "accent_word": accent,
        "caption": caption,
        "hashtags": tags,
    }


def _finalize(out):
    tags = out.get("hashtags", [])[:4]
    if settings.BRAND_HASHTAG not in tags:
        tags = tags[:3] + [settings.BRAND_HASHTAG]
    out["hashtags"] = tags
    return out


def _call_gemini(story):
    body = json.dumps({
        "contents": [{
            "parts": [{"text": PROMPT.format(
                category=story["category"],
                title=story["title"],
                summary=story.get("summary", ""),
            )}],
        }],
        "generationConfig": {"response_mime_type": "application/json"},
    }).encode()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"```json|```", "", text).strip()
    return _finalize(json.loads(text))


def _call_anthropic(story):
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": PROMPT.format(
                category=story["category"],
                title=story["title"],
                summary=story.get("summary", ""),
            ),
        }],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    text = "".join(b.get("text", "") for b in data.get("content", []))
    text = re.sub(r"```json|```", "", text).strip()
    return _finalize(json.loads(text))


def _is_transient(err):
    msg = str(err)
    return "HTTP 429" in msg or "HTTP 503" in msg or "HTTP 500" in msg


def _with_retries(fn, story, label):
    last_err = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            print(f"{label} transient error, retrying in {delay}s: {last_err}")
            time.sleep(delay)
        try:
            return fn(story)
        except Exception as e:
            last_err = e
            if not _is_transient(e):
                break
    raise last_err


def rewrite(story):
    if GEMINI_KEY:
        try:
            return _with_retries(_call_gemini, story, "Gemini")
        except Exception as e:
            print("Gemini rewrite failed, using fallback:", e)
            return _rule_based(story)
    if not ANTHROPIC_KEY:
        return _rule_based(story)
    try:
        return _with_retries(_call_anthropic, story, "Anthropic")
    except Exception as e:
        print("AI rewrite failed, using fallback:", e)
        return _rule_based(story)
