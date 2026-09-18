from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from sentence_search.database_models import (
    ApplicationMatchSummary,
    Base,
    SentenceKeyMatch,
)


def test_sqlalchemy_defines_two_matching_tables():
    assert set(Base.metadata.tables) == {
        "sentence_key_matches",
        "application_match_summary",
    }


def test_key_row_has_only_direct_match_list_and_timestamps():
    columns = set(SentenceKeyMatch.__table__.columns.keys())

    assert columns == {
        "sentenceKey",
        "matchingSentenceKeys",
        "createdAt",
        "updatedAt",
    }
    assert "globalId" not in columns
    assert "applicationId" not in columns


def test_matching_sentence_keys_uses_postgresql_text_array():
    statement = str(
        CreateTable(SentenceKeyMatch.__table__).compile(dialect=postgresql.dialect())
    )

    assert '"matchingSentenceKeys" TEXT[]' in statement


def test_matching_key_array_has_a_gin_index_for_deletion():
    indexes = {index.name: index for index in SentenceKeyMatch.__table__.indexes}

    assert (
        indexes["sentence_key_matches_keys_gin_idx"].dialect_options["postgresql"][
            "using"
        ]
        == "gin"
    )


def test_application_summary_has_section_and_total_counts():
    columns = set(ApplicationMatchSummary.__table__.columns.keys())
    statement = str(
        CreateTable(ApplicationMatchSummary.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert columns == {
        "applicationId",
        "analysisGroup",
        "sectionMatchCounts",
        "totalMatching",
        "updatedAt",
    }
    assert '"sectionMatchCounts" JSONB' in statement
    assert '"totalMatching" BIGINT' in statement
