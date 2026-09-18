from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SentenceKeyMatch(Base):
    __tablename__ = "sentence_key_matches"

    sentenceKey: Mapped[str] = mapped_column(Text, primary_key=True)
    matchingSentenceKeys: Mapped[list] = mapped_column(
        ARRAY(Text),
        default=list,
        server_default=text("'{}'::text[]"),
        nullable=False,
    )

    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Deletion asks which arrays contain a removed key. GIN avoids scanning
        # the complete table for that overlap lookup.
        Index(
            "sentence_key_matches_keys_gin_idx",
            "matchingSentenceKeys",
            postgresql_using="gin",
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
    totalMatching: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (CheckConstraint('"totalMatching" >= 0'),)
