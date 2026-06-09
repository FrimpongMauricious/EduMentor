# Deployment Guide — WASSCE AI Mentor

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
