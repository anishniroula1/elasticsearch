"""Cursor pages of application entities that have outside matches."""

from math import ceil
from typing import Any

from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.pagination import (
    decode_semantic_page_token,
    encode_semantic_page_token,
)
from semantic_search.search_utils import (
    ENTITY_PAGE_SIZE,
    SemanticSearchUtilities,
)

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
        """Resume from an OpenSearch cursor and return up to 100 matches."""

        if next_token:
            state = decode_semantic_page_token(
                next_token,
                application_id=application_id,
                threshold=threshold,
            )
            after_key = state["afterKey"]
            total_unique_entities = state["totalUniqueEntities"]
            total_matching_entities = state["totalMatchingEntities"]
            returned_before = state["returnedMatchingEntities"]
        else:
            after_key = None
            total_unique_entities = 0
            total_matching_entities = 0
            returned_before = 0

        entities, next_after_key, evaluated_entities = self._matching_page(
            application_id,
            threshold,
            after_key,
        )

        if not next_token:
            remaining_unique, remaining_matching = self._remaining_totals(
                application_id,
                threshold,
                next_after_key,
            )
            total_unique_entities = evaluated_entities + remaining_unique
            total_matching_entities = len(entities) + remaining_matching

        returned_matching_entities = returned_before + len(entities)
        new_token = encode_semantic_page_token(
            application_id=application_id,
            threshold=threshold,
            after_key=next_after_key,
            total_unique_entities=total_unique_entities,
            total_matching_entities=total_matching_entities,
            returned_matching_entities=returned_matching_entities,
        )
        total_pages = (
            ceil(total_matching_entities / SEMANTIC_MATCH_PAGE_SIZE)
            if total_matching_entities
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
            "totalMatchingEntities": total_matching_entities,
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

    def _matching_page(
        self,
        application_id: str,
        threshold: int,
        after_key: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, int]:
        matches = []
        evaluated_entities = 0
        cursor = after_key

        while len(matches) < SEMANTIC_MATCH_PAGE_SIZE:
            remaining = SEMANTIC_MATCH_PAGE_SIZE - len(matches)
            entities, cursor = self.utils.application_entity_summary_page(
                application_id,
                threshold,
                after_key=cursor,
                size=remaining,
            )
            evaluated_entities += len(entities)
            matches.extend(self._matching_entities(entities))
            if cursor is None:
                break

        return matches, cursor, evaluated_entities

    def _remaining_totals(
        self,
        application_id: str,
        threshold: int,
        after_key: dict[str, Any] | None,
    ) -> tuple[int, int]:
        """Calculate exact first-page totals after its continuation point."""

        total_unique_entities = 0
        total_matching_entities = 0
        cursor = after_key
        while cursor is not None:
            entities, cursor = self.utils.application_entity_summary_page(
                application_id,
                threshold,
                after_key=cursor,
                size=ENTITY_PAGE_SIZE,
            )
            total_unique_entities += len(entities)
            total_matching_entities += len(
                self._matching_entities(entities)
            )
        return total_unique_entities, total_matching_entities

    @staticmethod
    def _matching_entities(
        entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            entity
            for entity in entities
            if (
                entity["exactMatchCount"] > 0
                or entity["similarMatchCount"] > 0
            )
        ]
