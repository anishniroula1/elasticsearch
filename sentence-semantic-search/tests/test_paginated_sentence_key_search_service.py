from sentence_search.paginated_sentence_key_search_service import (
    PaginatedSentenceKeySearchService,
)
from sentence_search.sentence_key_search_service import SentenceKeySearchService

SOURCE_KEY = "a" * 64
SIMILAR_KEY = "b" * 64


class FakeOpenSearchStore:
    def __init__(self):
        self.page_requests = []

    def catalog_vector(self, sentence_key):
        assert sentence_key == SOURCE_KEY
        return [1.0, 0.0]

    def catalog_matches(self, sentence_key, vector, threshold):
        return [
            {"sentenceKey": SOURCE_KEY, "similarityPercentage": 100.0},
            {"sentenceKey": SIMILAR_KEY, "similarityPercentage": 92.0},
        ]

    def matching_occurrence_page(
        self,
        sentence_keys,
        excluded_application_id,
        analysis_group,
        page_size,
        after_global_id,
        include_total,
    ):
        self.page_requests.append(
            {
                "keys": sentence_keys,
                "pageSize": page_size,
                "afterGlobalId": after_global_id,
                "includeTotal": include_total,
            }
        )
        if after_global_id is None:
            return {
                "matches": [
                    {"globalId": 10, "sentenceKey": SOURCE_KEY},
                    {"globalId": 11, "sentenceKey": SIMILAR_KEY},
                ],
                "nextAfterGlobalId": 11,
                "totalMatches": None,
            }
        return {
            "matches": [
                {"globalId": 12, "sentenceKey": SIMILAR_KEY},
            ],
            "nextAfterGlobalId": None,
            "totalMatches": None,
        }


def test_paginated_sentence_search_counts_only_the_current_page():
    opensearch = FakeOpenSearchStore()
    shared_search = SentenceKeySearchService(opensearch)
    service = PaginatedSentenceKeySearchService(opensearch, shared_search)

    first = service.search("A1", SOURCE_KEY, "Asylee", 90, 2, None)
    second = service.search(
        "A1",
        SOURCE_KEY,
        "Asylee",
        90,
        2,
        first["nextToken"],
    )

    assert first["matchingCountScope"] == "currentPage"
    assert first["totalMatches"] == 2
    assert first["exactMatchCount"] == 1
    assert first["similarMatchCount"] == 1
    assert first["pagination"]["hasNextPage"] is True
    assert second["totalMatches"] == 1
    assert second["exactMatchCount"] == 0
    assert second["similarMatchCount"] == 1
    assert second["pagination"]["page"] == 2
    assert second["pagination"]["hasNextPage"] is False
    assert all(not request["includeTotal"] for request in opensearch.page_requests)
