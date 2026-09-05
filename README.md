# WASSCE AI Mentor

An AI-powered exam preparation companion for WASSCE candidates — accessible on WhatsApp, USSD (feature phones), with performance dashboards for students, guardians, and teachers.

## The Problem

Millions of WASSCE candidates across Ghana prepare for one of the most consequential exams of their academic lives with limited access to personalized, on-demand support. Private tutoring is expensive. Study groups are inconsistent. And for the many students without reliable internet access or a smartphone, most ed-tech solutions simply don't reach them at all.

## The Solution

WASSCE AI Mentor meets students where they already are — on WhatsApp for smartphone users, and on USSD for feature phones with no internet required. Students practice real WASSCE-style questions across four core subjects, receive instant grading with detailed explanations, and get performance tracking that helps them (and the people supporting them) see exactly where to focus.

## Key Features

- **Two access channels** — WhatsApp for rich interactions, USSD for universal feature-phone reach
- **Four core subjects** — Mathematics, English Language, Integrated Science, and Social Studies
- **Objectives and Theory questions** — multiple-choice practice alongside free-response theory questions, adapted per subject
- **Adaptive AI grading** — theory answers are evaluated for understanding, not exact wording, with personalized feedback explaining what was right, what was missed, and why
- **No-repeat question delivery** — each practice session pulls from the full available question pool for a subject, so students see fresh questions rather than the same handful on repeat
- **Named, personalized sessions** — students register their name on first contact, so every interaction and every dashboard is personal
- **Three dashboard views**:
  - **Student** — enter your phone number to see your own performance: subjects attempted, scores, and progress over time
  - **Guardian** — the same performance view, for parents and guardians supporting a student's preparation
  - **Teacher** — a cohort-wide view across all registered students, for identifying who needs support and where
- **Automated study reminders** — scheduled nudges sent directly to students to encourage consistent practice

## Live Deployment

| Service | Link |
|---|---|
| Performance Dashboard (Student / Guardian / Teacher) | https://wassce-ai-mentor-react.onrender.com |
| API | https://wassce-ai-mentor-api.onrender.com |
| WhatsApp | Message **+1 415 523 8886** with `join inch-service`, then say `Hi` to begin |
| USSD | Available via the Africa's Talking sandbox for demonstration |

## How It Works

1. A student messages the bot on WhatsApp or dials the USSD code
2. On first contact, they're asked for their name
3. They choose a subject, and — where available — whether they want Objectives (MCQs) or Theory questions
4. The system delivers a question drawn from a curated bank of WASSCE-style content
5. The student answers in their own words or selects an option
6. An AI grading layer evaluates the response for understanding and returns a score with a clear explanation
7. Performance is logged and instantly reflected on the student, guardian, and teacher dashboards

## Tech Stack

- **Backend** — Python (FastAPI)
- **Messaging** — Twilio (WhatsApp), Africa's Talking (USSD)
- **AI Grading** — LLM-based adaptive evaluation for free-response answers
- **Database** — PostgreSQL
- **Dashboards** — React (student, guardian, and teacher views), Streamlit (internal analytics)
- **Hosting** — Render

## Project Structure

    wassce-ai-mentor/       Core backend: API, messaging webhooks, grading, database
    dashboard-react/        Student, guardian, and teacher performance dashboards

## Running Locally

**Backend**

    cd wassce-ai-mentor
    pip install -r requirements.txt
    python scripts/init_db.py
    python scripts/ingest.py --reset
    uvicorn api.main:app --reload

**Dashboard**

    cd dashboard-react
    npm install
    npm run dev

## Roadmap

- Guardian-student verified linking (currently, guardian access uses the same phone-lookup as student self-access)
- Expanded theory question coverage across all subjects
- Deeper analytics for teachers, including topic-level weakness detection
- Support for additional West African examination boards

---

Built to make quality exam preparation accessible to every WASSCE candidate, regardless of device or connectivity.
