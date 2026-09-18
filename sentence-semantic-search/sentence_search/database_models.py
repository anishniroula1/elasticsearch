from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SentenceMatch(Base):
    __tablename__ = "sentence_matches"

    globalId: Mapped[str] = mapped_column(Text, primary_key=True)
    tspId: Mapped[str] = mapped_column(Text, nullable=False)
    applicationId: Mapped[str] = mapped_column(Text, nullable=False)
    sectionName: Mapped[str] = mapped_column(Text, nullable=False)
    analysisGroup: Mapped[str] = mapped_column(Text, nullable=False)
    matchingGlobalIds: Mapped[list] = mapped_column(
        ARRAY(Text),
        default=list,
        server_default=text("'{}'::text[]"),
        nullable=False,
    )

    # These fields let the separate worker resume after a restart. They live on
    # this same row so a third queue table is not needed.
    matchStatus: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    matchAttempts: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    matchThreshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    matchAvailableAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    matchLockedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matchLastError: Mapped[str | None] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint('"matchThreshold" BETWEEN 1 AND 100'),
        CheckConstraint(
            '"matchStatus" IN '
            "('pending', 'running', 'completed', 'failed', 'skipped')"
        ),
        Index(
            "sentence_matches_application_idx",
            "applicationId",
            "analysisGroup",
            "globalId",
        ),
        Index("sentence_matches_tsp_idx", "applicationId", "tspId", "globalId"),
        Index(
            "sentence_matches_job_idx",
            "matchStatus",
            "matchAvailableAt",
            "globalId",
        ),
    )


class ApplicationMatchSummary(Base):
    __tablename__ = "application_match_summary"

    applicationId: Mapped[str] = mapped_column(Text, primary_key=True)
    analysisGroup: Mapped[str] = mapped_column(Text, primary_key=True)
    sectionMatchCounts: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    totalMatching: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (CheckConstraint('"totalMatching" >= 0'),)


def model_values(model) -> dict:
    """Return one SQLAlchemy model as a plain API dictionary."""

    return {
        column.name: getattr(model, column.name) for column in model.__table__.columns
    }
