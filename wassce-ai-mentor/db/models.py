from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship, mapped_column, Mapped
from db.database import Base


class Student(Base):
    __tablename__ = "students"

    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    session_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consent_given: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="student")


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), ForeignKey("students.student_id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    fsm_state: Mapped[str] = mapped_column(String(32), nullable=False, default="GREETING")
    current_subject: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="easy")
    question_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    student: Mapped["Student"] = relationship("Student", back_populates="sessions")


class Interaction(Base):
    __tablename__ = "interactions"

    interaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.session_id"), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), ForeignKey("students.student_id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    fsm_state: Mapped[str] = mapped_column(String(32), nullable=False)
    question_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    student_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluation_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    llm_response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class PerformanceVector(Base):
    __tablename__ = "performance_vectors"
    __table_args__ = (
        UniqueConstraint("student_id", "subject", "topic", "difficulty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[str] = mapped_column(String(64), ForeignKey("students.student_id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    @property
    def accuracy(self) -> float:
        return self.correct / max(self.attempts, 1)


class TestAttempt(Base):
    __tablename__ = "test_attempts"

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), ForeignKey("students.student_id"), nullable=False)
    test_type: Mapped[str] = mapped_column(String(8), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    responses: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_scores: Mapped[str | None] = mapped_column(Text, nullable=True)


# Alias to avoid clash with sqlalchemy.orm.Session in importing modules.
SessionRow = Session
