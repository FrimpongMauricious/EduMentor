"""
fsm/answer_evaluator.py — Evaluates a student's answer against the correct answer.

Returns one of: 'correct', 'partial', 'incorrect', 'skip'.
Used by FSM QUESTION_DELIVERY state.
"""
import re
from difflib import SequenceMatcher


def _normalise(text: str) -> str:
    """Lowercase, strip, collapse whitespace, remove most punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\-\+\=\.]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def evaluate_answer(student_answer: str, correct_answer: str) -> str:
    """
    Evaluate the student's response.

    Returns:
        'skip'      — student typed 'skip' or empty
        'correct'   — exact match or fuzzy ratio >= 0.85
        'partial'   — fuzzy ratio between 0.55 and 0.85, OR student answer contains
                      a key token from the correct answer
        'incorrect' — otherwise
    """
    if not student_answer:
        return "skip"

    raw = student_answer.strip().lower()
    if raw in {"skip", "next", "pass"}:
        return "skip"

    student_norm = _normalise(student_answer)
    correct_norm = _normalise(correct_answer)

    if not student_norm or not correct_norm:
        return "incorrect"

    # Exact normalised match
    if student_norm == correct_norm:
        return "correct"

    # Substring match in either direction (handles "5" vs "x = 5").
    # Allow length 1 for pure-digit answers (e.g. "5", "8") to avoid blocking
    # valid single-number responses; require length >= 2 for text to avoid
    # trivial single-letter false positives.
    if student_norm in correct_norm or correct_norm in student_norm:
        if len(student_norm) >= 2 or student_norm.isdigit():
            return "correct"

    # Fuzzy similarity ratio
    ratio = SequenceMatcher(None, student_norm, correct_norm).ratio()

    if ratio >= 0.85:
        return "correct"
    if ratio >= 0.55:
        return "partial"

    # Token overlap — if student answer contains any significant token
    # from the correct answer (length > 3), call it partial.
    correct_tokens = {t for t in correct_norm.split() if len(t) > 3}
    student_tokens = set(student_norm.split())
    if correct_tokens and (correct_tokens & student_tokens):
        return "partial"

    return "incorrect"
