"""
tests/test_rag.py — RAG pipeline unit tests.

Run via: pytest tests/test_rag.py -v
Prerequisite: corpus must be ingested via `python scripts/ingest.py --reset` once before running.
"""
import os
import pytest
from rag.embeddings import embed_text, embed_batch
from rag.retriever import retrieve, retrieve_with_threshold
from rag.pipeline import answer_query


class TestEmbeddings:
    def test_embed_text_returns_384_dims(self):
        v = embed_text("Solve for x: 2x + 5 = 15")
        assert isinstance(v, list)
        assert len(v) == 1536
        assert all(isinstance(x, float) for x in v)

    def test_embed_batch_returns_correct_shape(self):
        texts = ["Hello", "Photosynthesis", "Algebra"]
        vectors = embed_batch(texts)
        assert len(vectors) == 3
        assert all(len(v) == 1536 for v in vectors)

    def test_similar_queries_have_similar_embeddings(self):
        import numpy as np
        a = np.array(embed_text("Solve for x in algebra"))
        b = np.array(embed_text("Find the value of x in this equation"))
        c = np.array(embed_text("What is the capital of Ghana?"))
        sim_ab = float(np.dot(a, b))
        sim_ac = float(np.dot(a, c))
        assert sim_ab > sim_ac


class TestRetrieval:
    def test_retrieves_math_question(self):
        results = retrieve("Solve a linear equation", subject="maths", top_k=3)
        assert len(results) >= 1
        assert all(r["subject"] == "maths" for r in results)

    def test_retrieves_science_question(self):
        results = retrieve("What gas do plants need?", subject="science", top_k=3)
        assert len(results) >= 1
        assert all(r["subject"] == "science" for r in results)

    def test_subject_filter_excludes_others(self):
        results = retrieve("What is the capital?", subject="social_studies", top_k=3)
        assert all(r["subject"] == "social_studies" for r in results)

    def test_top_k_respected(self):
        results = retrieve("photosynthesis", top_k=2)
        assert len(results) <= 2

    def test_result_schema(self):
        results = retrieve("algebra", subject="maths", top_k=1)
        assert len(results) == 1
        keys = {"question_id", "subject", "topic", "difficulty",
                "question_text", "correct_answer", "explanation", "similarity"}
        assert keys.issubset(results[0].keys())

    def test_similarity_is_between_0_and_1(self):
        results = retrieve("trigonometry", subject="maths", top_k=1)
        assert 0.0 <= results[0]["similarity"] <= 1.0


class TestThresholdRetrieval:
    def test_relevant_query_is_grounded(self):
        results, is_grounded = retrieve_with_threshold(
            "What is the powerhouse of the cell?", subject="science", top_k=3
        )
        assert is_grounded is True
        assert len(results) >= 1

    def test_irrelevant_query_not_grounded(self):
        results, is_grounded = retrieve_with_threshold(
            "What is the airspeed velocity of an unladen swallow?",
            subject="science",
            threshold=0.95,
        )
        assert is_grounded is False


class TestAnswerQuery:
    def test_grounded_question_returns_response(self):
        out = answer_query("How do I solve 2x + 5 = 15?", subject="maths", channel="whatsapp")
        assert out["grounded"] is True
        assert out["question_id"] is not None
        assert isinstance(out["response"], str)
        assert len(out["response"]) > 0

    def test_response_respects_whatsapp_limit(self):
        out = answer_query("Tell me about Newton's laws", subject="science", channel="whatsapp")
        assert len(out["response"]) <= 1024

    def test_response_respects_ussd_limit(self):
        out = answer_query("What is photosynthesis?", subject="science", channel="ussd")
        assert len(out["response"]) <= 178

    def test_fallback_when_no_match(self):
        out = answer_query("zzzzzzzz quantum chromodynamics zzzzzzzz", subject=None)
        assert "response" in out
        assert "grounded" in out
