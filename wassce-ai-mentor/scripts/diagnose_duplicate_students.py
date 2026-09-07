"""
scripts/diagnose_duplicate_students.py — READ-ONLY diagnostic for duplicate
Student rows (same real person, two student_ids).

Root cause under investigation: student_id = sha256(normalise_phone(raw))
(utils/phone.phone_to_student_id). WhatsApp and USSD both derive it the same
way, so two channels alone do not cause a duplicate. What DOES cause one:
Twilio occasionally delivers a non-numeric "From" identifier instead of a
real phone number for WhatsApp linked-device/business-account senders (see
commit 51a1c6b, e.g. "whatsapp:GH.2142378006405521"). api/routes/whatsapp.py
computes student_id from that raw value BEFORE validating it — only the
*storage* of phone_number is gated on is_valid_phone(), not the identity
hash itself — so a garbled "From" produces a second, distinct student_id for
the same real person, with phone_number left NULL because it fails
validation.

This script only runs SELECT statements. It makes no writes and is safe to
run against production to confirm/quantify the above.

Usage:
    python scripts/diagnose_duplicate_students.py --name "Mauricious Frimpong"
    python scripts/diagnose_duplicate_students.py --systemic
"""
import argparse
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db.database import engine


NAME_MATCH_SQL = """
    SELECT student_id, name, phone_number, channel, registered_at, last_seen_at,
           session_count
    FROM students
    WHERE LOWER(TRIM(name)) = LOWER(TRIM(:name))
    ORDER BY registered_at ASC
"""

INTERACTION_COUNT_SQL = "SELECT COUNT(*) AS n FROM interactions WHERE student_id = :sid"

PERFORMANCE_SQL = """
    SELECT COALESCE(SUM(attempts), 0) AS attempts, COALESCE(SUM(correct), 0) AS correct
    FROM performance_vectors
    WHERE student_id = :sid
"""

FIRST_LAST_INTERACTION_SQL = """
    SELECT MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
    FROM interactions
    WHERE student_id = :sid
"""

# Candidates for a hidden channel-switch/garbled-identifier duplicate: a
# nameless, phoneless row whose entire interaction window falls within
# CANDIDATE_WINDOW_MINUTES of another (named or unnamed) row's window —
# i.e. plausibly the same real usage session split across two student_ids.
CANDIDATE_WINDOW_MINUTES = 30

NAMELESS_NO_PHONE_SQL = """
    SELECT student_id, name, phone_number, channel, registered_at, last_seen_at
    FROM students
    WHERE phone_number IS NULL
    ORDER BY registered_at ASC
"""

ALL_STUDENTS_SQL = """
    SELECT student_id, name, phone_number, channel, registered_at, last_seen_at
    FROM students
    ORDER BY registered_at ASC
"""


def _row_summary(conn, row) -> dict:
    sid = row.student_id
    interactions = conn.execute(text(INTERACTION_COUNT_SQL), {"sid": sid}).scalar_one()
    perf = conn.execute(text(PERFORMANCE_SQL), {"sid": sid}).one()
    window = conn.execute(text(FIRST_LAST_INTERACTION_SQL), {"sid": sid}).one()
    attempts, correct = perf.attempts, perf.correct
    accuracy = round(100.0 * correct / attempts, 1) if attempts else None
    return {
        "student_id": sid,
        "name": row.name,
        "phone_number": row.phone_number,
        "channel": row.channel,
        "registered_at": row.registered_at,
        "last_seen_at": row.last_seen_at,
        "interaction_count": interactions,
        "questions_attempted": attempts,
        "accuracy_pct": accuracy,
        "first_interaction": window.first_ts,
        "last_interaction": window.last_ts,
    }


def _print_summary(s: dict) -> None:
    print(f"  student_id        : {s['student_id']}")
    print(f"  name              : {s['name']!r}")
    print(f"  phone_number      : {s['phone_number']!r}")
    print(f"  channel           : {s['channel']}")
    print(f"  registered_at     : {s['registered_at']}")
    print(f"  last_seen_at      : {s['last_seen_at']}")
    print(f"  interaction_count : {s['interaction_count']}")
    print(f"  questions/accuracy: {s['questions_attempted']} attempted, "
          f"{s['accuracy_pct']}% accuracy")
    print(f"  interaction window: {s['first_interaction']} .. {s['last_interaction']}")
    print()


def diagnose_name(name: str) -> None:
    print(f"=== Students matching name (case/whitespace-insensitive): {name!r} ===\n")
    with engine.connect() as conn:
        rows = conn.execute(text(NAME_MATCH_SQL), {"name": name}).all()
        if not rows:
            print("No matching rows found.")
            return
        summaries = [_row_summary(conn, r) for r in rows]

    for s in summaries:
        _print_summary(s)

    if len(summaries) > 1:
        print(f"Found {len(summaries)} rows for the same name.")
        phones = {s["phone_number"] for s in summaries if s["phone_number"]}
        no_phone = [s for s in summaries if not s["phone_number"]]
        print(f"  - Rows with a phone_number on file : {len(phones)}")
        print(f"  - Rows with NO phone_number on file: {len(no_phone)}")
        if no_phone and phones:
            print(
                "  -> Consistent with the garbled-Twilio-identifier mechanism "
                "described above: one row is keyed on the real phone hash "
                "(has phone_number), the other on a hash of a non-numeric "
                "'From' value Twilio delivered instead (rejected by "
                "is_valid_phone(), so phone_number stayed NULL)."
            )


def diagnose_systemic() -> None:
    print("=== Systemic check: nameless/phoneless rows that might be hidden duplicates ===\n")
    with engine.connect() as conn:
        all_rows = conn.execute(text(ALL_STUDENTS_SQL)).all()
        nameless_no_phone = [r for r in all_rows if r.phone_number is None]

    if not nameless_no_phone:
        print("No rows with phone_number IS NULL - nothing to check.")
        return

    print(f"{len(nameless_no_phone)} row(s) with phone_number IS NULL out of "
          f"{len(all_rows)} total students.\n")

    with engine.connect() as conn:
        candidates = [_row_summary(conn, r) for r in nameless_no_phone]
        others = [_row_summary(conn, r) for r in all_rows if r.phone_number is not None]

    flagged = []
    for cand in candidates:
        if cand["first_interaction"] is None:
            continue
        for other in others:
            if other["first_interaction"] is None:
                continue
            delta = abs((cand["first_interaction"] - other["first_interaction"]).total_seconds())
            if delta <= CANDIDATE_WINDOW_MINUTES * 60:
                flagged.append((cand, other, delta / 60))

    if not flagged:
        print(
            "No phoneless row's interaction window overlaps a phoned row's "
            f"window within {CANDIDATE_WINDOW_MINUTES} minutes. This does NOT "
            "rule out other duplicates (e.g. two people who never used the "
            "bot at close times), but no timing-based candidates were found."
        )
        return

    print(f"Timing-based duplicate candidates (within {CANDIDATE_WINDOW_MINUTES} min):\n")
    for cand, other, minutes in flagged:
        print(f"  Phoneless {cand['student_id'][:16]}... (name={cand['name']!r}, "
              f"{cand['interaction_count']} interactions) "
              f"is within {minutes:.1f} min of "
              f"{other['student_id'][:16]}... (name={other['name']!r}, "
              f"phone={other['phone_number']!r}, {other['interaction_count']} interactions)")
    print(
        "\nThese are CANDIDATES only - confirm with the actual student before "
        "merging (e.g. ask them, or compare subject/topic activity patterns)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="Exact (case/whitespace-insensitive) name to look up")
    parser.add_argument(
        "--systemic", action="store_true",
        help="Scan for other potential hidden duplicates among phoneless rows",
    )
    args = parser.parse_args()

    if not args.name and not args.systemic:
        parser.error("Pass --name \"Full Name\" and/or --systemic")

    if args.name:
        diagnose_name(args.name)
        print()
    if args.systemic:
        diagnose_systemic()
