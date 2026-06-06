"""
rag/embeddings.py — Sentence-transformer embedding model.

Implements FR-21 (RAG grounding) preparation: embeds queries and corpus into 384-dim vectors.
Model: all-MiniLM-L6-v2 (90 MB, CPU-friendly).
"""
from sentence_transformers import SentenceTransformer
from utils.logger import get_logger

logger = get_logger(__name__)

_model = None
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def get_embedding_model() -> SentenceTransformer:
    """Lazy singleton. Loads the model on first call only."""
    global _model
    if _model is None:
        logger.info(f"Loading embedding model: {_MODEL_NAME}")
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("Embedding model loaded.")
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns a 384-dim list of floats."""
    model = get_embedding_model()
    vector = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    return vector.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings. Returns a list of 384-dim vectors."""
    model = get_embedding_model()
    vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, batch_size=32)
    return vectors.tolist()
