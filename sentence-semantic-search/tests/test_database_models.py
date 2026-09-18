from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from sentence_search.database_models import (
    ApplicationSentenceSummary,
    Base,
    SentenceRecord,
)
from sentence_search.postgres_store import ANALYSIS_SCOPE, PostgresStore


def test_sqlalchemy_defines_all_matching_tables():
    assert set(Base.metadata.tables) == {
        "sentence_records",
        "sentence_relationships",
        "application_sentence_summary",
    }


def test_sentence_record_uses_camel_case_columns():
    columns = set(SentenceRecord.__table__.columns.keys())
    assert {
        "globalId",
        "applicationId",
        "sentenceKey",
        "tspId",
        "documentId",
        "sectionName",
        "sentIdLocal",
        "sentenceContent",
        "isTracer",
        "isFormLanguage",
        "sourceType",
        "analysisGroup",
        "createdAt",
        "updatedAt",
    }.issubset(columns)
    assert {"exactMatchCount", "matchStatus", "matchAttempts"}.issubset(columns)


def test_sentence_record_compiles_for_postgresql():
    statement = str(
        CreateTable(SentenceRecord.__table__).compile(dialect=postgresql.dialect())
    )

    assert '"sentIdLocal" BIGINT NOT NULL' in statement
    assert '"sentenceContent" TEXT NOT NULL' in statement
    assert "'skipped'" in statement


def test_summary_supports_analysis_scope():
    statement = str(
        CreateTable(ApplicationSentenceSummary.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "'analysis'" in statement


def test_application_summary_prepares_total_match_count():
    counts = {
        "totalDocuments": 2,
        "totalSentences": 10,
        "matchedSentences": 4,
        "exactMatchCount": 3,
        "semanticMatchCount": 7,
        "pendingCount": 0,
        "runningCount": 0,
        "completedCount": 10,
        "failedCount": 0,
    }

    summary = PostgresStore._summary_values(
        "A1",
        "application",
        "",
        "",
        counts,
    )

    assert summary["totalMatchCount"] == 10


def test_matchable_sentence_is_pending():
    values = PostgresStore._record_values(_sentence_record(), 90)

    assert values["matchStatus"] == "pending"


def test_tracer_or_form_sentence_is_skipped():
    tracer = _sentence_record()
    tracer["isTracer"] = True
    form_language = _sentence_record()
    form_language["isFormLanguage"] = True

    assert PostgresStore._record_values(tracer, 90)["matchStatus"] == "skipped"
    assert PostgresStore._record_values(form_language, 90)["matchStatus"] == "skipped"


def test_summary_keys_include_analysis_group():
    keys = PostgresStore._summary_keys(_sentence_record())

    assert (ANALYSIS_SCOPE, "", "Asylee") in keys


def _sentence_record() -> dict:
    return {
        "globalId": "S1",
        "applicationId": "A1",
        "tspId": "T1",
        "documentId": "D1",
        "sectionName": "Statement",
        "analysisGroup": "Asylee",
        "sentIdLocal": 1,
        "sentenceContent": "Example sentence",
        "sentenceKey": "a" * 64,
        "isTracer": False,
        "isFormLanguage": False,
        "sourceType": "document",
        "createdAt": "2026-09-17T00:00:00Z",
        "updatedAt": "2026-09-17T00:00:00Z",
    }
