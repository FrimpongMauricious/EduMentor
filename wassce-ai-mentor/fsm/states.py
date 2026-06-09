"""
fsm/states.py — FSM state definitions and valid transitions.

Implements FR-FSM-01 (6 states + transitions).
"""
from enum import Enum


class FSMState(str, Enum):
    GREETING = "GREETING"
    SUBJECT_SELECTION = "SUBJECT_SELECTION"
    QUESTION_DELIVERY = "QUESTION_DELIVERY"
    ANSWER_EVALUATION = "ANSWER_EVALUATION"
    EXPLANATION = "EXPLANATION"
    SESSION_SUMMARY = "SESSION_SUMMARY"
    TEST_IN_PROGRESS = "TEST_IN_PROGRESS"


# Valid transitions from each state (used for validation)
VALID_TRANSITIONS: dict[FSMState, set[FSMState]] = {
    FSMState.GREETING:          {FSMState.SUBJECT_SELECTION, FSMState.GREETING, FSMState.TEST_IN_PROGRESS},
    FSMState.SUBJECT_SELECTION: {FSMState.QUESTION_DELIVERY, FSMState.SUBJECT_SELECTION, FSMState.GREETING, FSMState.TEST_IN_PROGRESS},
    FSMState.QUESTION_DELIVERY: {FSMState.ANSWER_EVALUATION, FSMState.QUESTION_DELIVERY, FSMState.SUBJECT_SELECTION, FSMState.GREETING},
    FSMState.ANSWER_EVALUATION: {FSMState.EXPLANATION},
    FSMState.EXPLANATION:       {FSMState.QUESTION_DELIVERY, FSMState.SUBJECT_SELECTION, FSMState.SESSION_SUMMARY, FSMState.GREETING, FSMState.TEST_IN_PROGRESS},
    FSMState.SESSION_SUMMARY:   {FSMState.GREETING, FSMState.SUBJECT_SELECTION},
    FSMState.TEST_IN_PROGRESS:  {FSMState.TEST_IN_PROGRESS, FSMState.GREETING, FSMState.SUBJECT_SELECTION},
}


# Subject selection mapping
SUBJECTS = {
    "1": "maths",
    "2": "english",
    "3": "science",
    "4": "social_studies",
    "maths": "maths",
    "math": "maths",
    "mathematics": "maths",
    "english": "english",
    "science": "science",
    "integrated science": "science",
    "social studies": "social_studies",
    "social": "social_studies",
}

SUBJECT_DISPLAY_NAMES = {
    "maths": "Core Mathematics",
    "english": "English Language",
    "science": "Integrated Science",
    "social_studies": "Social Studies",
}


# Global commands valid in ANY state (FR-08, FR-09)
GLOBAL_COMMANDS = {"STOP", "QUIT", "HELP", "MENU", "EXIT", "STARTTEST"}


def is_valid_transition(from_state: FSMState, to_state: FSMState) -> bool:
    """Check whether a transition is allowed."""
    return to_state in VALID_TRANSITIONS.get(from_state, set())


def parse_subject(text: str) -> str | None:
    """
    Parse free text or menu number into a canonical subject key.
    Returns None if no match.
    """
    if not text:
        return None
    return SUBJECTS.get(text.strip().lower())
