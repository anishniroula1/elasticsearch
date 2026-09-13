"""Fast semantic matching over a deduplicated vector catalog."""

from collections.abc import Callable
from typing import Any

from semantic_search.opensearch_store import (
    CATALOG_VECTOR_FIELD,
    SEMANTIC_FIELD,
    OpenSearchStore,
)

ENTITY_PAGE_SIZE = 1_000
CATALOG_PAGE_SIZE = 1_000
OCCURRENCE_PAGE_SIZE = 5_000
MSEARCH_BATCH_SIZE = 100
TERMS_BATCH_SIZE = 10_000
UNIQUE_ENTITY_COUNT_PRECISION = 40_000

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


class SemanticSearchUtilities:
    """Shared OpenSearch operations used by all semantic endpoints."""

    def __init__(self, store: OpenSearchStore):
        self.store = store

    def vector_space_type(self) -> str:
        space_type = self.store.vector_space_type
        if space_type != "cosinesimil":
            raise RuntimeError(
                "Semantic catalog vector space is not initialized"
            )
        return space_type

    def response_metadata(
        self,
        application_id: str,
        threshold: int,
        query_embedding_source: str,
    ) -> dict[str, Any]:
        """Build the response fields shared by semantic endpoints."""

        return {
            "applicationId": application_id,
            "thresholdPercentage": threshold,
            "similarityMetric": "cosine",
            "vectorSpaceType": self.vector_space_type(),
            "queryEmbeddingSource": query_embedding_source,
        }

    def application_entity_summaries(
        self,
        application_id: str,
        threshold: int,
    ) -> list[dict[str, Any]]:
        """Return every application entity with its outside match counts."""

        entities: list[dict[str, Any]] = []
        after_key = None
        while True:
            page, after_key, _ = self.application_entity_summary_page(
                application_id,
                threshold,
                after_key=after_key,
                size=ENTITY_PAGE_SIZE,
            )
            entities.extend(page)
            if after_key is None:
                break

        entities.sort(
            key=lambda item: (
                -item["countInCurrentCase"],
                item["normalizedText"],
                item["entityId"],
            )
        )
        return entities

    def application_entity_summary_page(
        self,
        application_id: str,
        threshold: int,
        *,
        after_key: dict[str, Any] | None,
        size: int,
        include_total: bool = False,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, Any] | None,
        int | None,
    ]:
        """Summarize one OpenSearch composite-aggregation page."""

        if size < 1:
            raise ValueError("Semantic summary page size must be positive")
        entities, next_after_key, total_unique_entities = (
            self._application_entities_page(
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
        matches_by_key = self._stored_vector_matches(
            semantic_keys,
            vectors,
            threshold,
        )

        candidate_keys = list(
            dict.fromkeys(
                match["semanticKey"]
                for matches in matches_by_key.values()
                for match in matches
            )
        )
        occurrence_counts = self._occurrence_counts(
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

    def _application_entities_page(
        self,
        application_id: str,
        *,
        after_key: dict[str, Any] | None,
        size: int,
        include_total: bool,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, Any] | None,
        int | None,
    ]:
        composite: dict[str, Any] = {
            "size": size,
            "sources": [
                {"entityId": {"terms": {"field": "entityId"}}}
            ],
        }
        if after_key:
            composite["after"] = after_key

        aggregations: dict[str, Any] = {
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
                    "precision_threshold": (
                        UNIQUE_ENTITY_COUNT_PRECISION
                    ),
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

    def _stored_vector_matches(
        self,
        semantic_keys: list[str],
        vectors: dict[str, list[float]],
        threshold: int,
    ) -> dict[str, list[dict[str, Any]]]:
        matches = {key: [] for key in semantic_keys}
        pending = [
            (key, vectors[key], None)
            for key in semantic_keys
        ]

        while pending:
            next_pending = []
            for offset in range(0, len(pending), MSEARCH_BATCH_SIZE):
                batch = pending[offset : offset + MSEARCH_BATCH_SIZE]
                bodies = [
                    self._catalog_vector_body(
                        source_key,
                        vector,
                        threshold,
                        after_key,
                        self.vector_space_type(),
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
                    matches[source_key].extend(
                        _catalog_candidate(
                            source_key,
                            hit["_source"],
                            hit["_score"],
                            threshold,
                            self.vector_space_type(),
                        )
                        for hit in self._catalog_hits(result)
                    )
                    after_key = result.get("after_key")
                    if after_key and len(result["buckets"]) == CATALOG_PAGE_SIZE:
                        next_pending.append(
                            (source_key, vector, after_key)
                        )
            pending = next_pending

        for source_matches in matches.values():
            source_matches.sort(key=_match_sort_key)
        return matches

    def single_stored_vector_matches(
        self,
        source_key: str,
        vector: list[float],
        threshold: int,
    ) -> list[dict[str, Any]]:
        return self._paged_catalog_matches(
            source_key,
            threshold,
            lambda after_key: self._catalog_vector_body(
                source_key,
                vector,
                threshold,
                after_key,
                self.vector_space_type(),
            ),
        )

    def catalog_text_matches(
        self,
        text: str,
        source_key: str,
        threshold: int,
    ) -> list[dict[str, Any]]:
        return self._paged_catalog_matches(
            source_key,
            threshold,
            lambda after_key: self._catalog_text_body(
                text,
                source_key,
                threshold,
                after_key,
                self.vector_space_type(),
            ),
        )

    def _paged_catalog_matches(
        self,
        source_key: str,
        threshold: int,
        body_factory: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        matches = []
        after_key = None
        while True:
            response = self.store.search_catalog(body_factory(after_key))
            result = self._catalog_result(response)
            matches.extend(
                _catalog_candidate(
                    source_key,
                    hit["_source"],
                    hit["_score"],
                    threshold,
                    self.vector_space_type(),
                )
                for hit in self._catalog_hits(result)
            )
            after_key = result.get("after_key")
            if not after_key or len(result["buckets"]) < CATALOG_PAGE_SIZE:
                break
        matches.sort(key=_match_sort_key)
        return matches

    @staticmethod
    def _catalog_result(response: dict[str, Any]) -> dict[str, Any]:
        if "error" in response:
            raise RuntimeError(
                f"OpenSearch semantic search failed: {response['error']}"
            )
        return response["aggregations"]["matches"]

    @staticmethod
    def _catalog_hits(result: dict[str, Any]):
        for bucket in result["buckets"]:
            yield bucket["sample"]["hits"]["hits"][0]

    @staticmethod
    def _catalog_vector_body(
        source_key: str,
        vector: list[float],
        threshold: int,
        after_key: dict[str, Any] | None,
        vector_space_type: str,
    ) -> dict[str, Any]:
        return _catalog_search_body(
            source_key,
            {
                "knn": {
                    CATALOG_VECTOR_FIELD: {
                        "vector": vector,
                        "min_score": _minimum_opensearch_score(
                            threshold,
                            vector_space_type,
                        ),
                    }
                }
            },
            after_key,
        )

    def _catalog_text_body(
        self,
        text: str,
        source_key: str,
        threshold: int,
        after_key: dict[str, Any] | None,
        vector_space_type: str,
    ) -> dict[str, Any]:
        return _catalog_search_body(
            source_key,
            {
                "neural": {
                    CATALOG_VECTOR_FIELD: {
                        "query_text": text,
                        "model_id": self.store.config.semantic_model_id,
                        "min_score": _minimum_opensearch_score(
                            threshold,
                            vector_space_type,
                        ),
                    }
                }
            },
            after_key,
        )

    def _occurrence_counts(
        self,
        application_id: str,
        semantic_keys: list[str],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for offset in range(0, len(semantic_keys), TERMS_BATCH_SIZE):
            key_batch = semantic_keys[offset : offset + TERMS_BATCH_SIZE]
            after_key = None
            while True:
                composite: dict[str, Any] = {
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
                        "query": _occurrence_filter(
                            application_id,
                            key_batch,
                        ),
                        "aggs": {
                            "matches": {"composite": composite}
                        },
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

    def matching_occurrences(
        self,
        application_id: str,
        semantic_keys: list[str],
    ) -> list[dict[str, Any]]:
        occurrences = []
        for offset in range(0, len(semantic_keys), TERMS_BATCH_SIZE):
            key_batch = semantic_keys[offset : offset + TERMS_BATCH_SIZE]
            after_key = None
            while True:
                composite: dict[str, Any] = {
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
                        "query": _occurrence_filter(
                            application_id,
                            key_batch,
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
                occurrences.extend(
                    bucket["sample"]["hits"]["hits"][0]["_source"]
                    for bucket in result["buckets"]
                )
                after_key = result.get("after_key")
                if (
                    not after_key
                    or len(result["buckets"]) < OCCURRENCE_PAGE_SIZE
                ):
                    break
        return occurrences


def _catalog_search_body(
    source_key: str,
    vector_query: dict[str, Any],
    after_key: dict[str, Any] | None,
) -> dict[str, Any]:
    composite: dict[str, Any] = {
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


def _catalog_candidate(
    source_key: str,
    candidate: dict[str, Any],
    opensearch_score: float,
    threshold: int,
    vector_space_type: str,
) -> dict[str, Any]:
    candidate_key = candidate["semanticKey"]
    if source_key == candidate_key:
        percentage = 100.0
        match_type = "exact"
    else:
        percentage = _cosine_percentage(
            opensearch_score,
            vector_space_type,
        )
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


def _occurrence_filter(
    application_id: str,
    semantic_keys: list[str],
) -> dict[str, Any]:
    return {
        "bool": {
            "filter": [{"terms": {"semanticKey": semantic_keys}}],
            "must_not": [{"term": {"applicationId": application_id}}],
        }
    }


def _minimum_opensearch_score(
    threshold: int,
    vector_space_type: str = "cosinesimil",
) -> float:
    if vector_space_type != "cosinesimil":
        raise ValueError(f"Unsupported vector space: {vector_space_type}")
    cosine_threshold = threshold / 100.0
    return (1.0 + cosine_threshold) / 2.0


def _cosine_percentage(
    opensearch_score: float,
    vector_space_type: str = "cosinesimil",
) -> float:
    if vector_space_type != "cosinesimil":
        raise ValueError(f"Unsupported vector space: {vector_space_type}")
    cosine_similarity = (2.0 * opensearch_score) - 1.0
    cosine_similarity = max(-1.0, min(1.0, cosine_similarity))
    return round(max(0.0, cosine_similarity) * 100.0, 2)


def _match_sort_key(match: dict[str, Any]):
    return (-match["matchPercentage"], match["semanticKey"])
