from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore


class SentenceMatchService:
    def __init__(
        self,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the stores used to calculate sentence matches."""

        self.opensearch = opensearch
        self.postgres = postgres

    def match_sentence_key(self, sentence_key: str, threshold: int) -> dict:
        """Find vector hits and save both sides of each relationship.

        Input: one saved sentence key and threshold 90.
        Output: direct matches added to that key and every matching key.
        """

        vector = self.opensearch.catalog_vector(sentence_key)
        catalog_matches = self.opensearch.catalog_matches(
            sentence_key,
            vector,
            threshold,
        )
        target_keys = [
            match["sentenceKey"]
            for match in catalog_matches
            if match["sentenceKey"] != sentence_key
        ]
        result = self.postgres.save_key_matches(
            sentence_key,
            target_keys,
        )
        return {
            "sentenceKey": sentence_key,
            "threshold": threshold,
            "catalogMatchesFound": len(target_keys),
            **result,
        }
