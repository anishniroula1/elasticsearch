"""Compatibility facade for the separated semantic service modules."""

from typing import Any

from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.paginated_summary_service import (
    PaginatedSemanticSummaryService,
)
from semantic_search.search_utils import (
    _cosine_percentage,
    _minimum_opensearch_score,
)
from semantic_search.summary_service import SemanticSummaryService
from semantic_search.text_search_service import SemanticTextSearchService

__all__ = [
    "SemanticSearchService",
    "_cosine_percentage",
    "_minimum_opensearch_score",
]


class SemanticSearchService:
    """Preserve the original API while delegating to focused services."""

    def __init__(self, store: OpenSearchStore):
        self.summary = SemanticSummaryService(store)
        self.text_search = SemanticTextSearchService(store)
        self.paginated_summary = PaginatedSemanticSummaryService(store)

    def application_summary(
        self,
        application_id: str,
        threshold: int,
    ) -> dict[str, Any]:
        return self.summary.application_summary(application_id, threshold)

    def search_text(
        self,
        application_id: str,
        text: str,
        threshold: int,
    ) -> dict[str, Any]:
        return self.text_search.search_text(application_id, text, threshold)

    def application_matches(
        self,
        application_id: str,
        threshold: int,
        next_token: str | None,
    ) -> dict[str, Any]:
        return self.paginated_summary.application_matches(
            application_id,
            threshold,
            next_token,
        )
