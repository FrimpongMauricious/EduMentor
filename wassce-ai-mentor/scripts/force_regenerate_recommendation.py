"""
scripts/force_regenerate_recommendation.py — Force-regenerate one student's
cached AI recommendation (student- and teacher-framed) and refresh the
cohort insight, bypassing the every-10-answers modulo gate in
ai.recommendations.maybe_trigger_student_recommendation().

Why this exists: that gate fires ONLY on the exact interaction that pushes
a student's live total answered-question count (summed fresh from
performance_vectors on every call — there is no stored "last triggered at"
checkpoint anywhere in the schema; see ai/recommendations.py and
db/models.py) across an exact multiple of RECOMMENDATION_TRIGGER_EVERY. Any
out-of-band change to a student's total that doesn't go through the normal
answer-grading flow — most notably scripts/merge_duplicate_student.py,
which now calls this same regeneration automatically for NEW merges (see
its docstring) — can leave an ALREADY-merged student's total sitting well
past the threshold with a stale or NULL cached recommendation (rendered by
the dashboard API as the generic "not enough data yet" placeholder,
indistinguishable from a real low-data student) with no organic trigger
ever firing again until enough brand-new answers arrive to cross the next
boundary. This script is the manual unstick for a student already in that
state.

This makes REAL LLM calls (ai.recommendations.generate_student_recommendation
is not mocked here) and commits the results, exactly as the normal trigger
would — it just isn't gated on the modulo check first.

Usage:
    python scripts/force_regenerate_recommendation.py --student <student_id>
"""
import argparse
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from db.models import Student
from adaptive.engine import get_student_profile
from ai.recommendations import (
    generate_student_recommendation,
    refresh_cohort_insight,
    RECOMMENDATION_TRIGGER_EVERY,
    NOT_ENOUGH_DATA_STUDENT,
    NOT_ENOUGH_DATA_TEACHER,
    _parse_stored_bullets,
)


def force_regenerate(student_id: str) -> None:
    db = SessionLocal()
    try:
        student = db.get(Student, student_id)
        if student is None:
            print(f"ERROR: no student found with student_id={student_id}")
            sys.exit(1)

        profile = get_student_profile(db, student_id)
        total = sum(s["attempts"] for s in profile["subjects"].values())

        before_student, _ = _parse_stored_bullets(student.recommendation_text, NOT_ENOUGH_DATA_STUDENT)
        before_teacher, _ = _parse_stored_bullets(student.teacher_recommendation_text, NOT_ENOUGH_DATA_TEACHER)
        print(f"student_id     : {student_id}")
        print(f"name           : {student.name!r}")
        print(f"total_attempts : {total}")
        print(f"BEFORE recommendation_text (parsed)         : {before_student}")
        print(f"BEFORE teacher_recommendation_text (parsed) : {before_teacher}")
        print(f"BEFORE recommendation_generated_at          : {student.recommendation_generated_at}")

        if total < RECOMMENDATION_TRIGGER_EVERY:
            print(
                f"\nThis student has {total} answered questions, below the "
                f"{RECOMMENDATION_TRIGGER_EVERY}-answer minimum — a real "
                f"recommendation cannot be generated yet. This is the "
                f"legitimate 'not enough data' case, not a bug; nothing to do."
            )
            return

        print("\nGenerating student-framed recommendation (real LLM call)...")
        student_result = generate_student_recommendation(db, student, audience="student")
        for bullet in student_result["bullets"]:
            print(f"  - {bullet}")

        print("Generating teacher-framed recommendation (real LLM call)...")
        teacher_result = generate_student_recommendation(db, student, audience="teacher")
        for bullet in teacher_result["bullets"]:
            print(f"  - {bullet}")

        print("Refreshing cohort insight (real LLM call)...")
        cohort_result = refresh_cohort_insight(db)
        for bullet in cohort_result["bullets"]:
            print(f"  - {bullet}")

        print("\nDone. Verify via GET /api/dashboard/student/<phone> or the teacher overview.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--student", required=True, help="student_id to regenerate for")
    args = parser.parse_args()
    force_regenerate(args.student)
