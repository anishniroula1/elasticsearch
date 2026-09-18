from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.sentence_summary_service import SentenceSummaryService


class SentenceDeletionService:
    def __init__(
        self,
        opensearch: OpenSearchStore,
        postgres: PostgresStore,
        summary_service: SentenceSummaryService,
    ):
        """Save the stores used by application and document deletion."""

        self.opensearch = opensearch
        self.postgres = postgres
        self.summary_service = summary_service

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
        """Delete occurrences, unused vectors, and unused key relationships."""

        catalog_keys = self.opensearch.deletion_catalog_keys(
            application_id,
            tsp_id,
        )
        deleted_groups = self.opensearch.deletion_application_groups(
            application_id,
            tsp_id,
        )
        key_rows = self.postgres.matching_keys(catalog_keys)
        affected_keys = set(catalog_keys)
        for key in catalog_keys:
            affected_keys.update(key_rows[key]["matchingSentenceKeys"])

        occurrence_count = self.opensearch.delete_occurrences(
            application_id,
            tsp_id,
        )
        catalog_result = self.opensearch.delete_unused_catalog_keys(catalog_keys)
        postgres_result = self.postgres.delete_keys(catalog_result["sentenceKeys"])
        surviving_groups = self.opensearch.application_groups_for_keys(
            list(affected_keys)
        )

        summaries_deleted = 0
        if application_id is not None and tsp_id is None:
            summaries_deleted = self.postgres.delete_application_summaries(
                application_id
            )
            surviving_groups = [
                group
                for group in surviving_groups
                if group["applicationId"] != application_id
            ]
        else:
            # A TSP deletion can leave other sections in the same application.
            # Refresh its old groups so an empty or smaller total is saved.
            surviving_groups.extend(deleted_groups)
        summaries_refreshed = self.summary_service.refresh_application_groups(
            surviving_groups
        )
        return {
            "applicationId": application_id,
            "tspId": tsp_id,
            "occurrencesDeleted": occurrence_count,
            "catalogDocumentsDeleted": catalog_result["deleted"],
            "applicationSummariesDeleted": summaries_deleted,
            "applicationSummariesRefreshed": summaries_refreshed,
            **postgres_result,
        }
