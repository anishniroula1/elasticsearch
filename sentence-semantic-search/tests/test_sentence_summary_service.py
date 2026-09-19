from sentence_search.sentence_summary_service import SentenceSummaryService


class FakeOpenSearchStore:
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
                    "globalId": 1,
                    "sentenceKey": "key-a",
                    "sentenceContent": "First sentence",
                },
                {
                    "globalId": 2,
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
        assert excluded_application_id == "A1"
        assert analysis_group == "Asylee"
        all_counts = {"key-a": 2, "key-b": 1, "key-c": 4, "key-d": 5}
        return {key: all_counts[key] for key in sentence_keys}

    def catalog_vectors(self, sentence_keys):
        return {key: [1.0, 0.0] for key in sentence_keys}

    def catalog_matches(self, source_key, vector, threshold):
        if source_key == "key-a":
            matches = [
                {"sentenceKey": "key-a"},
                {"sentenceKey": "key-c"},
            ]
            if threshold <= 83:
                matches.append({"sentenceKey": "key-d"})
            return matches
        return [{"sentenceKey": "key-b"}]

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


def test_summary_calculates_complete_counts_live_at_default_threshold():
    service = SentenceSummaryService(FakeOpenSearchStore())

    result = service.application_summary("A1", "Asylee", 90, 100, None)

    assert result["totalSentences"] == 2
    assert result["totalMatching"] == 13
    assert result["sectionMatches"] == [
        {"sectionName": "Affidavit", "matchingCount": 12},
        {"sectionName": "B1", "matchingCount": 1},
    ]
    assert result["pageTotalMatches"] == 7
    assert result["sentences"][0]["exactMatchCount"] == 2
    assert result["sentences"][0]["similarMatchCount"] == 4
    assert result["neuralSearchUsed"] is False


def test_lower_threshold_calculates_more_matches_live():
    service = SentenceSummaryService(FakeOpenSearchStore())

    result = service.application_summary("A1", "Asylee", 70, 100, None)

    assert result["thresholdPercentage"] == 70
    assert result["totalMatching"] == 23
    assert result["sectionMatches"] == [
        {"sectionName": "Affidavit", "matchingCount": 22},
        {"sectionName": "B1", "matchingCount": 1},
    ]
    assert result["pageTotalMatches"] == 12
    assert result["sentences"][0]["similarMatchCount"] == 9
