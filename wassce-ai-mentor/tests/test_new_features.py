"""
tests/test_new_features.py — Tests for the two new features:
  Feature 1: MCQ vs Theory question type selection (WhatsApp only)
  Feature 2: USSD '99' pagination for truncated responses

Run via: pytest tests/test_new_features.py -v
"""
import json
import re
import uuid
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db import models  # noqa: F401 — register models with Base
from rag.grader import detect_question_type
from fsm.ussd_pagination import paginate_ussd, get_next_ussd_chunk, has_pending_pagination
from fsm.dialogue_manager import handle_message
from fsm.states import FSMState


# ─── In-memory test DB fixture ─────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _sid() -> str:
    """64-char hex student ID."""
    return uuid.uuid4().hex + uuid.uuid4().hex


# ===========================================================================
# FEATURE 1: MCQ vs Theory question type selection
# ===========================================================================

class TestQuestionTypeDetection:
    """Verify that detect_question_type correctly classifies corpus entries."""

    def test_mcq_question_detected(self):
        q = "What is 2+2?\nA. 3\nB. 4\nC. 5\nD. 6"
        assert detect_question_type(q) == "mcq"

    def test_theory_question_detected(self):
        q = "Explain the process of photosynthesis."
        assert detect_question_type(q) == "open"

    def test_explicit_type_mcq_overrides_pattern(self):
        """explicit_type='mcq' wins even if text has no option pattern."""
        assert detect_question_type("No options here", explicit_type="mcq") == "mcq"

    def test_explicit_type_open_overrides_pattern(self):
        """explicit_type='open' wins even if text looks like MCQ."""
        assert detect_question_type("Which?\nA. cat\nB. dog", explicit_type="open") == "open"


class TestCorpusTypeSplit:
    """Verify that the corpus actually contains both MCQ and theory entries."""

    def test_corpus_has_mcq_and_theory(self):
        with open("data/corpus/wassce_qa.json", encoding="utf-8") as f:
            corpus = json.load(f)

        mcqs = [e for e in corpus if detect_question_type(e["question_text"]) == "mcq"]
        theories = [e for e in corpus if detect_question_type(e["question_text"]) == "open"]

        assert len(mcqs) > 0, "Corpus must contain MCQ entries"
        assert len(theories) > 0, "Corpus must contain theory entries"

    def test_mcq_entries_have_option_pattern(self):
        with open("data/corpus/wassce_qa.json", encoding="utf-8") as f:
            corpus = json.load(f)

        mcqs = [e for e in corpus if detect_question_type(e["question_text"]) == "mcq"]
        pattern = re.compile(r"\n\s*[A-D]\.\s", re.MULTILINE)
        for entry in mcqs[:10]:
            assert pattern.search(entry["question_text"]), (
                f"MCQ entry missing A./B./C./D. pattern: {entry['question_text'][:80]}"
            )

    def test_theory_entries_lack_option_pattern(self):
        with open("data/corpus/wassce_qa.json", encoding="utf-8") as f:
            corpus = json.load(f)

        theories = [e for e in corpus if detect_question_type(e["question_text"]) == "open"]
        pattern = re.compile(r"\n\s*[A-D]\.\s", re.MULTILINE)
        for entry in theories[:10]:
            assert not pattern.search(entry["question_text"]), (
                f"Theory entry has MCQ pattern: {entry['question_text'][:80]}"
            )


class TestWhatsAppTypeSelectionFlow:
    """
    Feature 1: WhatsApp-specific FSM flow.
    After subject selection, WhatsApp shows a type-selection prompt.
    """

    def test_subject_selection_leads_to_type_prompt(self, db):
        """On WhatsApp, picking a subject should ask for MCQ or Theory."""
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "1")  # Maths
        assert result.new_state == FSMState.QUESTION_TYPE_SELECTION
        assert "1" in result.response  # option 1 present
        assert "2" in result.response  # option 2 present
        assert result.question_id is None  # no question yet

    def test_type_prompt_mentions_objectives_and_theory(self, db):
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        result = handle_message(db, sid, "whatsapp", "2")  # English
        assert "Objectives" in result.response or "MCQ" in result.response.upper()
        assert "Theory" in result.response

    def test_selecting_1_serves_mcq(self, db):
        """Selecting type '1' on WhatsApp must deliver an MCQ question."""
        from rag.retriever import get_by_id
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # Maths → type prompt
        result = handle_message(db, sid, "whatsapp", "1")  # MCQ
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id is not None
        question = get_by_id(result.question_id)
        assert question is not None
        assert detect_question_type(question["question_text"]) == "mcq"

    def test_selecting_2_serves_theory(self, db):
        """Selecting type '2' on WhatsApp must deliver a theory question."""
        from rag.retriever import get_by_id
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "3")  # Science → type prompt
        result = handle_message(db, sid, "whatsapp", "2")  # Theory
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id is not None
        question = get_by_id(result.question_id)
        assert question is not None
        assert detect_question_type(question["question_text"]) == "open"

    def test_invalid_type_input_stays_in_type_selection(self, db):
        """Typing something other than 1 or 2 should re-prompt."""
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # Maths → type prompt
        result = handle_message(db, sid, "whatsapp", "banana")
        assert result.new_state == FSMState.QUESTION_TYPE_SELECTION
        assert "1" in result.response and "2" in result.response

    def test_menu_from_type_selection_goes_to_subject_selection(self, db):
        """MENU is a global command and must work from QUESTION_TYPE_SELECTION."""
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # → QUESTION_TYPE_SELECTION
        result = handle_message(db, sid, "whatsapp", "MENU")
        assert result.new_state == FSMState.SUBJECT_SELECTION

    def test_next_preserves_question_type_mcq(self, db):
        """After answering, NEXT must serve another MCQ if that was selected."""
        from rag.retriever import get_by_id
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")  # Maths → type prompt
        handle_message(db, sid, "whatsapp", "1")  # MCQ → question
        handle_message(db, sid, "whatsapp", "B")  # answer → EXPLANATION
        result = handle_message(db, sid, "whatsapp", "NEXT")
        if result.new_state == FSMState.QUESTION_DELIVERY:
            assert result.question_id is not None
            question = get_by_id(result.question_id)
            assert question is not None
            assert detect_question_type(question["question_text"]) == "mcq"

    def test_next_preserves_question_type_theory(self, db):
        """After answering, NEXT must serve another theory question if that was selected."""
        from rag.retriever import get_by_id
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "3")  # Science → type prompt
        handle_message(db, sid, "whatsapp", "2")  # Theory → question
        handle_message(db, sid, "whatsapp", "Plants use sunlight to make food")
        result = handle_message(db, sid, "whatsapp", "NEXT")
        if result.new_state == FSMState.QUESTION_DELIVERY:
            assert result.question_id is not None
            question = get_by_id(result.question_id)
            assert question is not None
            assert detect_question_type(question["question_text"]) == "open"


class TestUSSDSkipsTypeSelection:
    """
    Feature 1: USSD must skip type selection entirely and always serve MCQs.
    """

    def test_ussd_subject_selection_delivers_mcq_directly(self, db):
        """On USSD, subject selection must go straight to a question (no type prompt)."""
        sid = _sid()
        handle_message(db, sid, "ussd", "")    # GREETING → SUBJECT_SELECTION
        result = handle_message(db, sid, "ussd", "1")   # Maths → MCQ immediately
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id is not None, "USSD must deliver a question immediately"

    def test_ussd_always_gets_mcq(self, db):
        """Questions delivered over USSD must be MCQs."""
        from rag.retriever import get_by_id
        sid = _sid()
        handle_message(db, sid, "ussd", "")
        result = handle_message(db, sid, "ussd", "1")
        assert result.new_state == FSMState.QUESTION_DELIVERY
        assert result.question_id is not None
        # Verify via actual question metadata (response may be paginated/truncated)
        question = get_by_id(result.question_id)
        assert question is not None
        assert detect_question_type(question["question_text"]) == "mcq"

    def test_ussd_never_shows_type_prompt(self, db):
        """The QUESTION_TYPE_SELECTION state is never reached on USSD."""
        sid = _sid()
        handle_message(db, sid, "ussd", "")
        result = handle_message(db, sid, "ussd", "1")
        assert result.new_state != FSMState.QUESTION_TYPE_SELECTION


# ===========================================================================
# FEATURE 2: USSD '99' pagination
# ===========================================================================

class TestUSSDPaginationUnit:
    """Unit tests for the pagination helper functions (pure dict-based API)."""

    def test_short_text_not_paginated(self):
        session = {}
        result = paginate_ussd("Short text here", session)
        assert result == "Short text here"
        assert "ussd_full_text" not in session
        assert "ussd_text_offset" not in session

    def test_text_exactly_at_limit_not_paginated(self):
        session = {}
        text = "A" * 150
        result = paginate_ussd(text, session)
        assert result == text
        assert "ussd_full_text" not in session

    def test_long_text_truncated_with_hint(self):
        session = {}
        long_text = "A" * 200
        result = paginate_ussd(long_text, session)
        assert "99. More" in result
        assert len(result) <= 165  # 150 chars + "\n99. More" (9 chars)
        assert session["ussd_full_text"] == long_text
        assert session["ussd_text_offset"] == 150

    def test_first_chunk_is_correct_slice(self):
        session = {}
        long_text = "X" * 300
        result = paginate_ussd(long_text, session)
        first_chunk = result.replace("\n99. More", "")
        assert first_chunk == "X" * 150

    def test_99_returns_second_chunk(self):
        full = "A" * 350
        session = {"ussd_full_text": full, "ussd_text_offset": 150}
        result = get_next_ussd_chunk(session)
        assert len(result) > 0
        assert "99. More" in result  # still more text remaining
        assert session["ussd_text_offset"] == 300  # advanced by 150

    def test_last_chunk_clears_state(self):
        """Final chunk (≤150 chars remaining) must remove pagination keys."""
        full = "A" * 200
        session = {"ussd_full_text": full, "ussd_text_offset": 150}
        result = get_next_ussd_chunk(session)
        # Only 50 chars remain — fits in one chunk
        assert result == "A" * 50
        assert "99. More" not in result
        assert "ussd_full_text" not in session
        assert "ussd_text_offset" not in session

    def test_has_pending_pagination_true(self):
        session = {"ussd_full_text": "abc", "ussd_text_offset": 10}
        assert has_pending_pagination(session) is True

    def test_has_pending_pagination_false_empty(self):
        assert has_pending_pagination({}) is False

    def test_has_pending_pagination_false_zero_offset(self):
        """Offset=0 means nothing sent yet — not considered 'pending'."""
        assert has_pending_pagination({"ussd_full_text": "abc", "ussd_text_offset": 0}) is False

    def test_paginate_clears_stale_state(self):
        """Calling paginate_ussd on short text clears any leftover state."""
        session = {"ussd_full_text": "old", "ussd_text_offset": 50}
        result = paginate_ussd("short", session)
        assert result == "short"
        assert "ussd_full_text" not in session
        assert "ussd_text_offset" not in session

    def test_multiple_chunks_cover_full_text(self):
        """Repeated calls to get_next_ussd_chunk must cover the entire original text."""
        full = "B" * 400
        session = {}
        first = paginate_ussd(full, session)
        collected = first.replace("\n99. More", "")

        while has_pending_pagination(session):
            chunk = get_next_ussd_chunk(session)
            collected += chunk.replace("\n99. More", "")

        assert collected == full


class TestUSSD99Integration:
    """Integration tests: '99' command flows through the FSM correctly."""

    def test_99_delivers_next_chunk(self, db):
        """Typing '99' after a paginated question returns the next chunk."""
        sid = _sid()
        handle_message(db, sid, "ussd", "")    # → SUBJECT_SELECTION
        # Pick Maths; the response may be long enough to paginate.
        # We force the scenario by patching no assumption — just test the '99' path.
        handle_message(db, sid, "ussd", "1")   # → QUESTION_DELIVERY

        # If the question was paginated, '99' should return a chunk.
        # If not paginated (short question), '99' acts as a regular answer.
        from db.models import SessionRow
        session_row = db.query(SessionRow).filter(SessionRow.student_id == sid).first()
        meta = json.loads(session_row.session_meta or "{}")

        if has_pending_pagination(meta):
            result = handle_message(db, sid, "ussd", "99")
            # After '99' the state should not change
            assert result.new_state == FSMState.QUESTION_DELIVERY
            assert len(result.response) > 0

    def test_menu_clears_pagination(self, db):
        """MENU during pagination must clear pagination state and go to SUBJECT_SELECTION."""
        sid = _sid()
        handle_message(db, sid, "ussd", "")
        handle_message(db, sid, "ussd", "1")   # → QUESTION_DELIVERY

        # Manually inject pagination state into the session to simulate a long question.
        from db.models import SessionRow
        session_row = db.query(SessionRow).filter(SessionRow.student_id == sid).first()
        fake_meta = {
            "question_type": "mcq",
            "ussd_full_text": "X" * 300,
            "ussd_text_offset": 150,
        }
        session_row.session_meta = json.dumps(fake_meta)
        db.commit()

        result = handle_message(db, sid, "ussd", "MENU")
        assert result.new_state == FSMState.SUBJECT_SELECTION

        # Verify pagination state was cleared
        db.refresh(session_row)
        meta_after = json.loads(session_row.session_meta or "{}")
        assert "ussd_full_text" not in meta_after
        assert "ussd_text_offset" not in meta_after

    def test_ussd_response_length_within_limit(self, db):
        """Every USSD response (including first chunk) must be ≤178 chars (before CON prefix)."""
        sid = _sid()
        handle_message(db, sid, "ussd", "")
        result = handle_message(db, sid, "ussd", "1")
        # 178 = 182 (USSD max) - 4 ("CON ")
        assert len(result.response) <= 178, (
            f"USSD response too long ({len(result.response)} chars): {result.response[:60]}..."
        )

    def test_whatsapp_response_never_paginated(self, db):
        """WhatsApp responses must never be chunked — full text always delivered."""
        sid = _sid()
        handle_message(db, sid, "whatsapp", "Hi")
        handle_message(db, sid, "whatsapp", "1")   # → type selection
        result = handle_message(db, sid, "whatsapp", "1")   # → MCQ
        # WhatsApp response should NOT contain the USSD pagination hint
        assert "99. More" not in result.response
