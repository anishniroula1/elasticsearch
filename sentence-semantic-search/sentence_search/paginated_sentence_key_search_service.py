from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.search_utils import decode_page_token, encode_page_token
from sentence_search.sentence_key_search_service import SentenceKeySearchService


class PaginatedSentenceKeySearchService:
    def __init__(
        self,
        opensearch: OpenSearchStore,
        sentence_search: SentenceKeySearchService,
    ):
        """Save the store and shared sentence-key search service."""

        self.opensearch = opensearch
        self.sentence_search = sentence_search

    def search(
        self,
        application_id: str,
        sentence_key: str,
        analysis_group: str,
        threshold: int,
        page_size: int,
        next_token: str | None,
    ) -> dict:
        """Return one match page and counts for only that page.

        Input: one saved sentence key, threshold 90, and page size 50.
        Output: up to 50 matches and a token for the next page.
        """

        sentence_key, search_keys, score_by_key = (
            self.sentence_search.matching_keys(sentence_key, threshold)
        )
        if next_token:
            state = decode_page_token(next_token)
            self._validate_token(
                state,
                application_id,
                sentence_key,
                analysis_group,
                threshold,
                page_size,
            )
            after_global_id = state["afterGlobalId"]
            returned_before = int(state["returnedMatches"])
        else:
            after_global_id = None
            returned_before = 0

        result = self.opensearch.matching_occurrence_page(
            search_keys,
            application_id,
            analysis_group,
            page_size,
            after_global_id,
            False,
        )
        matches = self.sentence_search.add_match_details(
            result["matches"],
            sentence_key,
            score_by_key,
        )
        exact_count = sum(match["matchType"] == "exact" for match in matches)
        similar_count = len(matches) - exact_count

        returned_after = returned_before + len(matches)
        new_token = None
        next_after = result["nextAfterGlobalId"]
        if next_after:
            new_token = encode_page_token(
                {
                    "type": "sentenceKeySemanticSearch",
                    "applicationId": application_id,
                    "sentenceKey": sentence_key,
                    "analysisGroup": analysis_group,
                    "threshold": threshold,
                    "pageSize": page_size,
                    "afterGlobalId": next_after,
                    "returnedMatches": returned_after,
                }
            )

        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "sentenceKey": sentence_key,
            "thresholdPercentage": threshold,
            "matchingCountScope": "currentPage",
            "directMatchingKeyCount": len(search_keys) - 1,
            "exactMatchCount": exact_count,
            "similarMatchCount": similar_count,
            "totalMatches": len(matches),
            "returnedMatches": len(matches),
            "pagination": {
                "page": (returned_before // page_size) + 1,
                "pageSize": page_size,
                "hasPreviousPage": returned_before > 0,
                "hasNextPage": new_token is not None,
            },
            "nextToken": new_token,
            "matches": matches,
            "neuralSearchUsed": False,
        }

    @staticmethod
    def _validate_token(
        state: dict,
        application_id: str,
        sentence_key: str,
        analysis_group: str,
        threshold: int,
        page_size: int,
    ):
        """Stop a token from being reused for a different search request."""

        expected = {
            "type": "sentenceKeySemanticSearch",
            "applicationId": application_id,
            "sentenceKey": sentence_key,
            "analysisGroup": analysis_group,
            "threshold": threshold,
            "pageSize": page_size,
        }
        for name, value in expected.items():
            if state.get(name) != value:
                raise ValueError("nextToken does not belong to this request")
        if "returnedMatches" not in state:
            raise ValueError("Invalid nextToken")
