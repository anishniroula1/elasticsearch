from collections.abc import Iterator
from math import ceil

from semantic_search.opensearch_store import (
    CATALOG_VECTOR_FIELD,
    SEMANTIC_FIELD,
    OpenSearchStore,
)
from semantic_search.pagination import (
    decode_text_search_page_token,
    encode_text_search_page_token,
)
from semantic_search.search_utils import (
    cosine_percentage,
    minimum_opensearch_score,
    response_metadata,
    vector_space_type,
)
from semantic_search.text import semantic_key

CATALOG_PAGE_SIZE = 1_000  # Unique semantic matches read per catalog query.
OCCURRENCE_PAGE_SIZE = 5_000  # Occurrence records read per aggregation page.
TERMS_BATCH_SIZE = 10_000  # Semantic keys allowed in one terms filter.
TEXT_SEARCH_PAGE_SIZE = 100  # Matched entities returned by the new endpoint.
TEXT_MATCH_COUNT_PRECISION = 40_000  # Accuracy used for the first-page total.

CATALOG_FIELDS = [
    "semanticKey",
    "normalizedText",
    "entitySearchText",
]

OCCURRENCE_FIELDS = [
    "sentenceEntityId",
    "applicationId",
    "tspId",
    "globalId",
    "entityId",
    "semanticKey",
    "rawEntity",
    "normalizedText",
    "entitySearchText",
    "entityType",
    "possibleSanction",
    "beginOffset",
    "endOffset",
    "score",
    "source",
    "documentType",
    "createdAt",
    "updatedAt",
]


def _catalog_match_sort_key(match: dict) -> tuple:
    """Put the best catalog match first, then sort by key."""

    return (-match["matchPercentage"], match["semanticKey"])


def _source_location_sort_key(location: dict) -> tuple:
    """Sort source locations by application and record ID."""

    return (location["applicationId"], location["sentenceEntityId"])


def _entity_match_sort_key(match: dict) -> tuple:
    """Put the best entity match first, then sort by entity ID."""

    return (-match["matchPercentage"], match["entityId"])


class SemanticTextSearchService:
    def __init__(self, store: OpenSearchStore):
        """Save the OpenSearch store used by this service."""

        self.store = store

    def search_text(
        self,
        application_id: str,
        text: str,
        threshold: int,
    ) -> dict:
        """Find every entity that matches the search text."""

        _, candidates, query_embedding_source = (
            self._catalog_candidates_for_text(
                text,
                threshold,
            )
        )

        candidates_by_key = {
            candidate["semanticKey"]: candidate
            for candidate in candidates
        }
        occurrences = self._load_matching_occurrences(
            application_id,
            list(candidates_by_key),
        )
        matches = self._group_occurrences_by_entity(
            occurrences,
            candidates_by_key,
        )

        return {
            **response_metadata(
                self.store,
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

    def search_text_page(
        self,
        application_id: str,
        text: str,
        threshold: int,
        next_token: str | None,
    ) -> dict:
        """Return one page of entities that match the search text.

        Input:
            application_id="A1", text="Acme", threshold=90
        Output:
            Up to 100 matches and a nextToken for the next page.
        """

        query_key = semantic_key(text)
        if next_token:
            state = decode_text_search_page_token(
                next_token,
                application_id=application_id,
                threshold=threshold,
                query_key=query_key,
            )
            after_key = state["afterKey"]
            total_matches = state["totalMatches"]
            returned_before = state["returnedMatches"]
            include_total = False
        else:
            after_key = None
            total_matches = 0
            returned_before = 0
            include_total = True

        _, candidates, query_embedding_source = (
            self._catalog_candidates_for_text(text, threshold)
        )

        candidates_by_key = {
            candidate["semanticKey"]: candidate
            for candidate in candidates
        }
        entity_ids, next_after_key, first_page_total = (
            self._load_matching_entity_page(
                application_id,
                list(candidates_by_key),
                after_key=after_key,
                include_total=include_total,
            )
        )
        if include_total:
            total_matches = int(first_page_total or 0)

        # Load full source locations only for the entity IDs on this page.
        occurrences = self._load_matching_occurrences(
            application_id,
            list(candidates_by_key),
            entity_ids=entity_ids,
        )
        matches = self._group_occurrences_by_entity(
            occurrences,
            candidates_by_key,
        )

        returned_after = returned_before + len(entity_ids)
        new_token = encode_text_search_page_token(
            application_id=application_id,
            threshold=threshold,
            query_key=query_key,
            after_key=next_after_key,
            total_matches=total_matches,
            returned_matches=returned_after,
        )
        total_pages = (
            ceil(total_matches / TEXT_SEARCH_PAGE_SIZE)
            if total_matches
            else 0
        )
        page = (returned_before // TEXT_SEARCH_PAGE_SIZE) + 1

        return {
            **response_metadata(
                self.store,
                application_id,
                threshold,
                query_embedding_source,
            ),
            "searchedText": text,
            "totalMatches": total_matches,
            "returnedMatches": len(matches),
            "returnedSourceLocations": sum(
                match["totalCount"] for match in matches
            ),
            "pagination": {
                "page": page,
                "pageSize": TEXT_SEARCH_PAGE_SIZE,
                "totalPages": total_pages,
                "hasPreviousPage": returned_before > 0,
                "hasNextPage": new_token is not None,
            },
            "nextToken": new_token,
            "matches": matches,
        }

    def _catalog_candidates_for_text(
        self,
        text: str,
        threshold: int,
    ) -> tuple:
        """Find matching catalog text and tell how its vector was made.

        Input:
            text="Acme", threshold=90
        Output:
            The text key, catalog matches, and vector source.
        """

        query_key = semantic_key(text)
        stored_vector = self.store.catalog_vector(query_key)
        candidates = self._find_catalog_matches(
            text,
            query_key,
            threshold,
            stored_vector,
        )
        query_embedding_source = (
            "titan" if stored_vector is None else "semanticCatalog"
        )
        return query_key, candidates, query_embedding_source

    def _find_catalog_matches(
        self,
        text: str,
        source_key: str,
        threshold: int,
        stored_vector: list | None,
    ) -> list:
        """Get all unique catalog matches for the search text."""

        space_type = vector_space_type(self.store)
        matches = []
        after_key = None
        while True:
            if stored_vector is None:
                body = self._build_neural_text_query(
                    text,
                    source_key,
                    threshold,
                    after_key,
                    space_type,
                )
            else:
                body = self._build_stored_vector_query(
                    source_key,
                    stored_vector,
                    threshold,
                    after_key,
                    space_type,
                )
            response = self.store.search_catalog(body)
            result = self._catalog_result(response)
            for hit in self._catalog_hits(result):
                matches.append(
                    self._catalog_candidate(
                        source_key,
                        hit["_source"],
                        hit["_score"],
                        threshold,
                        space_type,
                    )
                )
            after_key = result.get("after_key")
            if not after_key or len(result["buckets"]) < CATALOG_PAGE_SIZE:
                break

        matches.sort(key=_catalog_match_sort_key)
        return matches

    @staticmethod
    def _build_stored_vector_query(
        source_key: str,
        vector: list,
        threshold: int,
        after_key: dict | None,
        space_type: str,
    ) -> dict:
        """Make a catalog search using a saved vector."""

        vector_query = {
            "knn": {
                CATALOG_VECTOR_FIELD: {
                    "vector": vector,
                    "min_score": minimum_opensearch_score(
                        threshold,
                        space_type,
                    ),
                }
            }
        }
        return SemanticTextSearchService._catalog_search_body(
            source_key,
            vector_query,
            after_key,
        )

    def _build_neural_text_query(
        self,
        text: str,
        source_key: str,
        threshold: int,
        after_key: dict | None,
        space_type: str,
    ) -> dict:
        """Make a catalog search that asks Titan to vectorize new text."""

        vector_query = {
            "neural": {
                CATALOG_VECTOR_FIELD: {
                    "query_text": text,
                    "model_id": self.store.config.semantic_model_id,
                    "min_score": minimum_opensearch_score(
                        threshold,
                        space_type,
                    ),
                }
            }
        }
        return self._catalog_search_body(
            source_key,
            vector_query,
            after_key,
        )

    @staticmethod
    def _catalog_search_body(
        source_key: str,
        vector_query: dict,
        after_key: dict | None,
    ) -> dict:
        """Make the OpenSearch request for unique catalog matches."""

        composite = {
            "size": CATALOG_PAGE_SIZE,
            "sources": [
                {"semanticKey": {"terms": {"field": "semanticKey"}}}
            ],
        }
        if after_key:
            composite["after"] = after_key
        return {
            "size": 0,
            "track_total_hits": False,
            "query": {
                "bool": {
                    "should": [
                        {"term": {"semanticKey": source_key}},
                        vector_query,
                    ],
                    "minimum_should_match": 1,
                }
            },
            "aggs": {
                "matches": {
                    "composite": composite,
                    "aggs": {
                        "sample": {
                            "top_hits": {
                                "size": 1,
                                "_source": CATALOG_FIELDS,
                            }
                        }
                    },
                }
            },
        }

    @staticmethod
    def _catalog_result(response: dict) -> dict:
        """Get catalog matches or report the OpenSearch error."""

        if "error" in response:
            raise RuntimeError(
                f"OpenSearch semantic search failed: {response['error']}"
            )
        return response["aggregations"]["matches"]

    @staticmethod
    def _catalog_hits(result: dict) -> Iterator:
        """Return one catalog record from each result group."""

        for bucket in result["buckets"]:
            yield bucket["sample"]["hits"]["hits"][0]

    @staticmethod
    def _catalog_candidate(
        source_key: str,
        candidate: dict,
        opensearch_score: float,
        threshold: int,
        space_type: str,
    ) -> dict:
        """Change one catalog result into an exact or similar match."""

        candidate_key = candidate["semanticKey"]
        if source_key == candidate_key:
            percentage = 100.0
            match_type = "exact"
        else:
            percentage = cosine_percentage(opensearch_score, space_type)
            if percentage < threshold:
                raise RuntimeError(
                    "OpenSearch returned a semantic result below min_score"
                )
            match_type = "similar"
        return {
            "semanticKey": candidate_key,
            "normalizedText": candidate["normalizedText"],
            SEMANTIC_FIELD: candidate[SEMANTIC_FIELD],
            "matchPercentage": percentage,
            "matchType": match_type,
        }

    def _load_matching_occurrences(
        self,
        application_id: str,
        semantic_keys: list,
        entity_ids: list | None = None,
    ) -> list:
        """Load source locations for the matching entity IDs."""

        occurrences = []
        if entity_ids is not None and not entity_ids:
            return occurrences
        for offset in range(0, len(semantic_keys), TERMS_BATCH_SIZE):
            key_batch = semantic_keys[offset : offset + TERMS_BATCH_SIZE]
            after_key = None
            while True:
                composite = {
                    "size": OCCURRENCE_PAGE_SIZE,
                    "sources": [
                        {
                            "sentenceEntityId": {
                                "terms": {"field": "sentenceEntityId"}
                            }
                        }
                    ],
                }
                if after_key:
                    composite["after"] = after_key
                response = self.store.search_occurrences(
                    {
                        "size": 0,
                        "track_total_hits": False,
                        "query": self._occurrence_filter(
                            application_id,
                            key_batch,
                            entity_ids,
                        ),
                        "aggs": {
                            "matches": {
                                "composite": composite,
                                "aggs": {
                                    "sample": {
                                        "top_hits": {
                                            "size": 1,
                                            "_source": OCCURRENCE_FIELDS,
                                        }
                                    }
                                },
                            }
                        },
                    }
                )
                result = response["aggregations"]["matches"]
                for bucket in result["buckets"]:
                    occurrences.append(
                        bucket["sample"]["hits"]["hits"][0]["_source"]
                    )
                after_key = result.get("after_key")
                if (
                    not after_key
                    or len(result["buckets"]) < OCCURRENCE_PAGE_SIZE
                ):
                    break
        return occurrences

    def _load_matching_entity_page(
        self,
        application_id: str,
        semantic_keys: list,
        *,
        after_key: dict | None,
        include_total: bool,
    ) -> tuple:
        """Load up to 100 matching entity IDs from OpenSearch.

        Input:
            Semantic keys and an optional after key.
        Output:
            Entity IDs, the next after key, and the first-page total.
        """

        if not semantic_keys:
            total = 0 if include_total else None
            return [], None, total

        # Read one extra ID so the API can tell whether another page exists.
        # Source locations are still loaded for no more than 100 entities.
        composite = {
            "size": TEXT_SEARCH_PAGE_SIZE + 1,
            "sources": [{"entityId": {"terms": {"field": "entityId"}}}],
        }
        if after_key:
            composite["after"] = after_key
        aggregations = {"entities": {"composite": composite}}
        if include_total:
            aggregations["totalMatchingEntities"] = {
                "cardinality": {
                    "field": "entityId",
                    "precision_threshold": TEXT_MATCH_COUNT_PRECISION,
                }
            }

        response = self.store.search_occurrences(
            {
                "size": 0,
                "track_total_hits": False,
                "query": self._occurrence_filter(
                    application_id,
                    semantic_keys,
                ),
                "aggs": aggregations,
            }
        )
        result = response["aggregations"]["entities"]
        buckets = result["buckets"]
        page_buckets = buckets[:TEXT_SEARCH_PAGE_SIZE]
        entity_ids = [
            bucket["key"]["entityId"] for bucket in page_buckets
        ]
        next_after_key = None
        if len(buckets) > TEXT_SEARCH_PAGE_SIZE:
            next_after_key = page_buckets[-1]["key"]

        total = None
        if include_total:
            total = int(
                response["aggregations"]["totalMatchingEntities"]["value"]
            )
        return entity_ids, next_after_key, total

    @staticmethod
    def _occurrence_filter(
        application_id: str,
        semantic_keys: list,
        entity_ids: list | None = None,
    ) -> dict:
        """Match catalog keys outside the supplied application."""

        filters = [{"terms": {"semanticKey": semantic_keys}}]
        if entity_ids is not None:
            filters.append({"terms": {"entityId": entity_ids}})
        return {
            "bool": {
                "filter": filters,
                "must_not": [{"term": {"applicationId": application_id}}],
            }
        }

    @staticmethod
    def _group_occurrences_by_entity(
        occurrences: list,
        candidates_by_key: dict,
    ) -> list:
        """Group source locations and keep each entity's best match."""

        matches_by_id = {}
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
            # An entity may occur under more than one semantic key. The result
            # should describe its strongest candidate while retaining every
            # source location found for that entity.
            if candidate["matchPercentage"] > match["matchPercentage"]:
                match["entitySearchText"] = candidate[SEMANTIC_FIELD]
                match["matchPercentage"] = candidate["matchPercentage"]
                match["matchType"] = candidate["matchType"]
            match["applicationIds"].add(source["applicationId"])
            match["sourceLocations"].append(source)

        matches = []
        for match in matches_by_id.values():
            locations = match.pop("sourceLocations")
            locations.sort(key=_source_location_sort_key)
            application_ids = match.pop("applicationIds")
            matches.append(
                {
                    **match,
                    "uniqueApplicationIdCount": len(application_ids),
                    "totalCount": len(locations),
                    "sourceLocations": locations,
                }
            )

        matches.sort(key=_entity_match_sort_key)
        return matches
