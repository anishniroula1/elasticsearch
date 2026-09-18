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
        """Find and save every threshold match for one sentence."""

        source = self.postgres.sentence(job["globalId"])
        if not source:
            raise RuntimeError(
                f"Sentence registry row does not exist: {job['globalId']}"
            )
        if not is_matchable_sentence(source):
            return {
                "globalId": source["globalId"],
                "threshold": int(job["matchThreshold"]),
                "candidatesFound": 0,
                "relationshipsInserted": 0,
                "status": "skipped",
            }

        vector = self.opensearch.catalog_vector(source["sentenceKey"])
        catalog_matches = self.opensearch.catalog_matches(
            source["sentenceKey"],
            vector,
            int(job["matchThreshold"]),
        )
        result = self.postgres.save_matches(
            source,
            catalog_matches,
            self.config.semantic_model_id,
        )
        return {
            "globalId": source["globalId"],
            "threshold": int(job["matchThreshold"]),
            **result,
        }
