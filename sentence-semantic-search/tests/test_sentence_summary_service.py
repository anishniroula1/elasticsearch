from types import SimpleNamespace

from sentence_search.sentence_summary_service import SentenceSummaryService


class FakeOpenSearchStore:
    def __init__(self):
        self.affected_keys = None

    def application_occurrence_page(
        self,
        application_id,
        analysis_group,
        page_size,
        after_global_id,
        include_total,
    ):
        assert application_id == "A1"
        assert analysis_group == "Asylee"
        assert page_size == 100
        assert after_global_id is None
        assert include_total is True
        return {
            "sentences": [
                {
                    "globalId": "S1",
                    "sentenceKey": "key-a",
                    "sentenceContent": "First sentence",
                },
                {
                    "globalId": "S2",
                    "sentenceKey": "key-b",
                    "sentenceContent": "Second sentence",
                },
            ],
            "nextAfterGlobalId": None,
            "totalSentences": 2,
        }

    def occurrence_counts_by_key(
        self,
        sentence_keys,
        excluded_application_id,
        analysis_group,
    ):
        assert set(sentence_keys) == {"key-a", "key-b", "key-c"}
        assert excluded_application_id == "A1"
        assert analysis_group == "Asylee"
        return {"key-a": 2, "key-b": 1, "key-c": 4}

    def application_key_section_counts(self, application_id, analysis_group):
        return [
            {
                "sectionName": "Affidavit",
                "sentenceKey": "key-a",
                "occurrenceCount": 2,
            },
            {
                "sectionName": "B1",
                "sentenceKey": "key-b",
                "occurrenceCount": 1,
            },
        ]

    def application_groups_for_keys(self, sentence_keys):
        self.affected_keys = set(sentence_keys)
        return [{"applicationId": "A1", "analysisGroup": "Asylee"}]


class FakePostgresStore:
    def __init__(self):
        self.saved_summary = None

    def application_summary(self, application_id, analysis_group):
        return {
            "totalMatching": 40,
            "sectionMatches": [{"sectionName": "Affidavit", "matchingCount": 40}],
            "updatedAt": "2026-09-18T00:00:00Z",
        }

    def matching_keys(self, sentence_keys):
        all_rows = {
            "key-a": {
                "matchingSentenceKeys": ["key-c"],
            },
            "key-b": {
                "matchingSentenceKeys": [],
            },
        }
        return {key: all_rows[key] for key in sentence_keys}

    def save_application_summary(
        self,
        application_id,
        analysis_group,
        section_counts,
        total_matching,
    ):
        self.saved_summary = (
            application_id,
            analysis_group,
            section_counts,
            total_matching,
        )


def test_summary_returns_exact_and_similar_counts_per_sentence():
    service = SentenceSummaryService(
        SimpleNamespace(match_threshold=90),
        FakeOpenSearchStore(),
        FakePostgresStore(),
    )

    result = service.application_summary("A1", "Asylee", 100, None)

    assert result["totalSentences"] == 2
    assert result["totalMatching"] == 40
    assert result["pageMatchingSentences"] == 2
    assert result["pageTotalMatches"] == 7
    assert result["sentences"][0]["exactMatchCount"] == 2
    assert result["sentences"][0]["similarMatchCount"] == 4
    assert result["sentences"][0]["totalMatchCount"] == 6
    assert result["sentences"][1]["totalMatchCount"] == 1
    assert result["neuralSearchUsed"] is False


def test_summary_refresh_saves_all_section_totals():
    postgres = FakePostgresStore()
    service = SentenceSummaryService(
        SimpleNamespace(match_threshold=90),
        FakeOpenSearchStore(),
        postgres,
    )
    result = service.refresh_application_summary("A1", "Asylee")

    assert result["sectionMatchCounts"] == {"Affidavit": 12, "B1": 1}
    assert result["totalMatching"] == 13
    assert postgres.saved_summary == (
        "A1",
        "Asylee",
        {"Affidavit": 12, "B1": 1},
        13,
    )


def test_key_change_refreshes_every_affected_application():
    opensearch = FakeOpenSearchStore()
    postgres = FakePostgresStore()
    service = SentenceSummaryService(
        SimpleNamespace(match_threshold=90),
        opensearch,
        postgres,
    )

    result = service.refresh_affected_applications(["key-a"])

    assert opensearch.affected_keys == {"key-a", "key-c"}
    assert postgres.saved_summary == (
        "A1",
        "Asylee",
        {"Affidavit": 12, "B1": 1},
        13,
    )
    assert result["applicationSummariesRefreshed"] == 1
