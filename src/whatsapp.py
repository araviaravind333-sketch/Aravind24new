"""
WhatsApp Notify
===============
Sends candidate news stories to the user's own WhatsApp (Meta's WhatsApp
Cloud API) so a human can reply with a real, relevant photo for any of
them — this puts a person in the one specific step (finding the right
photo) that's been hardest to get reliably right by heuristics alone,
while everything else (writing, rendering, scheduling, publishing) stays
fully automated. No reply within the grace period -> src/main.py posts
the story anyway, text-only, rather than ever guessing with a stock photo.
"""

import json
import urllib.error
import urllib.request

from config import settings

GRAPH = f"https://graph.facebook.com/{settings.GRAPH_VERSION}"


def _post(payload):
    url = f"{GRAPH}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        return data["messages"][0]["id"]
    except urllib.error.HTTPError as e:
        print("WhatsApp send failed:", e, "-", e.read().decode(errors="replace"))
        return None
    except Exception as e:
        print("WhatsApp send failed:", e)
        return None


def send_candidate(story):
    """Send one candidate as its own message (not batched) — WhatsApp's
    reply-to feature lets the user reply directly to THIS message, which
    is how the pipeline knows which story a returned photo belongs to.

    Sent as an approved message TEMPLATE, not free-form text: a business-
    initiated message outside an active 24h customer-service session can
    only be delivered as a template -- free-form text still returns a
    message id from the API but gets silently dropped, which is exactly
    what happened before this was a template (news_candidate_alert_v1,
    submitted via the Graph API, must show status APPROVED)."""
    return _post({
        "messaging_product": "whatsapp",
        "to": settings.WHATSAPP_RECIPIENT,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANGUAGE},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(story["category"])},
                    {"type": "text", "text": str(story["score"])},
                    {"type": "text", "text": str(story["title"])[:1024]},
                ],
            }],
        },
    })
