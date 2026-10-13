/**
 * AravindNews24 — reliable scheduler
 * ==================================
 * Fires this repo's Telegram workflows on a real schedule, because
 * GitHub's own cron does not.
 *
 * MEASURED, over five days on the live repo:
 *
 *     workflow                     cron     should run   actually ran
 *     Telegram Candidate Queue     * /30      48/day        4-5/day
 *     Telegram Urgent Breaking     * /10     144/day        4-6/day
 *     Breaking News Check          * /30      48/day        4-5/day
 *     Photo Retry                  hourly     24/day        4-5/day
 *
 * Only two runs in that window were cancelled, so these are not being
 * killed by the shared `auto-poster` concurrency group — GitHub simply
 * never creates them. Scheduled workflows are best-effort, and ~90% of
 * this repo's are dropped.
 *
 * Two user-visible symptoms, one cause:
 *   1. Posting fell from 14/day to 3-7/day. telegram_cycle() posts one
 *      story per run, so output tracks run count exactly.
 *   2. Tapping a review button appeared to do nothing for hours. Taps
 *      are only read when a poll runs.
 *
 * WHY THIS IS A CRON TRIGGER AND NOT A TELEGRAM WEBHOOK
 * ----------------------------------------------------
 * A webhook was the obvious fix and is the wrong one. Telegram allows
 * EITHER getUpdates OR a webhook, never both, and this entire pipeline
 * reads updates by polling. Registering a webhook silently starves
 * getUpdates — that exact mistake took the bot off the air for over a
 * day (see src/telegram_bot.get_new_updates, which now force-clears any
 * webhook before every poll for that reason). It also moves delivery
 * onto a single point of failure: Telegram gets its 200 OK, and if the
 * Worker fails to hand the update on, that update is gone for good.
 *
 * This Worker never speaks to Telegram. It only asks GitHub to run a
 * workflow, and the workflow polls exactly as it always has. If this
 * Worker dies, the bot degrades to GitHub's own flaky schedule — which
 * is the current state, not an outage.
 *
 * SETUP (one time, free tier is far more than enough)
 * ---------------------------------------------------
 *  1. Create the Worker and paste this file in.
 *  2. Settings -> Variables and Secrets:
 *       GITHUB_TOKEN  a fine-grained PAT scoped to ONLY
 *                     "Actions: Read and write" on this one repo
 *       GITHUB_REPO   araviaravind333-sketch/Aravind24new
 *       GITHUB_REF    main
 *  3. Settings -> Triggers -> Cron Triggers, add:
 *       *\/5 * * * *
 *  4. Leave GitHub's own cron schedules in place as a fallback.
 *
 * Free tier: 100,000 requests/day. At one every five minutes this uses
 * 288, plus a handful of manual hits.
 */

// Rotated so one workflow being slow cannot starve the others, and so a
// single five-minute tick never queues all of them into the shared
// `auto-poster` concurrency group at once.
const WORKFLOWS = [
  "telegram-urgent.yml",   // reads button taps + urgent submissions
  "telegram-check.yml",    // the posting queue
  "telegram-urgent.yml",
  "breaking-news.yml",
];

// insights.yml logs the one number this whole account exists to grow
// (followers.csv) and was left off GitHub's own cron being the very
// thing this Worker exists to fix -- it was still on GitHub's native
// daily schedule, the same unreliable one every other workflow here
// used to run on. It does not belong in the hot WORKFLOWS rotation
// above (that would hit the Graph API every few minutes for no
// reason); it runs once every INSIGHTS_EVERY_N_TICKS instead, as its
// own independent, de-synced schedule.
const INSIGHTS_EVERY_N_TICKS = 24;  // every 2h at a 5-min tick rate

async function dispatch(env, workflow) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "aravindnews24-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
  });
  // 204 is success for workflow_dispatch. Anything else is logged with
  // its body: a silent failure here would look exactly like the GitHub
  // scheduler problem this Worker exists to solve.
  if (res.status !== 204) {
    console.error(`dispatch ${workflow} failed:`, res.status, await res.text());
    return false;
  }
  return true;
}

export default {
  async scheduled(event, env, ctx) {
    // Which workflow this tick is responsible for. Derived from the
    // clock rather than stored state so the Worker stays stateless.
    const tick = Math.floor(event.scheduledTime / 300000);
    const slot = tick % WORKFLOWS.length;
    ctx.waitUntil(dispatch(env, WORKFLOWS[slot]));
    // Runs alongside the main rotation, not instead of a slot in it --
    // this is a second, independent dispatch on the same tick.
    if (tick % INSIGHTS_EVERY_N_TICKS === 0) {
      ctx.waitUntil(dispatch(env, "insights.yml"));
    }
  },

  // Manual check: visiting the Worker's URL reports what it would do
  // next, without firing anything. Deliberately read-only -- this
  // Worker has no endpoint that external traffic can use to trigger a
  // run, so its URL leaking cannot cost anything.
  async fetch(request, env) {
    const tick = Math.floor(Date.now() / 300000);
    const slot = tick % WORKFLOWS.length;
    const nextInsights = (INSIGHTS_EVERY_N_TICKS - (tick % INSIGHTS_EVERY_N_TICKS)) * 5;
    return new Response(
      `AravindNews24 scheduler. Next tick would dispatch: ${WORKFLOWS[slot]}\n` +
      `insights.yml next in ~${nextInsights} min\n` +
      `Repo: ${env.GITHUB_REPO || "(GITHUB_REPO not set)"}\n` +
      `This endpoint does not trigger anything.\n`,
      { status: 200, headers: { "content-type": "text/plain" } },
    );
  },
};
