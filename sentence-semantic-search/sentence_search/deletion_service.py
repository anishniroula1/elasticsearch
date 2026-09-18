from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore


class SentenceDeletionService:
    def __init__(
        self,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
    ):
        """Save the stores used by application and document deletion."""

        self.opensearch = opensearch
        self.postgres = postgres

    def delete_application(self, application_id: str) -> dict:
        """Delete every sentence owned by one application."""

        return self._delete(application_id)

    def delete_tsp(
        self,
        tsp_id: str,
        application_id: str | None = None,
    ) -> dict:
        """Delete a TSP document with an optional application restriction."""

        return self._delete(application_id, tsp_id)

    def _delete(
        self,
        application_id: str | None,
        tsp_id: str | None = None,
    ) -> dict:
        """Delete occurrences, unused vectors, and PostgreSQL match lists."""

        catalog_keys = self.opensearch.deletion_catalog_keys(
            application_id,
            tsp_id,
        )
        occurrence_count = self.opensearch.delete_occurrences(
            application_id,
            tsp_id,
        )
        catalog_count = self.opensearch.delete_unused_catalog_keys(catalog_keys)
        postgres_result = self.postgres.delete_sentences(application_id, tsp_id)
        return {
            "applicationId": application_id,
            "tspId": tsp_id,
            "occurrencesDeleted": occurrence_count,
            "catalogDocumentsDeleted": catalog_count,
            **postgres_result,
        }
