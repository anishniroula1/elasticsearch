"""Cursor pages of application entities with outside match counts."""

from math import ceil
from typing import Any

from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.pagination import (
    decode_semantic_page_token,
    encode_semantic_page_token,
)
from semantic_search.search_utils import SemanticSearchUtilities

SEMANTIC_MATCH_PAGE_SIZE = 100


class PaginatedSemanticSummaryService:
    def __init__(self, store: OpenSearchStore):
        self.utils = SemanticSearchUtilities(store)

    def application_matches(
        self,
        application_id: str,
        threshold: int,
        next_token: str | None,
    ) -> dict[str, Any]:
        """Return match counts for one page of 100 source entities."""

        if next_token:
            state = decode_semantic_page_token(
                next_token,
                application_id=application_id,
                threshold=threshold,
            )
            after_key = state["afterKey"]
            total_unique_entities = state["totalUniqueEntities"]
            returned_before = state["returnedEntities"]
            include_total = False
        else:
            after_key = None
            total_unique_entities = 0
            returned_before = 0
            include_total = True

        entities, next_after_key, first_page_total = (
            self.utils.application_entity_summary_page(
                application_id,
                threshold,
                after_key=after_key,
                size=SEMANTIC_MATCH_PAGE_SIZE,
                include_total=include_total,
            )
        )
        if include_total:
            total_unique_entities = int(first_page_total or 0)

        returned_after = returned_before + len(entities)
        new_token = encode_semantic_page_token(
            application_id=application_id,
            threshold=threshold,
            after_key=next_after_key,
            total_unique_entities=total_unique_entities,
            returned_entities=returned_after,
        )
        total_pages = (
            ceil(total_unique_entities / SEMANTIC_MATCH_PAGE_SIZE)
            if total_unique_entities
            else 0
        )
        page = (returned_before // SEMANTIC_MATCH_PAGE_SIZE) + 1

        return {
            **self.utils.response_metadata(
                application_id,
                threshold,
                "semanticCatalog",
            ),
            "totalUniqueEntities": total_unique_entities,
            "returnedEntities": len(entities),
            "pagination": {
                "page": page,
                "pageSize": SEMANTIC_MATCH_PAGE_SIZE,
                "totalPages": total_pages,
                "hasPreviousPage": returned_before > 0,
                "hasNextPage": new_token is not None,
            },
            "nextToken": new_token,
            "entities": entities,
        }
