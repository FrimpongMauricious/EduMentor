# WASSCE AI Mentor

A multi-channel RAG-based AI tutoring system for WASSCE candidates, accessible via WhatsApp and USSD/SMS.

## Tech Stack

- **FastAPI** — REST API and webhook endpoints
- **LangChain + OpenAI (GPT-5.4-mini)** — RAG pipeline and LLM inference
- **ChromaDB** — Vector store for WASSCE Q&A corpus
- **sentence-transformers** — Local embeddings (all-MiniLM-L6-v2)
- **Twilio** — WhatsApp channel
- **Africa's Talking** — USSD/SMS channel
- **SQLAlchemy + SQLite** — Student session and performance storage
- **Streamlit** — Teacher analytics dashboard
- **Pytest** — Test suite

## Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd wassce-ai-mentor

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env and fill in your API keys
```

## Running the API

```bash
python run.py
```

The API will be available at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

## Running the Dashboard

```bash
streamlit run dashboard/app.py
```

## Running Tests

```bash
pytest tests/
```

## Ingesting the Corpus

```bash
python scripts/ingest.py
```

## Deployment

For production deployment to Render, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Project Info

| Field | Detail |
| --- | --- |
| **Group** | Group 2 |
| **Project** | Project 7 |
| **Department** | Computer Science |
| **Batch** | 2027 |
| **Supervisor** | Dr. Eric Opoku Osei |
