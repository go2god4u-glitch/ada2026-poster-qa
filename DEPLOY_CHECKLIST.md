# ADA 2026 Poster Q&A — Day-of-Conference Operational Checklist

Visitor flow: scan QR -> open web page (no app install) -> enter Name, Company, Email (optional) + question -> chat.
Presenter receives each message on Telegram (existing bot) and answers by **swipe-replying** to the forwarded message.
The reply appears live on the visitor page via long-poll.

Reply routing self-heals across server restarts: the 6-char session code is embedded in the forwarded
Telegram message text as `#s=XXXXXX` and recovered by regex if the in-memory / sqlite map is lost.

Stack: FastAPI + uvicorn + httpx, sqlite (`data.db`). Deploy target: Render free tier (web service),
GitHub-connected via `render.yaml`.

---

## 1 week before

- [ ] Push latest code to GitHub; confirm Render auto-deploys from the connected branch.
- [ ] Confirm `render.yaml` start command is `uvicorn app:app --host 0.0.0.0 --port $PORT`.
- [ ] Set environment variables in the Render dashboard:
  - [ ] `TELEGRAM_TOKEN`
  - [ ] `TELEGRAM_CHAT_ID`
  - [ ] `WEBHOOK_SECRET`
  - [ ] (no need to set `RENDER_EXTERNAL_URL` — Render auto-injects it; the app derives `BASE_URL` from it)
- [ ] Trigger a deploy and watch logs for clean startup (no tracebacks).
- [ ] Confirm the Telegram webhook auto-registered on startup to `{BASE_URL}/tg/{WEBHOOK_SECRET}`.
  - [ ] Verify via `https://api.telegram.org/bot<TELEGRAM_TOKEN>/getWebhookInfo` — `url` should match and `last_error_message` should be empty.
- [ ] Open the deployed URL in a browser; confirm the visitor chat page (`/` -> `static/index.html`) loads.
- [ ] Confirm `GET /healthz` returns OK (used as Render health check AND keep-alive target).
- [ ] Generate the QR code with `gen_qr.py` (qrcode lib, saves PNG to Desktop).
  - [ ] Use **error correction level H** (highest) so the code still scans with a logo overlay, glare, or minor print damage.
  - [ ] Point the QR at the deployed Render URL (the visitor page), not a local address.
  - [ ] Print it large, test-scan from ~1 m with two different phones, then print the final poster copy.
- [ ] Set up a keep-alive uptime pinger hitting `/healthz`:
  - [ ] **UptimeRobot** HTTP(s) monitor at a **5-minute** interval (or cron-job.org). This keeps the instance warm during booth hours.
  - [ ] Do **NOT** rely on GitHub Actions cron for keep-alive — it is too unreliable / delayed for this.
  - [ ] Note: Render free web services spin down after ~15 minutes of no inbound traffic, so a 5-minute ping keeps it from sleeping.

---

## Morning of

- [ ] Open the deployed URL on your phone; confirm the page loads fast (instance is already warm from the pinger).
- [ ] Confirm Telegram notifications arrive: send a test message and verify the forwarded message appears in your chat, including the `#s=XXXXXX` session tag.
- [ ] From a **second device** (e.g., a colleague's phone), do one full test Q&A:
  - [ ] Enter Name, Company, Email (optional) + a question.
  - [ ] Confirm the message reaches Telegram.
  - [ ] **Swipe-reply** to the forwarded Telegram message.
  - [ ] Confirm the reply appears **live** on the visitor page (long-poll delivers it within a couple seconds).
- [ ] Confirm all visitor-facing text is in English (international medical audience).
- [ ] Charge your phone / bring a battery pack — long-poll + Telegram keep the screen active.

---

## At the booth

- [ ] Keep **Telegram open** and to the foreground on your phone.
- [ ] Answer each visitor by **swipe-replying** to the forwarded message (do not start a fresh message — swipe-reply preserves routing).
- [ ] Watch for the **session code** (`#s=XXXXXX`) at the end of each forwarded message — it identifies which visitor you are answering.
- [ ] Keep replies concise; the visitor sees them appear live on their own phone.

---

## If something breaks

- [ ] **Slow / blank first load (cold start):** the instance likely slept. Wait ~30–60 seconds (Render quotes about one minute) for it to spin back up; the page will load once it is warm. The keep-alive pinger should normally prevent this.
- [ ] **Webhook seems dead / replies not routing:** re-trigger the app by visiting `/healthz` to wake it, then re-check `getWebhookInfo`. If still broken, **re-deploy** from Render — the webhook auto-registers on startup. Confirm `getWebhookInfo` `url` is correct and `last_error_message` is empty.
- [ ] **Don't know which visitor a message belongs to:** read the `#s=XXXXXX` code embedded in the forwarded Telegram message text. Routing recovers from this regex even if the in-memory / sqlite session map was lost on restart.
- [ ] **Swipe-reply not working on your device:** use the fallback reply format — send a normal Telegram message in the form `CODE message` (the 6-char session code, a space, then your reply text), e.g. `A1B2C3 Yes, the Phase 2 data is on the right panel.`
- [ ] **Total Render outage / persistent sleep problem (last resort):** an always-on alternative with no idle spin-down is available — **Koyeb** or **Fly.io** — but both require a card on file. Not the default; only consider if Render is failing on the day.

---

### Reference — key facts

- Required env vars: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `WEBHOOK_SECRET` (`RENDER_EXTERNAL_URL` auto-injected by Render).
- Endpoints: `GET /`, `GET /healthz`, `POST /api/session`, `POST /api/message`, `GET /api/poll`, `POST /tg/{secret}`.
- Webhook target: `{BASE_URL}/tg/{WEBHOOK_SECRET}`, auto-registered on startup.
- Render free tier: spins down after ~15 min idle; cold start ~30–60s (Render: "about one minute"). Verified June 2026.
- Keep-alive: UptimeRobot (5-min) or cron-job.org hitting `/healthz`. Not GitHub Actions cron.
- Always-on (no sleep, card required): Koyeb or Fly.io — option only, not the default.
