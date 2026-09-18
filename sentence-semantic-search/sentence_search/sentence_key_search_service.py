import hashlib
import re
from math import ceil

from sentence_search.config import Config
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.search_utils import decode_page_token, encode_page_token

SENTENCE_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SentenceKeySearchService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the settings and stores used by sentence-key search."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres

    def search(
        self,
        application_id: str,
        sentence_key: str,
        analysis_group: str,
        page_size: int,
        next_token: str | None,
    ) -> dict:
        """Find exact and saved similar sentences using only normal key filters.

        Input: one SHA-256 sentence key and application A1.
        Output: matching sentence records outside A1, 100 at a time.
        """

        sentence_key = sentence_key.strip().lower()
        if not SENTENCE_KEY_PATTERN.fullmatch(sentence_key):
            raise ValueError("sentenceKey must be a 64-character SHA-256 value")

        key_row = self.postgres.matching_keys([sentence_key])[sentence_key]
        similar_keys = [
            key for key in key_row["matchingSentenceKeys"] if key != sentence_key
        ]
        key_fingerprint = self._key_fingerprint(similar_keys)
        search_keys = [sentence_key, *similar_keys]

        if next_token:
            state = decode_page_token(next_token)
            self._validate_token(
                state,
                application_id,
                analysis_group,
                sentence_key,
                page_size,
                key_fingerprint,
            )
            after_global_id = state["afterGlobalId"]
            exact_count = int(state["exactMatchCount"])
            similar_count = int(state["similarMatchCount"])
            returned_before = int(state["returnedMatches"])
        else:
            after_global_id = None
            returned_before = 0
            counts = self.opensearch.occurrence_counts_by_key(
                search_keys,
                application_id,
                analysis_group,
            )
            exact_count = counts.get(sentence_key, 0)
            similar_count = sum(counts.get(key, 0) for key in similar_keys)

        result = self.opensearch.matching_occurrence_page(
            search_keys,
            application_id,
            analysis_group,
            page_size,
            after_global_id,
            False,
        )
        matches = []
        for occurrence in result["matches"]:
            match_type = (
                "exact" if occurrence["sentenceKey"] == sentence_key else "semantic"
            )
            matches.append({**occurrence, "matchType": match_type})

        returned_after = returned_before + len(matches)
        total_matches = exact_count + similar_count
        new_token = None
        next_after = result["nextAfterGlobalId"]
        if next_after:
            new_token = encode_page_token(
                {
                    "type": "sentenceKeySearch",
                    "applicationId": application_id,
                    "analysisGroup": analysis_group,
                    "sentenceKey": sentence_key,
                    "pageSize": page_size,
                    "afterGlobalId": next_after,
                    "exactMatchCount": exact_count,
                    "similarMatchCount": similar_count,
                    "returnedMatches": returned_after,
                    "keyFingerprint": key_fingerprint,
                }
            )

        total_pages = ceil(total_matches / page_size) if total_matches else 0
        return {
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "sentenceKey": sentence_key,
            "thresholdPercentage": self.config.match_threshold,
            "directMatchingKeyCount": len(similar_keys),
            "exactMatchCount": exact_count,
            "similarMatchCount": similar_count,
            "totalMatches": total_matches,
            "returnedMatches": len(matches),
            "pagination": {
                "page": (returned_before // page_size) + 1,
                "pageSize": page_size,
                "totalPages": total_pages,
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
        analysis_group: str,
        sentence_key: str,
        page_size: int,
        key_fingerprint: str,
    ):
        """Stop a token from being reused for a different search request."""

        expected = {
            "type": "sentenceKeySearch",
            "applicationId": application_id,
            "analysisGroup": analysis_group,
            "sentenceKey": sentence_key,
            "pageSize": page_size,
            "keyFingerprint": key_fingerprint,
        }
        for name, value in expected.items():
            if state.get(name) != value:
                raise ValueError("nextToken does not belong to this request")
        required = {"exactMatchCount", "similarMatchCount", "returnedMatches"}
        if not required.issubset(state):
            raise ValueError("Invalid nextToken")

    @staticmethod
    def _key_fingerprint(similar_keys: list) -> str:
        """Make a small value that detects key-list changes between pages."""

        value = "\n".join(sorted(similar_keys))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
