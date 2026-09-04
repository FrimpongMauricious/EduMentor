# WASSCE AI Mentor — React Performance Dashboard

A formal, blue-and-white performance dashboard for the WASSCE AI Mentor
programme, built as a **separate** service from the existing Streamlit
teacher dashboard (which remains untouched at `../wassce-ai-mentor/dashboard/`).

Two views:

- **Student** — enter a phone number, see only your own performance
  (rate limited on the backend to 10 requests/minute per IP).
- **Teacher** — password-protected, sees the full cohort plus a
  drill-down into any individual student.

## Stack

Vite + React + `react-router-dom` + `recharts`. No build-time backend
coupling — everything is fetched at runtime from the FastAPI JSON API
under `/api/dashboard/*`.

## Running locally

```bash
npm install
npm run dev
```

By default the app calls the production API
(`https://wassce-ai-mentor-api.onrender.com`). To point at a local backend,
create a `.env` file (see `.env.example`):

```
VITE_API_BASE_URL=http://localhost:8000
```

## Building

```bash
npm run build
```

Output goes to `dist/`. Preview the production build locally with
`npm run preview`.

## Deploying on Render

This is defined in `../wassce-ai-mentor/render.yaml` as the
`wassce-ai-mentor-react` service (`type: web`, `runtime: static`). If a
Blueprint sync ever rejects that static-site syntax on your Render account,
create the service manually instead, using these exact settings:

| Setting | Value |
|---|---|
| Type | Static Site |
| Repository root dir | `dashboard-react` |
| Build command | `npm install && npm run build` |
| Publish directory | `dist` |
| Environment variable | `VITE_API_BASE_URL` = `https://wassce-ai-mentor-api.onrender.com` |
| Redirect/rewrite rule | `/*` → `/index.html` (required for client-side routing) |

The rewrite rule is required — without it, refreshing on any route other
than `/` (e.g. `/student/dashboard`) will 404, because those paths only
exist client-side via `react-router-dom`.

## Backend requirements

The FastAPI backend must have CORS enabled (already added in
`api/main.py`) and expose:

- `GET /api/dashboard/student/{phone}`
- `GET /api/dashboard/teacher/overview` (header: `X-Dashboard-Password`)
- `GET /api/dashboard/teacher/student/{phone}` (header: `X-Dashboard-Password`)

See `../wassce-ai-mentor/api/routes/dashboard.py` for the implementation.
