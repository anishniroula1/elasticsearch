"""Unpaginated semantic summary for every entity in an application."""

from typing import Any

from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.search_utils import SemanticSearchUtilities


class SemanticSummaryService:
    def __init__(self, store: OpenSearchStore):
        self.utils = SemanticSearchUtilities(store)

    def application_summary(
        self,
        application_id: str,
        threshold: int,
    ) -> dict[str, Any]:
        """Build the existing complete application semantic summary."""

        entities = self.utils.application_entity_summaries(
            application_id,
            threshold,
        )
        return {
            **self.utils.response_metadata(
                application_id,
                threshold,
                "semanticCatalog",
            ),
            "totalUniqueEntities": len(entities),
            "entities": entities,
        }
