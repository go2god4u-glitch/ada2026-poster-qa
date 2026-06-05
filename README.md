# ADA 2026 Poster Q&A — Live Visitor Chat → Telegram

A tiny relay that lets booth visitors ask questions from their own phone (no app install) and lets
**you, the presenter, answer from Telegram**. A visitor scans the poster QR, opens a web page, enters
**Name + Company (+ optional Email)**, and chats. Each visitor message is **forwarded to your existing
Telegram bot**; you **swipe-reply** to that forwarded message and your reply appears live on the
visitor's page via long-poll. Routing is **self-healing across server restarts**: the 6-character
session code is embedded in every forwarded Telegram message as `#s=XXXXXX`, so even if the in-memory
and SQLite reply-map are lost, the server recovers the target session by regex from the message you
reply to.

> All visitor-facing text is **English** (international medical audience). The Telegram side (what only
> you see) is in Korean.

```
   Visitor phone (browser)                Relay server (app.py)                 Presenter (Telegram)
  ┌───────────────────────┐   POST /api/session  ┌──────────────────┐   sendMessage  ┌──────────────┐
  │  QR → web page         │ ───────────────────► │  FastAPI+uvicorn │ ─────────────► │  bot chat    │
  │  Name / Company / Email│   POST /api/message  │  sqlite data.db  │  "💬 ...#s=AB.."│  (your phone)│
  │  type question ........│ ───────────────────► │                  │                │              │
  │                        │                      │                  │ ◄───────────── │ swipe-reply  │
  │  reply appears live ◄──│ ◄─── GET /api/poll ──│  wake long-poll  │ POST /tg/{secret}│ to message  │
  └───────────────────────┘   (25s long-poll)     └──────────────────┘  (webhook)      └──────────────┘
```

---

## 1. Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI relay server (all endpoints, Telegram webhook, self-heal routing) |
| `static/index.html` | Visitor chat page (English) |
| `gen_qr.py` | Generates a high-res poster QR PNG (saved to Desktop) |
| `render.yaml` | Render Blueprint for one-click deploy |
| `requirements.txt` | `fastapi`, `uvicorn[standard]`, `httpx` |
| `data.db` | SQLite (created at runtime; **ephemeral on Render free** — that's why we self-heal) |

### Endpoints
- `GET /` — visitor chat page
- `GET /healthz` — health check (also the keep-alive ping target)
- `POST /api/session` — create a session, returns 6-char `session_id`
- `POST /api/message` — visitor sends a question
- `GET /api/poll?session_id=..&after=..` — 25s long-poll for presenter replies
- `POST /tg/{secret}` — Telegram webhook (receives your replies)

---

## 2. Quick local run

```bash
cd C:/Users/user/test_project/ada2026_chat
python -m pip install -r requirements.txt
```

Set the required env vars (PowerShell):

```powershell
$env:TELEGRAM_TOKEN   = "123456:ABC..."     # your bot token from @BotFather
$env:TELEGRAM_CHAT_ID = "987654321"         # your personal chat id with the bot
$env:WEBHOOK_SECRET   = "any-long-random-string"
# BASE_URL is optional locally; leave unset to run in "local test mode" (no webhook registered)
```

Run it:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** and start a chat. Messages will be forwarded to your Telegram.
Note: locally Telegram **cannot** push replies back (no public URL), so the webhook is skipped and
the reply path is exercised only after deploy (or via a tunnel like ngrok pointing `BASE_URL` at your
machine). For full local testing of the reply leg, set `BASE_URL` to a public tunnel URL.

---

## 3. Deploy to Render (GitHub + Blueprint)

The repo already contains `render.yaml`, so deploy is a Blueprint, not manual config.

1. **Create a GitHub repo** and push this folder:
   ```bash
   cd C:/Users/user/test_project/ada2026_chat
   git init
   git add .
   git commit -m "ADA 2026 poster Q&A relay"
   git branch -M main
   git remote add origin https://github.com/<you>/ada2026-qa.git
   git push -u origin main
   ```
   > Do **not** commit real secrets. `render.yaml` keeps `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` as
   > `sync: false` so you enter them in the dashboard. Consider adding `data.db*` to `.gitignore`.

2. In the Render dashboard: **New +  →  Blueprint**, connect the GitHub repo. Render reads
   `render.yaml` and proposes the `ada2026-qa` web service (plan: free, start command
   `uvicorn app:app --host 0.0.0.0 --port $PORT`, health check `/healthz`).

3. When prompted, fill in the secret env vars:
   - `TELEGRAM_TOKEN` — your bot token
   - `TELEGRAM_CHAT_ID` — your chat id
   - `WEBHOOK_SECRET` — **leave it**; `generateValue: true` makes Render auto-create a random value.
   - `RENDER_EXTERNAL_URL` — **do nothing**; Render injects this automatically (the app reads it as
     the public `BASE_URL`).

4. **Apply / Deploy.** First build installs deps and boots uvicorn. Your public URL is something like
   `https://ada2026-qa.onrender.com`.

---

## 4. Telegram: auto-webhook + how you reply

**Auto-registration.** On startup the app calls Telegram `setWebhook` to
`{BASE_URL}/tg/{WEBHOOK_SECRET}` (with `secret_token` = `WEBHOOK_SECRET`), restricted to `message`
updates. No manual webhook setup. (Requires a public `BASE_URL`, which Render provides via
`RENDER_EXTERNAL_URL`.) Check the deploy logs for `setWebhook: {... "ok": true ...}`.

**Replying (primary): swipe-reply.** Each forwarded question looks like:

```
💬 Jane Doe / Acme Pharma  #s=AB12CD
What is the half-life of the compound?
↩︎ 이 메시지에 답장하면 전달됩니다
```

Swipe-reply (or long-press → Reply) **to that message** and type your answer. It appears on the
visitor's page within seconds.

**Fallback: "CODE message".** If you can't reply to the original (e.g. it scrolled away or the bot
lost context), send a plain message in the form:

```
AB12CD Yes, roughly 12 hours in humans.
```

The first token is the 6-char session code; the rest is the answer. The server validates the code and
routes it. (As a last resort, if exactly one visitor has been active in the last 30 minutes, an
unrouted reply goes to that visitor.)

**Self-heal:** because `#s=AB12CD` is in the forwarded text, swipe-reply still works **even after a
Render restart wipes the in-memory and SQLite reply-map** — the code is recovered by regex from the
message you replied to.

---

## 5. Keep-alive (so the booth doesn't hit a cold start)

**Render free instances sleep after inactivity** — the commonly documented threshold is **~15 minutes
idle**, and the first request after sleep is a **cold start of roughly 30–60s (can be longer)**.
**Verify the current numbers** in Render's own docs before the event; cloud free-tier policies change.

**Recommended keep-alive:** point a dedicated uptime monitor at **`/healthz`** every **5 minutes**:
- **UptimeRobot** — free, 5-minute interval HTTP monitor on `https://<your-app>.onrender.com/healthz`.
- **cron-job.org** — same idea, free scheduled HTTP GET.

Turn the pinger **on the morning of the event** (or the night before) so the instance is already warm.

**Why NOT GitHub Actions cron:** scheduled GitHub Actions are **best-effort and frequently delayed**
(often well past the requested minute, sometimes skipped under load). That jitter is fine for chores
but unacceptable when a 5-minute gap can let the service fall asleep right as a visitor scans the QR.
Use a purpose-built uptime pinger instead.

**Always-on alternative (optional, not the default):** if you want **no sleep at all**, a free
always-on tier such as **Koyeb** or **Fly.io** can keep the instance running — but both generally
**require a card on file** to unlock that. Only consider this if a single cold start during the poster
session is unacceptable; otherwise the UptimeRobot approach is simpler.

---

## 6. Generate the poster QR

```bash
python gen_qr.py https://ada2026-qa.onrender.com
```

Saves a high-res, high-error-correction PNG to your **Desktop** (`ada2026_qa_qr.png`), sized for print.
If `qrcode` isn't installed: `pip install "qrcode[pil]"`. On the poster, pair the QR with a line like
**"Scan to ask the presenter — live reply."**

---

## 7. Pre-event test (DO THIS — it validates self-heal)

A normal "send a message, get a reply" test is **not enough**. You must test the **full loop including
a forced restart**, because the whole point of the `#s=` design is surviving a Render restart mid-event.

1. **Warm path.** Open the deployed URL on your phone, start a session, send a question. Confirm it
   arrives in Telegram with a `#s=XXXXXX` code. Swipe-reply; confirm the reply appears on the page.
2. **Force a restart.** In the Render dashboard, **Manual Deploy → restart** (or redeploy) the service.
   Wait for it to come back (`/healthz` returns `ok`). This **clears the in-memory map and the
   ephemeral SQLite reply-map.**
3. **Reply to the EXISTING conversation.** Go back to the **same** Telegram question from step 1
   (the one sent *before* the restart) and **swipe-reply to it again** with a new answer.
4. **Confirm it still routes.** The visitor page (same session, still open / re-polling) should receive
   that new reply. If it does, the regex recovery of `#s=XXXXXX` worked and routing is self-healing.
   Also verify the **"CODE message"** fallback (`AB12CD your answer`) routes correctly.
5. **Keep-alive check.** Confirm your UptimeRobot/cron-job.org monitor is live and hitting `/healthz`,
   and that the service stays warm across a 15+ minute idle window.

---

## Sources
- [Render — does free tier sleep after inactivity?](https://render.discourse.group/t/do-web-services-on-a-free-tier-go-to-sleep-after-some-time-inactive/3303)
- [Render community — service sleeps after inactivity](https://community.render.com/t/options-bridging-free-tier-and-20-mo-to-avoid-service-sleeping/12233)
- [Northflank — Render alternatives (2026)](https://northflank.com/blog/render-alternatives)
