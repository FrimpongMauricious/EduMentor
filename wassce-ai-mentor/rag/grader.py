"""
rag/grader.py — Adaptive answer grading.

Two evaluation modes:
1. MCQ exact-match (fast, deterministic, no API call)
2. LLM-based adaptive grading with partial credit (for theory/short-answer)

For open answers, the LLM rewards CONCEPTUAL UNDERSTANDING — not memorisation
of exact wording. A student who explains the same idea in different words
should get full credit.
"""
import json
import re
import os
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_client = None
_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY env var not set")
        _client = OpenAI(api_key=api_key)
    return _client


# ------------------------------------------------------------------
# Question type detection
# ------------------------------------------------------------------
_MCQ_PATTERN = re.compile(r"\n\s*[A-D]\.\s", re.MULTILINE)


def detect_question_type(question_text: str, explicit_type: Optional[str] = None) -> str:
    """
    Returns "mcq" or "open".
    If explicit_type is provided in the corpus entry, it overrides detection.
    Otherwise, detect by presence of A. B. C. D. pattern.
    """
    if explicit_type in ("mcq", "open"):
        return explicit_type
    if _MCQ_PATTERN.search(question_text or ""):
        return "mcq"
    return "open"


# ------------------------------------------------------------------
# MCQ exact-match grading
# ------------------------------------------------------------------
def _normalise_mcq(s: str) -> str:
    """Lowercase, strip, remove leading 'A.'/'A)' style prefixes."""
    s = (s or "").strip().lower()
    s = re.sub(r"^[a-d][\.\):]\s*", "", s)
    return s.strip()


def grade_mcq(student_answer: str, correct_answer: str) -> dict:
    """
    Returns {score: 100 or 0, is_correct: bool, feedback: str}
    Accepts: "A", "A.", "A. Cattle", "Cattle", "cattle" all match.
    """
    s = _normalise_mcq(student_answer)
    c = _normalise_mcq(correct_answer)

    if s == c:
        return {"score": 100, "is_correct": True, "feedback": "Correct! Well done."}

    # Letter-only match (student typed "A", correct is "A. Cattle")
    if len(student_answer.strip()) == 1 and student_answer.strip().upper() in "ABCD":
        correct_letter = correct_answer.strip()[0].upper() if correct_answer else ""
        if student_answer.strip().upper() == correct_letter:
            return {"score": 100, "is_correct": True, "feedback": "Correct! Well done."}

    # Value-only match (student typed "Cattle", correct is "A. Cattle")
    # Require len >= 2 to prevent single-char substrings (e.g. "a" in "rabbit")
    if (len(s) >= 2 and s in c) or c.endswith(s) or (c.startswith(s) and len(s) > 2):
        return {"score": 100, "is_correct": True, "feedback": "Correct! Well done."}

    return {"score": 0, "is_correct": False, "feedback": ""}


# ------------------------------------------------------------------
# LLM-based adaptive grading for theory/short-answer
# ------------------------------------------------------------------
_GRADING_PROMPT = """You are a WAEC-standard examiner grading a Ghanaian SHS student's answer.

CRITICAL GRADING PRINCIPLES:
1. Reward CONCEPTUAL UNDERSTANDING, not memorisation of exact wording.
2. If the student explains the same idea in different words, give FULL credit.
3. Synonyms, paraphrasing, simpler vocabulary, and informal English are ALL acceptable.
4. Examples of EQUIVALENT answers that should score 100%:
   - Expected: "Photosynthesis converts CO2 and H2O into glucose using light energy."
     Student:  "Plants use sunlight and water to make food."  -> 100% (same concept)
   - Expected: "The hypothalamus controls body temperature."
     Student:  "It is the hypothalamus."  -> 100% (correct identification)
   - Expected: "Bunsen burner, delivery tube, gas jar, ammonia gas"
     Student:  "A burner, a tube to carry gas, a container, and the gas itself" -> 80% (got 3 of 4 parts conceptually)
5. Only deduct points when the student is FACTUALLY WRONG, INCOMPLETE, or MISSING KEY CONCEPTS.
6. Bad spelling and grammar should NOT reduce the score unless they cause meaning to be unclear.
7. Be ENCOURAGING. Students should feel motivated to keep learning.

Question: {question}

Expected answer (model answer): {expected}

Student's answer: {student}

Grade on a 0-100 scale:
- 100 = Demonstrates full conceptual understanding (any wording)
- 80-99 = Captures main concepts, minor gaps
- 60-79 = Captures core idea, missing some important points
- 40-59 = Partial understanding, but key concepts missing
- 20-39 = Shows some related knowledge, mostly off-track
- 1-19 = Attempt made but largely incorrect
- 0 = Wrong, unrelated, or no answer

Respond with ONLY valid JSON (no markdown, no extra text):
{{
  "score": <integer 0-100>,
  "is_correct": <true if score >= 60, else false>,
  "key_points_got": [<list of concepts the student correctly explained, even if in different words>],
  "key_points_missed": [<list of important concepts NOT mentioned>],
  "feedback": "<one short encouraging sentence (max 25 words). If the answer is mostly right, congratulate. If partial, highlight what's good before what's missing.>"
}}"""


def grade_open(question: str, student_answer: str, correct_answer: str) -> dict:
    """
    Use LLM to grade open-ended (theory/short-answer) responses.
    Returns {score, is_correct, key_points_got, key_points_missed, feedback}.
    """
    student_answer = (student_answer or "").strip()

    if not student_answer:
        return {
            "score": 0, "is_correct": False,
            "key_points_got": [], "key_points_missed": ["No answer provided"],
            "feedback": "You did not provide an answer. Try again!"
        }

    if len(student_answer) < 2:
        return {
            "score": 0, "is_correct": False,
            "key_points_got": [], "key_points_missed": ["Answer too short"],
            "feedback": "Your answer is too short. Try writing a full response."
        }

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_MODEL_NAME,
            messages=[{
                "role": "user",
                "content": _GRADING_PROMPT.format(
                    question=question[:500],
                    expected=correct_answer[:800],
                    student=student_answer[:500]
                )
            }],
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|\s*```$", "", raw)
        result = json.loads(raw)
        result["score"] = max(0, min(100, int(result.get("score", 0))))
        result["is_correct"] = bool(result.get("is_correct", result["score"] >= 60))
        return result
    except Exception as e:
        logger.warning(f"LLM grading failed, falling back: {e}")
        s_lower = student_answer.lower().strip()
        c_lower = correct_answer.lower().strip()
        if s_lower == c_lower or s_lower in c_lower:
            return {
                "score": 100, "is_correct": True,
                "key_points_got": ["Exact match"], "key_points_missed": [],
                "feedback": "Correct!"
            }
        return {
            "score": 0, "is_correct": False,
            "key_points_got": [], "key_points_missed": ["Unable to grade"],
            "feedback": "Could not evaluate your answer. Try rephrasing."
        }


# ------------------------------------------------------------------
# Public unified API
# ------------------------------------------------------------------
def grade_answer(question_text: str, student_answer: str,
                 correct_answer: str, explicit_type: Optional[str] = None) -> dict:
    """
    Main entry point. Routes to MCQ or open grading based on detected type.
    Returns a dict with at least: score, is_correct, feedback, question_type.
    """
    qtype = detect_question_type(question_text, explicit_type)

    if qtype == "mcq":
        return {**grade_mcq(student_answer, correct_answer), "question_type": "mcq"}

    result = grade_open(question_text, student_answer, correct_answer)
    return {**result, "question_type": "open"}
