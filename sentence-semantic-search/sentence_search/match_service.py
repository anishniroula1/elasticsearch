from sentence_search.config import Config
from sentence_search.matching_rules import is_matchable_sentence
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore


class SentenceMatchService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the stores used by the background match worker."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres

    def process_job(self, job: dict) -> dict:
        """Find direct vector hits and merge their connected match groups."""

        source = self.opensearch.occurrence(job["globalId"])
        if not source:
            raise RuntimeError(
                f"OpenSearch occurrence does not exist: {job['globalId']}"
            )
        if not is_matchable_sentence(source):
            return {
                "globalId": source["globalId"],
                "threshold": int(job["matchThreshold"]),
                "catalogMatchesFound": 0,
                "directMatchesFound": 0,
                "matchGroupSize": 1,
                "matchListsUpdated": 0,
                "status": "skipped",
            }

        vector = self.opensearch.catalog_vector(source["sentenceKey"])
        catalog_matches = self.opensearch.catalog_matches(
            source["sentenceKey"],
            vector,
            int(job["matchThreshold"]),
        )
        occurrences = self.opensearch.matching_occurrences(
            source,
            catalog_matches,
        )
        result = self.postgres.save_match_group(
            source["globalId"],
            [occurrence["globalId"] for occurrence in occurrences],
        )
        return {
            "globalId": source["globalId"],
            "threshold": int(job["matchThreshold"]),
            "catalogMatchesFound": len(catalog_matches),
            **result,
        }
