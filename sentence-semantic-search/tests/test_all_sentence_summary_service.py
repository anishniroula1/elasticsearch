from sentence_search.all_sentence_summary_service import AllSentenceSummaryService


class FakePageSummary:
    def __init__(self):
        self.tokens = []

    def application_summary(
        self,
        application_id,
        analysis_group,
        threshold,
        page_size,
        next_token,
    ):
        assert application_id == "A1"
        assert analysis_group == "Asylee"
        assert threshold == 90
        assert page_size == 100
        self.tokens.append(next_token)
        if next_token is None:
            return {
                "totalSentences": 2,
                "totalMatching": 6,
                "matchingSentences": 1,
                "sectionMatches": [
                    {"sectionName": "Affidavit", "matchingCount": 6}
                ],
                "sentences": [{"globalId": 1}],
                "nextToken": "second-page",
            }
        return {
            "totalSentences": 2,
            "totalMatching": 3,
            "matchingSentences": 1,
            "sectionMatches": [
                {"sectionName": "Affidavit", "matchingCount": 1},
                {"sectionName": "B1", "matchingCount": 2},
            ],
            "sentences": [{"globalId": 2}],
            "nextToken": None,
        }


def test_all_summary_combines_every_internal_page():
    page_summary = FakePageSummary()
    service = AllSentenceSummaryService(page_summary)

    result = service.application_summary("A1", "Asylee", 90)

    assert page_summary.tokens == [None, "second-page"]
    assert result["summaryScope"] == "allSentences"
    assert result["totalMatching"] == 9
    assert result["matchingSentences"] == 2
    assert result["sectionMatches"] == [
        {"sectionName": "Affidavit", "matchingCount": 7},
        {"sectionName": "B1", "matchingCount": 2},
    ]
    assert result["sentences"] == [{"globalId": 1}, {"globalId": 2}]
