/**
 * AravindNews24 — WhatsApp inbound photo receiver
 * ================================================
 * GitHub Actions can only run on a schedule, not listen for live webhooks,
 * so this always-on Cloudflare Worker (free tier) is the piece that
 * actually receives the user's WhatsApp reply-photos in real time and
 * drops them into the repo where the next `whatsapp-check` run picks
 * them up.
 *
 * Flow:
 *   1. Meta calls GET  /webhook once, to verify this endpoint (echoes
 *      hub.challenge back if hub.verify_token matches).
 *   2. Meta calls POST /webhook on every incoming WhatsApp message. We
 *      only care about image messages sent as a *reply* (message.context.id
 *      is the wamid of the candidate message src/whatsapp.py sent) —
 *      that reply-to id is how we know which pending story the photo
 *      belongs to.
 *   3. Download the image bytes from the Graph API (two hops: media id
 *      -> temporary CDN url -> bytes), then commit it straight into the
 *      GitHub repo via the Contents API, at:
 *        data/whatsapp_inbox/<safe(message.context.id)>.jpg
 *      `safe()` here MUST stay byte-for-byte identical to Python's
 *      `_safe_filename()` in src/main.py — both sides have to agree on
 *      the exact filename or whatsapp_cycle() will never find the photo.
 *
 * Required Worker secrets (set via `wrangler secret put <NAME>` or the
 * Cloudflare dashboard -> Settings -> Variables and Secrets):
 *   WHATSAPP_VERIFY_TOKEN   any string you make up yourself; must match
 *                           what you enter in Meta's App Dashboard ->
 *                           WhatsApp -> Configuration -> Webhook.
 *   WHATSAPP_ACCESS_TOKEN   same long-lived token already used by
 *                           src/whatsapp.py (Meta > WhatsApp > API setup).
 *   GITHUB_TOKEN            a fine-grained PAT scoped to ONLY
 *                           "Contents: Read and write" on this one repo
 *                           (deliberately narrower than the PAT already
 *                           used for repo secrets — least privilege).
 *   GITHUB_REPO             "araviaravind333-sketch/Aravind24new"
 *   GITHUB_BRANCH           "main"
 */

const SAFE_FILENAME_RE = /[^A-Za-z0-9_-]/g;
function safeFilename(messageId) {
  return messageId.replace(SAFE_FILENAME_RE, "_");
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/webhook") {
      return handleVerify(url, env);
    }

    if (request.method === "POST" && url.pathname === "/webhook") {
      // Always ack fast so Meta doesn't retry/disable the webhook; do the
      // real work first since Workers keep running until the response is
      // returned (no separate background-task step needed here).
      try {
        await handleIncoming(request, env);
      } catch (err) {
        console.error("whatsapp webhook error:", err);
      }
      return new Response("EVENT_RECEIVED", { status: 200 });
    }

    return new Response("Not found", { status: 404 });
  },
};

function handleVerify(url, env) {
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token");
  const challenge = url.searchParams.get("hub.challenge");

  if (mode === "subscribe" && token === env.WHATSAPP_VERIFY_TOKEN) {
    return new Response(challenge, { status: 200 });
  }
  return new Response("Forbidden", { status: 403 });
}

async function handleIncoming(request, env) {
  const body = await request.json();

  const entry = body.entry?.[0];
  const change = entry?.changes?.[0];
  const value = change?.value;
  const messages = value?.messages;
  if (!messages || !messages.length) return; // status callbacks etc — ignore

  for (const message of messages) {
    if (message.type !== "image") continue;
    const replyToId = message.context?.id;
    if (!replyToId) continue; // not a reply to one of our candidate messages

    const mediaId = message.image.id;
    const imageBytes = await downloadWhatsAppMedia(mediaId, env.WHATSAPP_ACCESS_TOKEN);
    if (!imageBytes) continue;

    const filename = `${safeFilename(replyToId)}.jpg`;
    await commitToGitHub(filename, imageBytes, env);
  }
}

async function downloadWhatsAppMedia(mediaId, accessToken) {
  const metaRes = await fetch(`https://graph.facebook.com/v21.0/${mediaId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!metaRes.ok) {
    console.error("media metadata fetch failed:", metaRes.status, await metaRes.text());
    return null;
  }
  const meta = await metaRes.json();

  const fileRes = await fetch(meta.url, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!fileRes.ok) {
    console.error("media download failed:", fileRes.status);
    return null;
  }
  return new Uint8Array(await fileRes.arrayBuffer());
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

async function commitToGitHub(filename, bytes, env) {
  const path = `data/whatsapp_inbox/${filename}`;
  const apiUrl = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`;

  const res = await fetch(apiUrl, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "aravindnews24-whatsapp-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message: `whatsapp: received photo for ${filename}`,
      content: bytesToBase64(bytes),
      branch: env.GITHUB_BRANCH || "main",
    }),
  });

  if (!res.ok) {
    console.error("GitHub commit failed:", res.status, await res.text());
  }
}
