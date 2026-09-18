from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from sentence_search.database_models import (
    ApplicationMatchSummary,
    Base,
    SentenceMatch,
)
from sentence_search.postgres_store import PostgresStore


def test_sqlalchemy_defines_only_two_matching_tables():
    assert set(Base.metadata.tables) == {
        "sentence_matches",
        "application_match_summary",
    }


def test_sentence_match_has_metadata_and_one_matching_list():
    columns = set(SentenceMatch.__table__.columns.keys())

    assert {
        "globalId",
        "tspId",
        "applicationId",
        "sectionName",
        "analysisGroup",
        "matchingGlobalIds",
    }.issubset(columns)
    assert "sentenceKey" not in columns
    assert "sentenceContent" not in columns


def test_sentence_match_uses_postgresql_text_array():
    statement = str(
        CreateTable(SentenceMatch.__table__).compile(dialect=postgresql.dialect())
    )

    assert '"matchingGlobalIds" TEXT[]' in statement
    assert "'skipped'" in statement


def test_application_summary_uses_one_json_section_map():
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


def test_regular_sentence_is_pending():
    values = PostgresStore._match_values(_sentence(), 90)

    assert values["matchStatus"] == "pending"
    assert values["matchingGlobalIds"] == []


def test_tracer_or_form_sentence_is_skipped():
    tracer = _sentence()
    tracer["isTracer"] = True
    form_language = _sentence()
    form_language["isFormLanguage"] = True

    assert PostgresStore._match_values(tracer, 90)["matchStatus"] == "skipped"
    assert PostgresStore._match_values(form_language, 90)["matchStatus"] == "skipped"


def _sentence() -> dict:
    return {
        "globalId": "S1",
        "applicationId": "A1",
        "tspId": "T1",
        "sectionName": "Affidavit",
        "analysisGroup": "Asylee",
        "isTracer": False,
        "isFormLanguage": False,
        "createdAt": "2026-09-17T00:00:00Z",
        "updatedAt": "2026-09-17T00:00:00Z",
    }
