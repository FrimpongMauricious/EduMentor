"""
fsm/messages.py — User-facing message templates.

All copy in one place for easy review by teachers and tweaking for tone.
Per FR-NFR-15: messages must use simple English appropriate for SHS students.
"""
from fsm.states import SUBJECT_DISPLAY_NAMES


def greeting() -> str:
    return (
        "Welcome to WASSCE AI Mentor!\n"
        "I will help you practise for your WASSCE exams.\n\n"
        "Pick a subject:\n"
        "1. Core Mathematics\n"
        "2. English Language\n"
        "3. Integrated Science\n"
        "4. Social Studies"
    )


def subject_selection_prompt() -> str:
    return (
        "Pick a subject:\n"
        "1. Core Mathematics\n"
        "2. English Language\n"
        "3. Integrated Science\n"
        "4. Social Studies"
    )


def subject_invalid() -> str:
    return (
        "Sorry, I did not understand. Please reply with 1, 2, 3, or 4 to pick a subject."
    )


def subject_confirmed(subject_key: str) -> str:
    display = SUBJECT_DISPLAY_NAMES.get(subject_key, subject_key)
    return f"Great! Let's practise {display}. Sending your first question..."


def question_delivery(question_text: str) -> str:
    return f"Question:\n{question_text}\n\nType your answer, or SKIP."


def answer_correct() -> str:
    return "Correct! Well done."


def answer_partial() -> str:
    return "Close! You got part of it. Here is the full answer:"


def answer_incorrect() -> str:
    return "Not quite. Here is the correct answer:"


def answer_skipped() -> str:
    return "Question skipped. Here is the answer for your reference:"


def explanation_block(correct_answer: str, explanation: str) -> str:
    return f"Answer: {correct_answer}\nWhy: {explanation}"


def next_action_prompt() -> str:
    return "Reply NEXT for another, MENU to change subject, or STOP to end."


def session_summary(attempted: int, correct: int, weakest_subject: str | None) -> str:
    accuracy = round((correct / attempted) * 100) if attempted else 0
    msg = f"Session Summary:\nQuestions attempted: {attempted}\nCorrect: {correct} ({accuracy}%)"
    if weakest_subject:
        msg += f"\nFocus area: {SUBJECT_DISPLAY_NAMES.get(weakest_subject, weakest_subject)}"
    msg += "\n\nReply MENU to start again or STOP to exit."
    return msg


def help_message() -> str:
    return (
        "Commands:\n"
        "NEXT - next question\n"
        "MENU - change subject\n"
        "SCORE - session summary\n"
        "STARTTEST - take the WASSCE test\n"
        "STOP - end session"
    )


def farewell() -> str:
    return "Goodbye! Come back any time to keep practising. Good luck with WASSCE!"


def fallback_unknown() -> str:
    return (
        "Sorry, I did not understand. "
        "Reply HELP to see what you can do, or MENU to pick a subject."
    )


def session_expired() -> str:
    return "Your last session timed out. Let's start again!"


def low_confidence_fallback() -> str:
    return (
        "I could not find a WASSCE question on that. "
        "Reply MENU to pick a subject."
    )


def test_intro(test_type: str, total_questions: int) -> str:
    label = "PRE-TEST" if test_type == "pre" else "POST-TEST"
    return (
        f"📝 {label} — {total_questions} questions\n"
        f"5 per subject, no help, no skipping.\n"
        f"Type your answer, then send.\n"
        f"Type CANCEL to exit.\n\n"
        f"Ready? Send any reply to begin."
    )


def test_question(qnum: int, total: int, subject: str, question_text: str) -> str:
    subject_names = {
        "maths": "Maths",
        "english": "English",
        "science": "Science",
        "social_studies": "Social Studies",
    }
    return (
        f"Q{qnum}/{total} [{subject_names.get(subject, subject)}]\n"
        f"{question_text}\n\n"
        f"Type your answer."
    )


def test_already_complete() -> str:
    return (
        "You have already completed both the pre-test and post-test. "
        "Reply MENU to keep practising."
    )


def test_cancelled() -> str:
    return "Test cancelled. Your progress was not saved.\nReply MENU to continue."
