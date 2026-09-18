from sentence_search.config import Config
from sentence_search.match_service import SentenceMatchService
from sentence_search.matching_rules import is_matchable_sentence
from sentence_search.models import SentenceOccurrence
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.sentence_summary_service import SentenceSummaryService


class SentenceService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
        match_service: SentenceMatchService,
        summary_service: SentenceSummaryService,
    ):
        """Save the stores used when a sentence is added."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres
        self.match_service = match_service
        self.summary_service = summary_service

    def add_sentence(
        self,
        sentence: SentenceOccurrence,
    ) -> dict:
        """Index one sentence, calculate matches, and refresh saved counts."""

        source = sentence.model_dump(mode="json")
        # Validate first so a bad global ID cannot leave OpenSearch half updated.
        self.opensearch.validate_occurrence_identity([source])
        key = source["sentenceKey"]
        existing = self.opensearch.existing_catalog_keys([key])
        catalog_indexed = 0
        matchable = is_matchable_sentence(source)
        if key not in existing:
            self.opensearch.bulk_index_catalog([self._catalog_source(source)])
            catalog_indexed = 1

        self.opensearch.bulk_index_occurrences([source])
        # The semantic vector must be searchable before matching starts.
        self.opensearch.refresh_indices()
        registration = self.postgres.register_sentence_keys([source])
        match_result = None
        summary_result = None
        if matchable:
            match_result = self.match_service.match_sentence_key(
                key,
                self.config.match_threshold,
            )
            summary_result = self.summary_service.refresh_affected_applications([key])
        return {
            "globalId": source["globalId"],
            "sentenceKey": key,
            "catalogDocumentIndexed": bool(catalog_indexed),
            "sentenceKeyRegistered": key in registration["newSentenceKeys"],
            "matchCalculated": matchable,
            "threshold": self.config.match_threshold,
            "status": "completed" if matchable else "skipped",
            "matchResult": match_result,
            "summaryResult": summary_result,
        }

    @staticmethod
    def _catalog_source(source: dict) -> dict:
        """Keep only fields allowed by the strict catalog mapping."""

        return {
            "sentenceContent": source["sentenceContent"],
            "sentenceKey": source["sentenceKey"],
            "createdAt": source["createdAt"],
            "updatedAt": source["updatedAt"],
        }
