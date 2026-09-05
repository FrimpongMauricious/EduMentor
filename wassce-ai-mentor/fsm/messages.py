"""
fsm/messages.py — User-facing message templates.

All copy in one place for easy review by teachers and tweaking for tone.
Per FR-NFR-15: messages must use simple English appropriate for SHS students.

Each function accepts an optional channel parameter ("whatsapp" or "ussd").
USSD variants are terse enough to fit a single USSD screen (~160 chars).
"""
from fsm.states import SUBJECT_DISPLAY_NAMES

# ─── SUBJECT MENU ─────────────────────────────────────────────────────────────

def greeting(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "WASSCE AI Mentor\n1. Maths\n2. English\n3. Science\n4. Social Studies\n5. My Report"
    return (
        "Welcome to WASSCE AI Mentor!\n"
        "I will help you practise for your WASSCE exams.\n\n"
        "Pick a subject:\n"
        "1. Core Mathematics\n"
        "2. English Language\n"
        "3. Integrated Science\n"
        "4. Social Studies"
    )

#creating the subject selection prompt 
def subject_selection_prompt(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "Pick subject:\n1. Maths\n2. English\n3. Science\n4. Social Studies\n5. My Report"
    return (
        "Pick a subject:\n"
        "1. Core Mathematics\n"
        "2. English Language\n"
        "3. Integrated Science\n"
        "4. Social Studies"
    )


def subject_invalid(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "Invalid. Pick 1-4."
    return "Sorry, I did not understand. Please reply with 1, 2, 3, or 4 to pick a subject."


def subject_confirmed(subject_key: str, channel: str = "whatsapp") -> str:
    display = SUBJECT_DISPLAY_NAMES.get(subject_key, subject_key)
    if channel == "ussd":
        return display
    return f"Great! Let's practise {display}. Sending your first question..."


# ─── QUESTION DELIVERY ────────────────────────────────────────────────────────

def question_delivery(question_text: str, channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return f"{question_text}\n\nAnswer (0=Skip):"
    return f"Question:\n{question_text}\n\nType your answer, or SKIP."


# ─── ANSWER / EXPLANATION ─────────────────────────────────────────────────────

def next_action_prompt(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "1.Next 2.Menu 0.Stop"
    return "Reply NEXT for another, MENU to change subject, or STOP to end."


def build_answer_response(
    evaluation: str,
    is_correct: bool,
    score: int,
    feedback: str,
    correct_ans: str,
    expl_text: str,
    channel: str = "whatsapp",
) -> str:
    """
    Build the complete answer + explanation + navigation block.

    Args:
        evaluation: "correct" | "partial" | "incorrect" | "skip"
        is_correct: grader's is_correct flag (True when score >= threshold)
        score:      0-100 grader score
        feedback:   LLM feedback string (may be empty for exact-match MCQ)
        correct_ans: the model answer
        expl_text:  explanation from corpus
        channel:    "whatsapp" or "ussd"
    """
    if channel == "ussd":
        if evaluation == "skip":
            verdict = "Skipped."
        elif evaluation == "correct" and score == 100:
            verdict = "Correct!"
        elif evaluation in {"correct", "partial"}:
            verdict = f"Score:{score}%"
        else:
            verdict = "Wrong."
        nav = "1.Next 2.Menu 0.Stop"
        return f"{verdict}\n{correct_ans}\n{expl_text}\n{nav}"

    # WhatsApp — full verbose response
    if evaluation == "skip":
        verdict = "Question skipped. Here is the answer for your reference:"
    elif is_correct and score == 100:
        verdict = "Correct! Well done."
    elif is_correct:
        verdict = f"Good attempt! Score: {score}%\n{feedback}"
    elif score > 0:
        verdict = f"Not quite. Score: {score}%\n{feedback}"
    else:
        verdict = "Not quite. Here is the correct answer:"
    nav = next_action_prompt("whatsapp")
    return f"{verdict}\nAnswer: {correct_ans}\nWhy: {expl_text}\n\n{nav}"


# ─── LEGACY HELPERS (kept for backward compatibility) ─────────────────────────

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


# ─── NAVIGATION / META ────────────────────────────────────────────────────────

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


def farewell(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "Thanks! Dial again to restart."
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


# ─── TEST MODULE ──────────────────────────────────────────────────────────────

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


# ─── NAME CAPTURE (first contact only) ───────────────────────────────────────

def name_prompt(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "WASSCE AI Mentor\nEnter your name:"
    return "Welcome to WASSCE AI Mentor! 📚\nWhat is your name?"


def name_invalid(channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return "Enter a valid name:"
    return "Please enter a valid name (2–50 letters)."


def name_accepted_with_menu(name: str, channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return f"Hi {name}!\n1. Maths\n2. English\n3. Science\n4. Social Studies\n5. My Report"
    return (
        f"Hi {name}! 👋\n"
        "Pick a subject:\n"
        "1. Core Mathematics\n"
        "2. English Language\n"
        "3. Integrated Science\n"
        "4. Social Studies"
    )


def welcome_back(name: str, channel: str = "whatsapp") -> str:
    if channel == "ussd":
        return f"Hi {name}!\n1. Maths\n2. English\n3. Science\n4. Social Studies\n5. My Report"
    return (
        f"Welcome back, {name}! 👋\n"
        "Pick a subject:\n"
        "1. Core Mathematics\n"
        "2. English Language\n"
        "3. Integrated Science\n"
        "4. Social Studies"
    )


# ─── QUESTION TYPE SELECTION (WhatsApp only) ──────────────────────────────────

def subject_and_type_prompt(subject_key: str) -> str:
    display = SUBJECT_DISPLAY_NAMES.get(subject_key, subject_key)
    return (
        f"Great! You picked {display}.\n\n"
        "Choose question type:\n"
        "1. Objectives (MCQs)\n"
        "2. Theory\n\n"
        "Type 1 or 2."
    )


def question_type_invalid() -> str:
    return "Please type 1 for Objectives (MCQs) or 2 for Theory."


def no_questions_of_type(question_type: str, channel: str) -> str:
    if channel == "ussd":
        return "No questions found.\n0. Back"
    label = "MCQ" if question_type == "mcq" else "theory"
    return (
        f"No {label} questions available for this subject. "
        f"Try the other type or reply MENU to pick a different subject."
    )


def pool_exhausted_notice(subject_key: str, question_type: str, channel: str) -> str:
    """Shown when a student has correctly answered every eligible question
    for a subject+type in this session, and the pool has just been reset."""
    display = SUBJECT_DISPLAY_NAMES.get(subject_key, subject_key)
    label = "MCQs" if question_type == "mcq" else "theory questions"
    if channel == "ussd":
        return f"Done all {display} {label}! Starting over."
    return f"You've completed all available {display} {label} for now — starting over.\n\n"
