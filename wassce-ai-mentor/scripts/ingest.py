"""
scripts/ingest.py — Loads the WASSCE Q&A corpus into ChromaDB.

Usage: python scripts/ingest.py [--reset]

Implements FR-18 (corpus indexed), FR-19 (full Q&A schema preserved as metadata).
"""
import sys
import os
import json
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb
from chromadb.config import Settings as ChromaSettings
from config import get_settings
from rag.embeddings import embed_batch
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


def build_document_text(qa: dict) -> str:
    """Combine question + answer + explanation into a single embeddable document."""
    return (
        f"Subject: {qa['subject']} | Topic: {qa['topic']} | Difficulty: {qa['difficulty']}\n"
        f"Question: {qa['question_text']}\n"
        f"Answer: {qa['correct_answer']}\n"
        f"Explanation: {qa['explanation']}"
    )


def main(reset: bool = False) -> None:
    corpus_path = "data/corpus/wassce_qa.json"
    if not os.path.exists(corpus_path):
        logger.error(f"Corpus not found at {corpus_path}")
        sys.exit(1)

    with open(corpus_path, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    logger.info(f"Loaded {len(corpus)} Q&A pairs from {corpus_path}")

    # Filter only validated entries (FR-RAG-04)
    validated = [qa for qa in corpus if qa.get("validated", False)]
    if len(validated) < len(corpus):
        logger.warning(f"Skipping {len(corpus) - len(validated)} unvalidated entries")

    # Initialise ChromaDB persistent client
    os.makedirs(settings.chroma_persist_dir, exist_ok=True)
    client = chromadb.PersistentClient(
        path=settings.chroma_persist_dir,
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    if reset:
        logger.warning(f"Resetting collection: {settings.chroma_collection_name}")
        try:
            client.delete_collection(settings.chroma_collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=settings.chroma_collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # Build documents, IDs, and metadata
    ids = [qa["question_id"] for qa in validated]
    documents = [build_document_text(qa) for qa in validated]
    metadatas = [
        {
            "question_id": qa["question_id"],
            "subject": qa["subject"],
            "topic": qa["topic"],
            "difficulty": qa["difficulty"],
            "question_text": qa["question_text"],
            "correct_answer": qa["correct_answer"],
            "explanation": qa["explanation"],
            "source": qa.get("source", "unknown"),
        }
        for qa in validated
    ]

    logger.info("Embedding documents (this may take 10-30s on first run)...")
    embeddings = embed_batch(documents)

    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    count = collection.count()
    logger.info(f"Ingestion complete. Collection '{settings.chroma_collection_name}' now contains {count} entries.")

    # Summary by subject
    from collections import Counter
    by_subject = Counter(qa["subject"] for qa in validated)
    logger.info("Breakdown by subject:")
    for subject, n in sorted(by_subject.items()):
        logger.info(f"  {subject}: {n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Delete and recreate the collection")
    args = parser.parse_args()
    main(reset=args.reset)
