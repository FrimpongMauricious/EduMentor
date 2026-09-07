"""
scripts/merge_duplicate_student.py — Merge two Student rows confirmed to be
the same real person into one canonical record.

REVIEW BEFORE RUNNING. This touches real student data. Nothing is written
until you pass --execute; without it, the script only prints the merge plan
(a dry run) and makes no database changes.

Background: two Student rows can exist for the same person because
student_id = sha256(normalise_phone(raw_from_value)), and a Twilio WhatsApp
webhook occasionally delivers a non-numeric "From" identifier instead of the
sender's real phone number (linked-device/business-account quirk — see
commit 51a1c6b). That garbled value still gets hashed into a student_id
(api/routes/whatsapp.py computes student_id before validating "From"), so a
second, phoneless record accumulates whatever interactions happened while
Twilio was sending the bad value, while the person's real number keeps its
own separate, phoned record.

What this script does, inside a single DB transaction:
  1. Loads both Student rows (--keep and --retire).
  2. Re-points every Interaction, Session, and TestAttempt row from
     --retire's student_id to --keep's student_id (no FK/uniqueness
     conflicts on these tables — interaction_id/session_id/attempt_id are
     independently unique).
  3. Merges PerformanceVector rows. This table has a UNIQUE(student_id,
     subject, topic, difficulty) constraint, so where both records have a
     vector for the same (subject, topic, difficulty), the two are summed
     into --keep's row and --retire's is deleted; otherwise --retire's row
     is simply re-pointed to --keep's student_id. This is what makes the
     merge lossless: 119 + 6 attempts in, 125 attempts out — nothing is
     dropped, nothing is double-counted.
  4. Merges scalar Student fields onto --keep (see _merge_student_fields):
     earliest registered_at, latest last_seen_at, summed session_count,
     phone_number/name/recommendation fields filled in from --retire only
     where --keep is missing them, consent_given OR'd.
  5. Deletes the --retire Student row.
  6. Prints a before/after summary (attempts, interactions, sessions,
     test_attempts) so you can confirm total counts are preserved.
  7. If the merged --keep record's total answered questions is now >= the
     recommendation feature's threshold (ai.recommendations.
     RECOMMENDATION_TRIGGER_EVERY, currently 10), force-regenerates its
     cached student- and teacher-framed recommendations and refreshes the
     cohort insight — see "Recommendation regeneration" below for why.

Recommendation regeneration (step 7): ai.recommendations.
maybe_trigger_student_recommendation() fires ONLY on the exact interaction
that pushes a student's live total answered-question count (summed fresh
from performance_vectors on every call — there is no stored "last
triggered at" checkpoint anywhere in the schema) across an exact multiple
of RECOMMENDATION_TRIGGER_EVERY. A merge changes that live total in one
step, out of band from the normal answer-by-answer flow, and there is no
guarantee the new combined total lands on a multiple of 10 (e.g. 119 + 6 =
125 does not). Left alone, the merged record would sit there — however far
past the threshold — showing whatever recommendation text was cached
before the merge (or NULL, which the dashboard API renders as the generic
"not enough data yet" placeholder) until enough NEW organic answers
happened to arrive to reach the next boundary. Step 7 closes that gap by
calling ai.recommendations.generate_student_recommendation() directly
(bypassing the modulo gate, which is correct here — the merge itself is
the event that invalidates the cache) whenever the post-merge total already
qualifies, so a merged student is never left stuck.

What it deliberately does NOT try to reconcile: --keep's Student.channel
stays whatever it already was — a student who used both WhatsApp and USSD
will have interactions/sessions on both channels post-merge (correctly
reflected in dashboard.channel_stats(), which counts from those tables),
but the single Student.channel field can only hold one value. This has no
effect on any interaction/session/performance data.

Usage:
    # Dry run (default) — prints the plan, touches nothing:
    python scripts/merge_duplicate_student.py --keep <student_id> --retire <student_id>

    # Actually perform the merge (asks for typed confirmation first):
    python scripts/merge_duplicate_student.py --keep <student_id> --retire <student_id> --execute
"""
import argparse
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from db.models import Student, SessionRow, Interaction, PerformanceVector, TestAttempt
from ai.recommendations import (
    generate_student_recommendation,
    refresh_cohort_insight,
    RECOMMENDATION_TRIGGER_EVERY,
)


def _counts(db, student_id: str) -> dict:
    perf = db.query(PerformanceVector).filter(PerformanceVector.student_id == student_id).all()
    return {
        "sessions": db.query(SessionRow).filter(SessionRow.student_id == student_id).count(),
        "interactions": db.query(Interaction).filter(Interaction.student_id == student_id).count(),
        "test_attempts": db.query(TestAttempt).filter(TestAttempt.student_id == student_id).count(),
        "performance_vectors": len(perf),
        "attempts": sum(pv.attempts for pv in perf),
        "correct": sum(pv.correct for pv in perf),
    }


def _print_counts(label: str, c: dict) -> None:
    print(f"  [{label}] sessions={c['sessions']} interactions={c['interactions']} "
          f"test_attempts={c['test_attempts']} "
          f"performance_vectors={c['performance_vectors']} "
          f"(attempts={c['attempts']}, correct={c['correct']})")


def _merge_student_fields(keep: Student, retire: Student) -> None:
    """Mutate `keep` in place with the union of both records' data."""
    if not keep.phone_number and retire.phone_number:
        keep.phone_number = retire.phone_number
    if not keep.name and retire.name:
        keep.name = retire.name
    keep.registered_at = min(keep.registered_at, retire.registered_at)
    keep.last_seen_at = max(keep.last_seen_at, retire.last_seen_at)
    keep.session_count = (keep.session_count or 0) + (retire.session_count or 0)
    keep.consent_given = keep.consent_given or retire.consent_given

    # Keep whichever recommendation is more recent.
    keep_gen = keep.recommendation_generated_at
    retire_gen = retire.recommendation_generated_at
    if retire_gen and (not keep_gen or retire_gen > keep_gen):
        keep.recommendation_text = retire.recommendation_text
        keep.teacher_recommendation_text = retire.teacher_recommendation_text
        keep.recommendation_generated_at = retire_gen


def merge(keep_id: str, retire_id: str, execute: bool) -> None:
    if keep_id == retire_id:
        print("ERROR: --keep and --retire are the same student_id.")
        sys.exit(1)

    db = SessionLocal()
    try:
        keep = db.get(Student, keep_id)
        retire = db.get(Student, retire_id)
        if keep is None:
            print(f"ERROR: --keep student_id not found: {keep_id}")
            sys.exit(1)
        if retire is None:
            print(f"ERROR: --retire student_id not found: {retire_id}")
            sys.exit(1)

        print("=== Merge plan ===")
        print(f"KEEP   : {keep.student_id}  name={keep.name!r}  phone={keep.phone_number!r}  "
              f"channel={keep.channel}")
        print(f"RETIRE : {retire.student_id}  name={retire.name!r}  phone={retire.phone_number!r}  "
              f"channel={retire.channel}")
        print()
        before_keep = _counts(db, keep_id)
        before_retire = _counts(db, retire_id)
        _print_counts("keep (before)", before_keep)
        _print_counts("retire (before)", before_retire)
        expected_attempts = before_keep["attempts"] + before_retire["attempts"]
        expected_interactions = before_keep["interactions"] + before_retire["interactions"]
        print(f"\nExpected after merge: attempts={expected_attempts}, "
              f"interactions={expected_interactions}\n")

        if not execute:
            print("Dry run only - no changes made. Re-run with --execute to apply.")
            return

        confirm = input(
            f"Type MERGE to permanently merge {retire_id[:12]}... into "
            f"{keep_id[:12]}... : "
        )
        if confirm != "MERGE":
            print("Confirmation text did not match. Aborting - no changes made.")
            return

        # Re-point simple FK tables (no uniqueness conflicts possible).
        db.query(Interaction).filter(Interaction.student_id == retire_id).update(
            {"student_id": keep_id}
        )
        db.query(SessionRow).filter(SessionRow.student_id == retire_id).update(
            {"student_id": keep_id}
        )
        db.query(TestAttempt).filter(TestAttempt.student_id == retire_id).update(
            {"student_id": keep_id}
        )

        # Merge PerformanceVector rows, respecting the
        # UNIQUE(student_id, subject, topic, difficulty) constraint.
        keep_vectors = {
            (pv.subject, pv.topic, pv.difficulty): pv
            for pv in db.query(PerformanceVector).filter(PerformanceVector.student_id == keep_id).all()
        }
        retire_vectors = db.query(PerformanceVector).filter(
            PerformanceVector.student_id == retire_id
        ).all()
        for pv in retire_vectors:
            key = (pv.subject, pv.topic, pv.difficulty)
            existing = keep_vectors.get(key)
            if existing:
                existing.attempts += pv.attempts
                existing.correct += pv.correct
                db.delete(pv)
            else:
                pv.student_id = keep_id

        _merge_student_fields(keep, retire)
        db.delete(retire)

        db.commit()

        after_keep = _counts(db, keep_id)
        print("\n=== Merge complete ===")
        _print_counts("keep (after)", after_keep)
        if after_keep["attempts"] != expected_attempts:
            print(
                f"\nWARNING: attempts after merge ({after_keep['attempts']}) != "
                f"expected ({expected_attempts}). Investigate before trusting this record."
            )
        if after_keep["interactions"] != expected_interactions:
            print(
                f"\nWARNING: interactions after merge ({after_keep['interactions']}) != "
                f"expected ({expected_interactions}). Investigate before trusting this record."
            )

        # Step 7: force a recommendation refresh if the merged total already
        # qualifies — see "Recommendation regeneration" in the module
        # docstring for why this can't just wait for the normal trigger.
        if after_keep["attempts"] >= RECOMMENDATION_TRIGGER_EVERY:
            print(
                f"\nMerged total ({after_keep['attempts']}) is at/above the "
                f"recommendation threshold ({RECOMMENDATION_TRIGGER_EVERY}) - "
                f"force-regenerating cached recommendations (real LLM calls)..."
            )
            try:
                result = generate_student_recommendation(db, keep, audience="student")
                print("  student recommendation:")
                for bullet in result["bullets"]:
                    print(f"    - {bullet}")
            except Exception as e:
                print(f"  WARNING: student recommendation generation failed: {e}")
            try:
                result = generate_student_recommendation(db, keep, audience="teacher")
                print("  teacher recommendation:")
                for bullet in result["bullets"]:
                    print(f"    - {bullet}")
            except Exception as e:
                print(f"  WARNING: teacher recommendation generation failed: {e}")
            try:
                result = refresh_cohort_insight(db)
                print("  cohort insight refreshed:")
                for bullet in result["bullets"]:
                    print(f"    - {bullet}")
            except Exception as e:
                print(f"  WARNING: cohort insight refresh failed: {e}")
        else:
            print(
                f"\nMerged total ({after_keep['attempts']}) is below the "
                f"recommendation threshold ({RECOMMENDATION_TRIGGER_EVERY}) - "
                f"nothing to regenerate yet."
            )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", required=True, help="student_id to keep (canonical record)")
    parser.add_argument("--retire", required=True, help="student_id to merge into --keep, then delete")
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually perform the merge (default is a dry run that only prints the plan)",
    )
    args = parser.parse_args()
    merge(args.keep, args.retire, args.execute)
