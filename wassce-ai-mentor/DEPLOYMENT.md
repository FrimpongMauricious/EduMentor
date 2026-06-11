# Deployment Guide — WASSCE AI Mentor

## Live URLs (v1.0.0)

| Service | URL | Purpose |
|---|---|---|
| API | https://wassce-ai-mentor-api.onrender.com | FastAPI backend — receives Twilio + Africa's Talking webhooks |
| Dashboard | https://wassce-ai-mentor-dashboard.onrender.com | Streamlit teacher analytics (password-protected) |
| Health Check | https://wassce-ai-mentor-api.onrender.com/health | Used by cron-job.org keep-alive ping |

## Channel Configuration

| Channel | Provider | Sandbox Number / Code |
|---|---|---|
| WhatsApp | Twilio (sandbox) | +1 415 523 8886 (join with code: `join inch-service`) |
| USSD | Africa's Talking (sandbox) | `*384*25470#` (via AT simulator at https://simulator.africastalking.com) |

## Database

| Service | Provider | Region | Purpose |
|---|---|---|---|
| PostgreSQL | Neon (free tier) | eu-central-1 (Frankfurt) | Persistent storage: students, sessions, interactions, performance_vectors, test_attempts |
| Vector Store | ChromaDB (local on Render) | Frankfurt | 24 WASSCE Q&A pairs embedded via OpenAI text-embedding-3-small (1536-dim) |

## LLM Stack

| Model | Purpose | Provider |
|---|---|---|
| gpt-5.4-mini | Conversational responses + answer explanations | OpenAI |
| text-embedding-3-small | Document and query embeddings | OpenAI |

## Infrastructure

- **Hosting:** Render free tier, Frankfurt region (eu-central-1)
- **Keep-alive:** cron-job.org pings `/health` every 10 minutes to prevent Render free-tier idle spin-down
- **Test status:** 96/96 automated tests passing

## Known Limitations (Sandbox Mode)

- **Twilio WhatsApp sandbox:** has daily message limits (~9–100 messages/day). Sufficient for development and exhibition demos but not for full pilot at scale. Production deployment requires upgrading to a Twilio paid WhatsApp Business sender.
- **Africa's Talking USSD sandbox:** accessible only via AT's online simulator. Production deployment requires a paid dedicated USSD short code through a Ghanaian telco.
- **Both limitations are deployment formalities** — the architecture, code, and live system are fully production-ready.

---

This document describes how to deploy the WASSCE AI Mentor to Render free tier.

## Prerequisites

- GitHub repository with this code (already in place)
- Render account: https://render.com (free, sign up with GitHub)
- Twilio account with WhatsApp sandbox configured
- Africa's Talking sandbox account with a USSD service code
- OpenAI API key for LLM responses
- A free cron-job.org account for keep-alive pinging

## Step 1 — Create the API Web Service on Render

1. Log into https://render.com
2. Click **New** -> **Blueprint**
3. Connect your GitHub repo `wassce-ai-mentor`
4. Render will detect the `render.yaml` and propose two services
5. Click **Apply**

## Step 2 — Set the Environment Variables (Secrets)

Render will create the services but will pause them because the `sync: false` env vars need to be filled in.

Go to **wassce-ai-mentor-api** -> **Environment**, and add these values:

| Key | Value |
|---|---|
| TWILIO_ACCOUNT_SID | From your Twilio Console |
| TWILIO_AUTH_TOKEN | From your Twilio Console |
| TWILIO_WHATSAPP_NUMBER | `whatsapp:+14155238886` |
| AT_USERNAME | `sandbox` |
| AT_API_KEY | From your AT Sandbox -> Settings -> API Key |
| AT_SHORTCODE | `*384*25470#` |
| OPENAI_API_KEY | From your OpenAI Console |
| OPENAI_MODEL | `gpt-5.4-mini` |
| DASHBOARD_PASSWORD | A strong password — share only with the supervisor |

Repeat the same DASHBOARD_PASSWORD for the **wassce-ai-mentor-dashboard** service.

Click **Save Changes** -> the services will redeploy automatically.

## Step 3 — Wait for Build to Complete

First build takes ~6-10 minutes. Watch the logs:
- API service: Render -> wassce-ai-mentor-api -> Logs
- Look for: `Application startup complete.` and `Uvicorn running on http://0.0.0.0:10000`

## Step 4 — Update Twilio + Africa's Talking Webhooks

Your Render URLs will look something like:
- API: `https://wassce-ai-mentor-api.onrender.com`
- Dashboard: `https://wassce-ai-mentor-dashboard.onrender.com`

In Twilio Console -> WhatsApp Sandbox -> Sandbox settings:
- Set **"When a message comes in"** to: `https://wassce-ai-mentor-api.onrender.com/webhook/whatsapp`

In Africa's Talking -> USSD -> Service Codes -> edit your `*384*25470#`:
- Set **Callback URL** to: `https://wassce-ai-mentor-api.onrender.com/webhook/ussd`

## Step 5 — Smoke Test

Run from your laptop:

```bash
python scripts/smoke_test.py https://wassce-ai-mentor-api.onrender.com
```

Expected: three checkmarks.

## Step 6 — Set up Keep-Alive Pinging

Render free tier idles services after 15 minutes of inactivity. To prevent slow cold starts during the pilot, set up cron-job.org to ping /health every 10 minutes.

1. Sign up at https://cron-job.org (free, no credit card)
2. Create a new cron job with these settings:
   - **Title**: WASSCE AI Mentor keep-alive
   - **URL**: `https://wassce-ai-mentor-api.onrender.com/health`
   - **Schedule**: Every 10 minutes
3. Save and enable.

## Step 7 — Final Live Test

- Send a WhatsApp message to the Twilio sandbox number from your phone
- Dial `*384*25470#` in the Africa's Talking simulator
- Open the dashboard URL and log in with your DASHBOARD_PASSWORD

The system is now live and ready for the pilot study.

## Troubleshooting

**Build fails with "out of memory":**
- Render free tier has 512 MB. The build may need more for sentence-transformers.
- Workaround: comment out `python scripts/ingest.py --reset` from the buildCommand and run ingestion manually via Render Shell after deploy.

**Cold start latency > 30 seconds:**
- Normal on free tier. Keep-alive pings will keep it warm during pilot hours.

**Twilio signature validation fails:**
- TWILIO_AUTH_TOKEN must match exactly what's in your Twilio Console.
- Make sure the webhook URL in Twilio matches the Render URL exactly (https, no trailing slash).

**Data resets on idle:**

- Render free tier does not allow persistent disks. SQLite and ChromaDB are stored on ephemeral disk and reset after service spin-down or redeploy.
- The corpus is automatically reloaded on every deploy via the buildCommand.
- For long-term pilot data persistence, upgrade to a paid Render plan with persistent disk, OR migrate to PostgreSQL (Render offers a free PostgreSQL tier separately).
