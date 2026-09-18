from sentence_search.config import Config
from sentence_search.matching_rules import is_matchable_sentence
from sentence_search.models import SentenceOccurrence
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore


class SentenceService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the stores used when a sentence is added."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres

    def add_sentence(
        self,
        sentence: SentenceOccurrence,
    ) -> dict:
        """Index one sentence and queue its semantic matching job."""

        source = sentence.model_dump(mode="json")
        # Validate first so a bad global ID cannot leave OpenSearch half updated.
        self.postgres.validate_sentence_identity([source])
        key = source["sentenceKey"]
        existing = self.opensearch.existing_catalog_keys([key])
        catalog_indexed = 0
        matchable = is_matchable_sentence(source)
        if key not in existing:
            self.opensearch.bulk_index_catalog([self._catalog_source(source)])
            catalog_indexed = 1

        self.opensearch.bulk_index_occurrences([source])
        # The worker must see the vector before its PostgreSQL job is visible.
        self.opensearch.refresh_indices()
        registration = self.postgres.register_sentences(
            [source],
            self.config.match_threshold,
        )
        return {
            "globalId": source["globalId"],
            "sentenceKey": key,
            "catalogDocumentIndexed": bool(catalog_indexed),
            "matchJobQueued": bool(registration["jobsQueued"]),
            "threshold": self.config.match_threshold,
            "status": self._status(matchable, registration["jobsQueued"]),
        }

    @staticmethod
    def _status(matchable: bool, jobs_queued: int) -> str:
        """Explain whether matching was queued, skipped, or already done."""

        if not matchable:
            return "skipped"
        if jobs_queued:
            return "queued"
        return "existing"

    @staticmethod
    def _catalog_source(source: dict) -> dict:
        """Keep only fields allowed by the strict catalog mapping."""

        return {
            "sentenceContent": source["sentenceContent"],
            "sentenceKey": source["sentenceKey"],
            "createdAt": source["createdAt"],
            "updatedAt": source["updatedAt"],
        }
