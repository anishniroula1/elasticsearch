"""Semantic search and source locations for one supplied entity text."""

from typing import Any

from semantic_search.opensearch_store import SEMANTIC_FIELD, OpenSearchStore
from semantic_search.search_utils import SemanticSearchUtilities
from semantic_search.text import normalize_text, semantic_key


class SemanticTextSearchService:
    def __init__(self, store: OpenSearchStore):
        self.store = store
        self.utils = SemanticSearchUtilities(store)

    def search_text(
        self,
        application_id: str,
        text: str,
        threshold: int,
    ) -> dict[str, Any]:
        """Search the catalog, then load matching occurrence locations."""

        query_key = semantic_key(text)
        stored_vector = self.store.catalog_vector(query_key)
        if stored_vector is None:
            candidates = self.utils.catalog_text_matches(
                text,
                query_key,
                threshold,
            )
            query_embedding_source = "titan"
        else:
            candidates = self.utils.single_stored_vector_matches(
                query_key,
                stored_vector,
                threshold,
            )
            if not any(
                candidate["semanticKey"] == query_key
                for candidate in candidates
            ):
                candidates.append(
                    {
                        "semanticKey": query_key,
                        "normalizedText": normalize_text(text),
                        SEMANTIC_FIELD: text,
                        "matchPercentage": 100.0,
                        "matchType": "exact",
                    }
                )
            query_embedding_source = "semanticCatalog"

        candidates_by_key = {
            candidate["semanticKey"]: candidate
            for candidate in candidates
        }
        occurrences = self.utils.matching_occurrences(
            application_id,
            list(candidates_by_key),
        )

        matches_by_id: dict[str, dict[str, Any]] = {}
        for occurrence in occurrences:
            source = occurrence.copy()
            key = source.pop("semanticKey")
            candidate = candidates_by_key[key]
            entity_id = source["entityId"]
            match = matches_by_id.setdefault(
                entity_id,
                {
                    "entityId": entity_id,
                    "entitySearchText": candidate[SEMANTIC_FIELD],
                    "matchPercentage": candidate["matchPercentage"],
                    "matchType": candidate["matchType"],
                    "applicationIds": set(),
                    "sourceLocations": [],
                },
            )
            if candidate["matchPercentage"] > match["matchPercentage"]:
                match["entitySearchText"] = candidate[SEMANTIC_FIELD]
                match["matchPercentage"] = candidate["matchPercentage"]
                match["matchType"] = candidate["matchType"]
            match["applicationIds"].add(source["applicationId"])
            match["sourceLocations"].append(source)

        matches = []
        for match in matches_by_id.values():
            locations = sorted(
                match.pop("sourceLocations"),
                key=lambda item: (
                    item["applicationId"],
                    item["sentenceEntityId"],
                ),
            )
            application_ids = match.pop("applicationIds")
            matches.append(
                {
                    **match,
                    "uniqueApplicationIdCount": len(application_ids),
                    "totalCount": len(locations),
                    "sourceLocations": locations,
                }
            )

        matches.sort(
            key=lambda item: (
                -item["matchPercentage"],
                item["entityId"],
            )
        )
        return {
            **self.utils.response_metadata(
                application_id,
                threshold,
                query_embedding_source,
            ),
            "searchedText": text,
            "totalMatches": len(matches),
            "totalSourceLocations": sum(
                match["totalCount"] for match in matches
            ),
            "matches": matches,
        }
