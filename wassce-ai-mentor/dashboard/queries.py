"""
dashboard/queries.py — Read-only analytics queries for the teacher dashboard.

All functions accept a SQLAlchemy engine and return pandas DataFrames or dicts.
Keeping queries separate from the Streamlit UI makes them independently testable.
"""
from datetime import datetime, timedelta
import pandas as pd
from sqlalchemy import text

SUBJECT_DISPLAY = {
    "maths": "Maths",
    "english": "English",
    "science": "Science",
    "social_studies": "Social Studies",
}


def _exec(engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Execute a SQL string and return a DataFrame."""
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        rows = result.fetchall()
        cols = list(result.keys())
    return pd.DataFrame(rows, columns=cols)


def cohort_overview(engine) -> dict:
    """Return top-level cohort metrics."""
    n_students = _exec(engine, "SELECT COUNT(*) AS n FROM students")["n"].iloc[0]
    n_sessions = _exec(engine, "SELECT COUNT(*) AS n FROM sessions")["n"].iloc[0]

    qa = _exec(engine, """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN evaluation_result = 'correct' THEN 1 ELSE 0 END) AS correct
        FROM interactions
        WHERE evaluation_result IS NOT NULL
          AND evaluation_result NOT IN ('skip', 'no_question')
    """)
    total_q = int(qa["total"].iloc[0] or 0)
    correct_q = int(qa["correct"].iloc[0] or 0)
    accuracy = round(correct_q / total_q * 100, 1) if total_q else 0.0

    cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    n_active = _exec(
        engine,
        "SELECT COUNT(*) AS n FROM students WHERE last_seen_at >= :cutoff",
        {"cutoff": cutoff},
    )["n"].iloc[0]

    return {
        "students": int(n_students),
        "sessions": int(n_sessions),
        "questions_answered": total_q,
        "accuracy_pct": float(accuracy),
        "active_7d": int(n_active),
    }


def sessions_per_day(engine) -> pd.DataFrame:
    """Daily session count for the activity chart."""
    return _exec(engine, """
        SELECT date(started_at) AS day, COUNT(*) AS sessions
        FROM sessions
        GROUP BY day
        ORDER BY day
    """)


def subject_topic_accuracy(engine) -> pd.DataFrame:
    """
    Accuracy (%) per subject+topic aggregated across all students.
    Columns: subject, topic, attempts, correct, accuracy_pct, subject_display
    """
    df = _exec(engine, """
        SELECT subject, topic,
               SUM(attempts)  AS attempts,
               SUM(correct)   AS correct
        FROM performance_vectors
        GROUP BY subject, topic
        HAVING SUM(attempts) > 0
        ORDER BY subject, topic
    """)
    if df.empty:
        return pd.DataFrame(columns=["subject", "topic", "attempts", "correct", "accuracy_pct", "subject_display"])
    df["accuracy_pct"] = (df["correct"] / df["attempts"] * 100).round(1)
    df["subject_display"] = df["subject"].map(SUBJECT_DISPLAY).fillna(df["subject"])
    return df


def channel_stats(engine) -> pd.DataFrame:
    """
    Per-channel counts of students, sessions, and interactions.
    Columns: channel, students, sessions, interactions
    """
    return _exec(engine, """
        SELECT
            s.channel,
            COUNT(DISTINCT s.student_id)      AS students,
            COUNT(DISTINCT sess.session_id)   AS sessions,
            COUNT(i.interaction_id)           AS interactions
        FROM students s
        LEFT JOIN sessions sess ON sess.student_id = s.student_id
        LEFT JOIN interactions i ON i.student_id = s.student_id
        GROUP BY s.channel
    """)


def weak_topics(engine, min_attempts: int = 1) -> pd.DataFrame:
    """
    Topics ranked by accuracy (lowest first).
    Only includes topic groups with at least min_attempts total.
    Columns: subject, topic, attempts, correct, accuracy_pct, subject_display
    """
    df = _exec(engine, """
        SELECT subject, topic,
               SUM(attempts) AS attempts,
               SUM(correct)  AS correct
        FROM performance_vectors
        GROUP BY subject, topic
        HAVING SUM(attempts) >= :min_attempts
        ORDER BY (SUM(correct) * 1.0 / SUM(attempts)) ASC
    """, {"min_attempts": min_attempts})
    if df.empty:
        return pd.DataFrame(columns=["subject", "topic", "attempts", "correct", "accuracy_pct", "subject_display"])
    df["accuracy_pct"] = (df["correct"] / df["attempts"] * 100).round(1)
    df["subject_display"] = df["subject"].map(SUBJECT_DISPLAY).fillna(df["subject"])
    return df


def prepost_results(engine) -> pd.DataFrame:
    """
    Pre/post test scores per student (first pre and first post each).
    Columns: student_id, pre_score, post_score, improvement
    """
    df = _exec(engine, """
        SELECT student_id, test_type, total_score
        FROM test_attempts
        WHERE completed_at IS NOT NULL
        ORDER BY student_id, started_at ASC
    """)
    if df.empty:
        return pd.DataFrame(columns=["student_id", "pre_score", "post_score", "improvement"])

    pre = (
        df[df["test_type"] == "pre"]
        .groupby("student_id")["total_score"]
        .first()
        .rename("pre_score")
    )
    post = (
        df[df["test_type"] == "post"]
        .groupby("student_id")["total_score"]
        .first()
        .rename("post_score")
    )
    result = pd.concat([pre, post], axis=1).reset_index()
    result = result[result["pre_score"].notna()].copy()
    result["improvement"] = result["post_score"] - result["pre_score"]
    return result


def student_list(engine) -> pd.DataFrame:
    return _exec(engine, """
        SELECT student_id, channel, registered_at, last_seen_at, session_count
        FROM students
        ORDER BY registered_at DESC
    """)


def interaction_export(engine) -> pd.DataFrame:
    return _exec(engine, """
        SELECT interaction_id, student_id, channel, timestamp,
               fsm_state, question_id, student_response,
               evaluation_result, llm_response_ms, retrieval_score
        FROM interactions
        ORDER BY timestamp DESC
    """)


def performance_export(engine) -> pd.DataFrame:
    return _exec(engine, """
        SELECT student_id, subject, topic, difficulty, attempts, correct
        FROM performance_vectors
        ORDER BY student_id, subject, topic
    """)


def test_attempts_export(engine) -> pd.DataFrame:
    return _exec(engine, """
        SELECT attempt_id, student_id, test_type, started_at, completed_at,
               total_score, subject_scores
        FROM test_attempts
        ORDER BY started_at DESC
    """)
