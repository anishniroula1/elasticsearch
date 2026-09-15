from collections.abc import Iterator
from math import ceil

from semantic_search.opensearch_store import (
    CATALOG_VECTOR_FIELD,
    SEMANTIC_FIELD,
    OpenSearchStore,
)
from semantic_search.pagination import (
    decode_semantic_page_token,
    encode_semantic_page_token,
)
from semantic_search.search_utils import (
    cosine_percentage,
    minimum_opensearch_score,
    response_metadata,
    vector_space_type,
)

ENTITY_PAGE_SIZE = 1_000  # Entities processed by the complete summary per page.
CATALOG_PAGE_SIZE = 1_000  # Unique semantic matches read per catalog query.
OCCURRENCE_PAGE_SIZE = 5_000  # Occurrence buckets read per count query.
MSEARCH_BATCH_SIZE = 100  # Vector queries sent in one OpenSearch msearch call.
TERMS_BATCH_SIZE = 10_000  # Semantic keys allowed in one terms filter.
UNIQUE_ENTITY_COUNT_PRECISION = 40_000  # Cardinality accuracy for page totals.
SEMANTIC_MATCH_PAGE_SIZE = 100  # Entities returned by the paginated endpoint.

ENTITY_FIELDS = [
    "entityId",
    "semanticKey",
    "normalizedText",
    "rawEntity",
    "entitySearchText",
    "entityType",
    "possibleSanction",
]

CATALOG_FIELDS = [
    "semanticKey",
    "normalizedText",
    "entitySearchText",
]


def _summary_sort_key(entity: dict) -> tuple:
    """Put common entities first, then sort by text and ID."""

    return (
        -entity["countInCurrentCase"],
        entity["normalizedText"],
        entity["entityId"],
    )


def _catalog_match_sort_key(match: dict) -> tuple:
    """Put the best match first, then sort by catalog ID."""

    return (-match["matchPercentage"], match["semanticKey"])


class SemanticSummaryService:
    def __init__(self, store: OpenSearchStore):
        """Save the OpenSearch store used by this service."""

        self.store = store

    def application_summary(
        self,
        application_id: str,
        threshold: int,
    ) -> dict:
        """Get the full match summary for one application."""

        entities = []
        after_key = None
        while True:
            page, after_key, _ = self.application_summary_page(
                application_id,
                threshold,
                after_key=after_key,
                size=ENTITY_PAGE_SIZE,
            )
            entities.extend(page)
            if after_key is None:
                break

        entities.sort(key=_summary_sort_key)
        return {
            **response_metadata(
                self.store,
                application_id,
                threshold,
                "semanticCatalog",
            ),
            "totalUniqueEntities": len(entities),
            "entities": entities,
        }

    def application_summary_page(
        self,
        application_id: str,
        threshold: int,
        *,
        after_key: dict | None,
        size: int,
        include_total: bool = False,
    ) -> tuple:
        """Get match counts for one page of application entities."""

        if size < 1:
            raise ValueError("Semantic summary page size must be positive")
        entities, next_after_key, total_unique_entities = (
            self._load_application_entities_page(
                application_id,
                after_key=after_key,
                size=size,
                include_total=include_total,
            )
        )
        if not entities:
            return [], None, total_unique_entities

        semantic_keys = list(
            dict.fromkeys(entity["semanticKey"] for entity in entities)
        )
        vectors = self.store.catalog_vectors(semantic_keys)
        matches_by_key = self._find_stored_vector_matches(
            semantic_keys,
            vectors,
            threshold,
        )

        # One occurrence aggregation counts all candidates for the page. This
        # avoids a separate occurrence query for each source entity.
        candidate_keys = list(
            dict.fromkeys(
                match["semanticKey"]
                for matches in matches_by_key.values()
                for match in matches
            )
        )
        occurrence_counts = self._count_matching_occurrences(
            application_id,
            candidate_keys,
        )

        for entity in entities:
            matches = matches_by_key[entity["semanticKey"]]
            entity["exactMatchCount"] = sum(
                occurrence_counts.get(match["semanticKey"], 0)
                for match in matches
                if match["matchType"] == "exact"
            )
            entity["similarMatchCount"] = sum(
                occurrence_counts.get(match["semanticKey"], 0)
                for match in matches
                if match["matchType"] == "similar"
            )
            entity.pop("semanticKey")

        return entities, next_after_key, total_unique_entities

    def _load_application_entities_page(
        self,
        application_id: str,
        *,
        after_key: dict | None,
        size: int,
        include_total: bool,
    ) -> tuple:
        """Get one page of unique entities and the first-page total."""

        composite = {
            "size": size,
            "sources": [{"entityId": {"terms": {"field": "entityId"}}}],
        }
        if after_key:
            composite["after"] = after_key

        aggregations = {
            "entities": {
                "composite": composite,
                "aggs": {
                    "sample": {
                        "top_hits": {
                            "size": 1,
                            "_source": ENTITY_FIELDS,
                        }
                    }
                },
            }
        }
        if include_total:
            aggregations["totalUniqueEntities"] = {
                "cardinality": {
                    "field": "entityId",
                    "precision_threshold": UNIQUE_ENTITY_COUNT_PRECISION,
                }
            }

        response = self.store.search_occurrences(
            {
                "size": 0,
                "track_total_hits": False,
                "query": {"term": {"applicationId": application_id}},
                "aggs": aggregations,
            }
        )
        result = response["aggregations"]["entities"]
        buckets = result["buckets"]
        entities = []
        for bucket in buckets:
            source = bucket["sample"]["hits"]["hits"][0]["_source"]
            entities.append(
                {
                    "entityId": bucket["key"]["entityId"],
                    "semanticKey": source["semanticKey"],
                    "normalizedText": source.get("normalizedText", ""),
                    "rawEntity": source.get("rawEntity", ""),
                    SEMANTIC_FIELD: source.get(SEMANTIC_FIELD, ""),
                    "entityType": source.get("entityType", ""),
                    "possibleSanction": source.get(
                        "possibleSanction",
                        False,
                    ),
                    "countInCurrentCase": bucket["doc_count"],
                }
            )

        next_after_key = result.get("after_key")
        if len(buckets) < size:
            next_after_key = None
        total_unique_entities = None
        if include_total:
            total_unique_entities = int(
                response["aggregations"]["totalUniqueEntities"]["value"]
            )
        return entities, next_after_key, total_unique_entities

    def _find_stored_vector_matches(
        self,
        semantic_keys: list,
        vectors: dict,
        threshold: int,
    ) -> dict:
        """Search the catalog for every vector on the current page."""

        space_type = vector_space_type(self.store)
        matches = {key: [] for key in semantic_keys}
        pending = [(key, vectors[key], None) for key in semantic_keys]

        # A composite aggregation may return another after_key for an
        # individual source vector. Keep only those searches in the next pass.
        while pending:
            next_pending = []
            for offset in range(0, len(pending), MSEARCH_BATCH_SIZE):
                batch = pending[offset : offset + MSEARCH_BATCH_SIZE]
                bodies = [
                    self._build_catalog_vector_query(
                        source_key,
                        vector,
                        threshold,
                        after_key,
                        space_type,
                    )
                    for source_key, vector, after_key in batch
                ]
                responses = self.store.multi_search_catalog(bodies)
                if len(responses) != len(batch):
                    raise RuntimeError(
                        "OpenSearch returned an invalid msearch response"
                    )
                for state, response in zip(batch, responses):
                    source_key, vector, _ = state
                    result = self._catalog_result(response)
                    for hit in self._catalog_hits(result):
                        matches[source_key].append(
                            self._catalog_candidate(
                                source_key,
                                hit["_source"],
                                hit["_score"],
                                threshold,
                                space_type,
                            )
                        )
                    next_after_key = result.get("after_key")
                    if (
                        next_after_key
                        and len(result["buckets"]) == CATALOG_PAGE_SIZE
                    ):
                        next_pending.append(
                            (source_key, vector, next_after_key)
                        )
            pending = next_pending

        for source_matches in matches.values():
            source_matches.sort(key=_catalog_match_sort_key)
        return matches

    @staticmethod
    def _build_catalog_vector_query(
        source_key: str,
        vector: list,
        threshold: int,
        after_key: dict | None,
        space_type: str,
    ) -> dict:
        """Make one catalog search using a saved vector and threshold."""

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
        return SemanticSummaryService._catalog_search_body(
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

    def _count_matching_occurrences(
        self,
        application_id: str,
        semantic_keys: list,
    ) -> dict:
        """Count matching records outside the current application."""

        counts = {}
        for offset in range(0, len(semantic_keys), TERMS_BATCH_SIZE):
            key_batch = semantic_keys[offset : offset + TERMS_BATCH_SIZE]
            after_key = None
            while True:
                composite = {
                    "size": OCCURRENCE_PAGE_SIZE,
                    "sources": [
                        {
                            "semanticKey": {
                                "terms": {"field": "semanticKey"}
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
                        ),
                        "aggs": {"matches": {"composite": composite}},
                    }
                )
                result = response["aggregations"]["matches"]
                for bucket in result["buckets"]:
                    key = bucket["key"]["semanticKey"]
                    counts[key] = counts.get(key, 0) + bucket["doc_count"]
                after_key = result.get("after_key")
                if (
                    not after_key
                    or len(result["buckets"]) < OCCURRENCE_PAGE_SIZE
                ):
                    break
        return counts

    @staticmethod
    def _occurrence_filter(
        application_id: str,
        semantic_keys: list,
    ) -> dict:
        """Make a filter that leaves out the current application."""

        return {
            "bool": {
                "filter": [{"terms": {"semanticKey": semantic_keys}}],
                "must_not": [{"term": {"applicationId": application_id}}],
            }
        }


class PaginatedSemanticSummaryService:
    def __init__(self, store: OpenSearchStore):
        """Save the store and the summary service."""

        self.store = store
        self.summary = SemanticSummaryService(store)

    def application_matches(
        self,
        application_id: str,
        threshold: int,
        next_token: str | None,
    ) -> dict:
        """Get match counts for up to 100 application entities."""

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
            self.summary.application_summary_page(
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
            **response_metadata(
                self.store,
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
