from sentence_search.opensearch_store import OpenSearchStore


class SentenceDeletionService:
    def __init__(self, opensearch: OpenSearchStore):
        """Save the OpenSearch store used by application and document deletion."""

        self.opensearch = opensearch

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
        """Delete occurrences and catalog vectors that are no longer used."""

        catalog_keys = self.opensearch.deletion_catalog_keys(
            application_id,
            tsp_id,
        )
        occurrence_count = self.opensearch.delete_occurrences(
            application_id,
            tsp_id,
        )
        catalog_result = self.opensearch.delete_unused_catalog_keys(catalog_keys)
        return {
            "applicationId": application_id,
            "tspId": tsp_id,
            "occurrencesDeleted": occurrence_count,
            "catalogDocumentsDeleted": catalog_result["deleted"],
        }
