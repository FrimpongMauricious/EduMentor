"""
rag/retriever.py — ChromaDB retrieval with subject filtering and similarity threshold.

Implements FR-21 (RAG retrieval), FR-22 (fallback on low similarity), FR-25 (subject filter).
"""
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from config import get_settings
from rag.embeddings import embed_text
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Similarity threshold below which we treat retrieval as "no match" (FR-22).
# Chroma returns distances; with cosine space, similarity = 1 - distance.
SIMILARITY_THRESHOLD = 0.40  # Conservative for a small starter corpus

_client = None
_collection = None


def _get_collection():
    """Lazy singleton for the ChromaDB collection."""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        _collection = _client.get_or_create_collection(
            name=settings.chroma_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"Retriever connected to collection '{settings.chroma_collection_name}' "
            f"with {_collection.count()} entries."
        )
    return _collection


def retrieve(
    query: str,
    subject: Optional[str] = None,
    top_k: int = 3,
) -> list[dict]:
    """
    Retrieve the top-k most relevant Q&A entries.

    Args:
        query: The student's question text.
        subject: Optional subject filter ('maths', 'english', 'science', 'social_studies').
        top_k: Number of results to return (default 3 per FR-21).

    Returns:
        List of dicts, each containing:
          - question_id, subject, topic, difficulty
          - question_text, correct_answer, explanation
          - similarity (float between 0 and 1)
    """
    collection = _get_collection()
    query_vec = embed_text(query)

    where = {"subject": subject} if subject else None

    results = collection.query(
        query_embeddings=[query_vec],
        n_results=top_k,
        where=where,
    )

    if not results or not results.get("ids") or not results["ids"][0]:
        logger.warning(f"No retrieval results for query: {query!r} (subject={subject})")
        return []

    output = []
    for i, rid in enumerate(results["ids"][0]):
        metadata = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        similarity = 1.0 - distance  # cosine distance to similarity

        output.append({
            "question_id": metadata["question_id"],
            "subject": metadata["subject"],
            "topic": metadata["topic"],
            "difficulty": metadata["difficulty"],
            "question_text": metadata["question_text"],
            "correct_answer": metadata["correct_answer"],
            "explanation": metadata["explanation"],
            "similarity": round(similarity, 4),
        })

    return output


def get_by_id(question_id: str) -> Optional[dict]:
    """
    Retrieve a single Q&A entry directly by its question_id.
    Used by the FSM to look up the current question without semantic search.
    """
    collection = _get_collection()
    results = collection.get(ids=[question_id], include=["metadatas"])
    if not results or not results.get("ids") or not results["ids"]:
        return None
    meta = results["metadatas"][0]
    return {
        "question_id": meta["question_id"],
        "subject": meta["subject"],
        "topic": meta["topic"],
        "difficulty": meta["difficulty"],
        "question_text": meta["question_text"],
        "correct_answer": meta["correct_answer"],
        "explanation": meta["explanation"],
        "similarity": 1.0,
    }


def get_by_subject(
    subject: str,
    difficulty: Optional[str] = None,
) -> list[dict]:
    """
    Return all Q&A entries for a given subject (and optional difficulty level)
    without any semantic ranking. Used by the FSM adaptive picker.
    """
    collection = _get_collection()
    if difficulty:
        where = {"$and": [{"subject": {"$eq": subject}}, {"difficulty": {"$eq": difficulty}}]}
    else:
        where = {"subject": {"$eq": subject}}

    results = collection.get(where=where, include=["metadatas"])
    if not results or not results.get("ids") or not results["ids"]:
        return []

    output = []
    for meta in results["metadatas"]:
        output.append({
            "question_id": meta["question_id"],
            "subject": meta["subject"],
            "topic": meta["topic"],
            "difficulty": meta["difficulty"],
            "question_text": meta["question_text"],
            "correct_answer": meta["correct_answer"],
            "explanation": meta["explanation"],
            "similarity": 1.0,
        })
    return output


def retrieve_with_threshold(
    query: str,
    subject: Optional[str] = None,
    top_k: int = 3,
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[list[dict], bool]:
    """
    Retrieve with similarity threshold check.

    Returns:
        (results, is_grounded): is_grounded=False if no result meets the threshold,
        in which case the LLM should return a fallback message per FR-22.
    """
    results = retrieve(query, subject=subject, top_k=top_k)
    if not results:
        return [], False

    top_similarity = results[0]["similarity"]
    is_grounded = top_similarity >= threshold

    if not is_grounded:
        logger.info(f"Low similarity ({top_similarity:.3f} < {threshold}) for query: {query!r}")

    return results, is_grounded
