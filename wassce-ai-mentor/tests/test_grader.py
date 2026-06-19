"""Tests for rag/grader.py"""
import os
import pytest
from rag.grader import detect_question_type, grade_mcq, grade_open, grade_answer

requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set"
)


class TestQuestionTypeDetection:
    def test_detects_mcq_with_options(self):
        q = "What is 2+2?\nA. 3\nB. 4\nC. 5\nD. 6"
        assert detect_question_type(q) == "mcq"

    def test_detects_open_question(self):
        q = "Explain the process of photosynthesis."
        assert detect_question_type(q) == "open"

    def test_explicit_type_overrides(self):
        q = "A. apple"
        assert detect_question_type(q, explicit_type="open") == "open"


class TestMCQGrading:
    def test_letter_match(self):
        r = grade_mcq("D", "D. Rabbit")
        assert r["is_correct"] is True
        assert r["score"] == 100

    def test_full_text_match(self):
        r = grade_mcq("D. Rabbit", "D. Rabbit")
        assert r["is_correct"] is True

    def test_value_only_match(self):
        r = grade_mcq("Rabbit", "D. Rabbit")
        assert r["is_correct"] is True

    def test_case_insensitive(self):
        r = grade_mcq("rabbit", "D. Rabbit")
        assert r["is_correct"] is True

    def test_wrong_answer(self):
        r = grade_mcq("A", "D. Rabbit")
        assert r["is_correct"] is False
        assert r["score"] == 0


class TestOpenGrading:
    @requires_openai
    def test_correct_open_answer(self):
        q = "What is photosynthesis?"
        correct = "Photosynthesis is the process by which plants make food from carbon dioxide, water, and sunlight using chlorophyll."
        student = "It is how plants use sunlight and water to make food."
        r = grade_open(q, student, correct)
        assert r["score"] >= 50
        assert "feedback" in r

    def test_empty_answer_handled(self):
        r = grade_open("Q", "", "expected answer")
        assert r["score"] == 0
        assert r["is_correct"] is False

    @requires_openai
    def test_paraphrased_answer_gets_full_credit(self):
        """Critical: answering correctly in own words should score high."""
        q = "What is photosynthesis?"
        expected = "Photosynthesis is the process by which green plants convert carbon dioxide and water into glucose using sunlight, with chlorophyll as the catalyst."
        student = "It is how plants use sunlight, water and air to produce food."
        r = grade_open(q, student, expected)
        assert r["score"] >= 80, f"Paraphrased answer only scored {r['score']}%"
        assert r["is_correct"] is True

    @requires_openai
    def test_simple_english_gets_credit(self):
        """Student using simpler vocabulary should not be penalised."""
        q = "Why does aluminium not corrode?"
        expected = "Aluminium forms a thin protective layer of aluminium oxide on its surface which prevents further reaction."
        student = "Because a covering forms on it that stops more rusting."
        r = grade_open(q, student, expected)
        assert r["score"] >= 60, f"Simple English answer only scored {r['score']}%"


class TestUnifiedGrader:
    def test_routes_mcq(self):
        q = "Pick one:\nA. cat\nB. dog"
        r = grade_answer(q, "A", "A. cat")
        assert r["question_type"] == "mcq"
        assert r["is_correct"] is True

    def test_routes_open(self):
        q = "Define democracy"
        r = grade_answer(q, "", "expected")
        assert r["question_type"] == "open"
