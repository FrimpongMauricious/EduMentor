"""
rag/embeddings.py — OpenAI text-embedding-3-small embedding client.

Switched from local sentence-transformers (90 MB model, ~400 MB RAM)
to OpenAI text-embedding-3-small (HTTP API, zero RAM footprint).

Dimensions: 1536 (vs 384 for MiniLM).
Cost: $0.02 per 1M tokens. Corpus = ~10K tokens (~$0.0002 to ingest).
Per-query cost: ~$0.000001.
"""
import os
from dotenv import load_dotenv
from openai import OpenAI
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_client = None
_MODEL_NAME = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def _get_client() -> OpenAI:
    """Lazy singleton OpenAI client."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY env var not set — required for embeddings."
            )
        _client = OpenAI(api_key=api_key)
        logger.info(f"OpenAI embedding client initialised (model={_MODEL_NAME})")
    return _client


def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns a 1536-dim list of floats."""
    client = _get_client()
    response = client.embeddings.create(
        model=_MODEL_NAME,
        input=text,
    )
    return response.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of strings. Returns a list of 1536-dim vectors.
    OpenAI supports up to 2048 inputs per request.
    """
    if not texts:
        return []
    client = _get_client()
    response = client.embeddings.create(
        model=_MODEL_NAME,
        input=texts,
    )
    return [item.embedding for item in response.data]


# Compatibility shim for any code that still calls get_embedding_model()
def get_embedding_model():
    """Deprecated. Kept for backwards compatibility with existing imports."""
    return _get_client()
