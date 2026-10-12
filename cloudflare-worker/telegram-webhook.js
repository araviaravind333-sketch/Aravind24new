/**
 * DO NOT DEPLOY OR REGISTER THIS. USE scheduler.js INSTEAD.
 * =========================================================
 * Registering this webhook took the bot off the air for over a day.
 *
 * Telegram allows EITHER getUpdates OR a webhook, never both. This
 * whole pipeline reads updates by polling, so the moment setWebhook
 * points at this Worker, getUpdates starts returning an empty list --
 * no error, indefinitely. Every message and every button tap silently
 * vanished for 26+ hours before the cause was found, and the setup
 * instructions further down are what caused it.
 *
 * The design was also self-defeating: it wakes the workflow so that
 * src/main.py can poll for the very update the webhook just consumed.
 * That cannot work.
 *
 * src/telegram_bot.get_new_updates() now calls deleteWebhook before
 * every poll, so even if this does get registered it is torn down
 * within one cycle. Kept only as a record of the failure.
 *
 * The real problem it was reaching for -- GitHub dropping ~90% of
 * scheduled runs -- is handled by scheduler.js, which never talks to
 * Telegram at all.
 *
 * Original header follows.
 *
 * AravindNews24 — Telegram instant-trigger webhook
 * =================================================
 * Fixes a real problem: GitHub Actions' scheduler doesn't reliably honor
 * very frequent (sub-10-minute) cron schedules -- it can silently go 15+
 * minutes between runs of a workflow scheduled for every 5 minutes,
 * which defeats the whole point of the urgent-breaking-news path
 * (post within minutes).
 *
 * This Worker is a dumb, reliable trigger, nothing more: Telegram calls
 * it the instant ANY message arrives (real-time, no polling), and it
 * immediately fires GitHub's workflow_dispatch API to run
 * telegram-urgent.yml right then. All the actual decision-making (is
 * this a reply to a candidate, is this a link+media urgent submission,
 * etc.) stays in src/main.py's _poll_telegram_replies() -- unchanged,
 * already tested. This Worker doesn't duplicate that logic; it just
 * wakes the workflow up immediately instead of waiting for the next
 * scheduled tick, which stays in place as a fallback safety net.
 *
 * Setup:
 *   1. Deploy this Worker, note its URL.
 *   2. Set secrets (Settings -> Variables and Secrets):
 *        TELEGRAM_BOT_TOKEN     same token used everywhere else
 *        TELEGRAM_WEBHOOK_SECRET  any string you make up
 *        GITHUB_TOKEN           a NEW fine-grained PAT scoped to ONLY
 *                               "Actions: Read and write" on this one
 *                               repo (deliberately separate from the
 *                               secrets-only and contents-only PATs
 *                               already in use elsewhere -- least
 *                               privilege)
 *        GITHUB_REPO            "araviaravind333-sketch/Aravind24new"
 *        GITHUB_REF             "main"
 *   3. Register the webhook with Telegram (one-time, via curl):
 *        https://api.telegram.org/bot<TOKEN>/setWebhook
 *          ?url=<this worker's URL>/webhook
 *          &secret_token=<same value as TELEGRAM_WEBHOOK_SECRET>
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/webhook") {
      return new Response("Not found", { status: 404 });
    }

    // Telegram echoes this header back on every webhook call once set via
    // setWebhook's secret_token param -- the only practical way to verify
    // a request genuinely came from Telegram and not a random POST.
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (secret !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    // Ack Telegram immediately -- don't make it wait on the GitHub call.
    ctx.waitUntil(triggerWorkflow(env));
    return new Response("OK", { status: 200 });
  },
};

async function triggerWorkflow(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/telegram-urgent.yml/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "aravindnews24-telegram-webhook",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
  });
  if (!res.ok) {
    console.error("workflow_dispatch failed:", res.status, await res.text());
  }
}
