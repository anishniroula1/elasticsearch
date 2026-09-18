from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SentenceRecord(Base):
    __tablename__ = "sentence_records"

    globalId: Mapped[str] = mapped_column(Text, primary_key=True)
    applicationId: Mapped[str] = mapped_column(Text, nullable=False)
    tspId: Mapped[str] = mapped_column(Text, nullable=False)
    documentId: Mapped[str] = mapped_column(Text, nullable=False)
    sectionName: Mapped[str] = mapped_column(Text, nullable=False)
    analysisGroup: Mapped[str] = mapped_column(Text, nullable=False)
    sentIdLocal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sentenceContent: Mapped[str] = mapped_column(Text, nullable=False)
    sentenceKey: Mapped[str] = mapped_column(String(64), nullable=False)
    isTracer: Mapped[bool] = mapped_column(Boolean, nullable=False)
    isFormLanguage: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sourceType: Mapped[str] = mapped_column(Text, nullable=False)
    exactMatchCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    semanticMatchCount: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    matchThreshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    matchStatus: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    matchAttempts: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    matchAvailableAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    matchLockedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matchLastError: Mapped[str | None] = mapped_column(Text)
    matchCompletedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint('"exactMatchCount" >= 0'),
        CheckConstraint('"semanticMatchCount" >= 0'),
        CheckConstraint('"matchThreshold" BETWEEN 1 AND 100'),
        CheckConstraint(
            '"matchStatus" IN '
            "('pending', 'running', 'completed', 'failed', 'skipped')"
        ),
        Index("sentence_records_application_idx", "applicationId", "globalId"),
        Index(
            "sentence_records_tsp_idx",
            "applicationId",
            "tspId",
            "globalId",
        ),
        Index("sentence_records_key_idx", "sentenceKey", "globalId"),
        Index(
            "sentence_records_group_idx",
            "applicationId",
            "sectionName",
            "analysisGroup",
        ),
        Index(
            "sentence_records_job_idx",
            "matchStatus",
            "matchAvailableAt",
            "globalId",
        ),
    )


class SentenceRelationship(Base):
    __tablename__ = "sentence_relationships"

    sentenceIdLow: Mapped[str] = mapped_column(
        Text,
        ForeignKey("sentence_records.globalId", ondelete="CASCADE"),
        primary_key=True,
    )
    sentenceIdHigh: Mapped[str] = mapped_column(
        Text,
        ForeignKey("sentence_records.globalId", ondelete="CASCADE"),
        primary_key=True,
    )
    similarityPercentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
    )
    matchType: Mapped[str] = mapped_column(Text, nullable=False)
    modelVersion: Mapped[str] = mapped_column(Text, primary_key=True)
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint('"sentenceIdLow" < "sentenceIdHigh"'),
        CheckConstraint(
            '"similarityPercentage" >= 0 AND "similarityPercentage" <= 100'
        ),
        CheckConstraint("\"matchType\" IN ('exact', 'semantic')"),
        Index(
            "sentence_relationships_low_page_idx",
            "sentenceIdLow",
            similarityPercentage.desc(),
            "sentenceIdHigh",
        ),
        Index(
            "sentence_relationships_high_page_idx",
            "sentenceIdHigh",
            similarityPercentage.desc(),
            "sentenceIdLow",
        ),
    )


class ApplicationSentenceSummary(Base):
    __tablename__ = "application_sentence_summary"

    applicationId: Mapped[str] = mapped_column(Text, primary_key=True)
    summaryScope: Mapped[str] = mapped_column(Text, primary_key=True)
    sectionName: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    analysisGroup: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    totalDocuments: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    totalSentences: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    matchedSentences: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    exactMatchCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    semanticMatchCount: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    totalMatchCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    pendingCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    runningCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completedCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    failedCount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("\"summaryScope\" IN ('application', 'analysis', 'section')"),
        Index(
            "application_sentence_summary_lookup_idx",
            "applicationId",
            "summaryScope",
        ),
    )


def model_values(model) -> dict:
    """Return one SQLAlchemy model as a plain API dictionary."""

    return {
        column.name: getattr(model, column.name) for column in model.__table__.columns
    }
