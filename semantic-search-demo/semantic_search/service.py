"""Fast semantic matching over a deduplicated vector catalog."""

from typing import Any, Callable

from semantic_search.opensearch_store import (
    CATALOG_VECTOR_FIELD,
    SEMANTIC_FIELD,
    OpenSearchStore,
)
from semantic_search.text import semantic_key


ENTITY_PAGE_SIZE = 1_000
CATALOG_PAGE_SIZE = 1_000
OCCURRENCE_PAGE_SIZE = 5_000
MSEARCH_BATCH_SIZE = 50
TERMS_BATCH_SIZE = 10_000

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


class SemanticSearchService:
    def __init__(self, store: OpenSearchStore):
        self.store = store

    def application_summary(
        self,
        application_id: str,
        threshold: int,
    ) -> dict[str, Any]:
        """Build an application summary using vectors created at seeding."""

        entities = self._application_entities(application_id)
        if not entities:
            return self._summary_response(
                application_id,
                threshold,
                [],
            )

        source_by_key = {
            entity["semanticKey"]: entity[SEMANTIC_FIELD]
            for entity in entities
        }
        vectors = self.store.catalog_vectors(list(source_by_key))
        matches_by_key = self._stored_vector_matches(
            source_by_key,
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

        return self._summary_response(
            application_id,
            threshold,
            entities,
        )

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
            candidates = self._catalog_text_matches(
                text,
                query_key,
                threshold,
            )
            query_embedding_source = "titan"
        else:
            candidates = self._single_stored_vector_matches(
                query_key,
                stored_vector,
                threshold,
            )
            query_embedding_source = "semanticCatalog"

        candidates_by_key = {
            candidate["semanticKey"]: candidate
            for candidate in candidates
        }
        occurrences = self._matching_occurrences(
            application_id,
            list(candidates_by_key),
        )

        matches_by_id: dict[str, dict[str, Any]] = {}
        for source in occurrences:
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
            "applicationId": application_id,
            "searchedText": text,
            "thresholdPercentage": threshold,
            "similarityMetric": "cosine",
            "queryEmbeddingSource": query_embedding_source,
            "totalMatches": len(matches),
            "totalSourceLocations": sum(
                match["totalCount"] for match in matches
            ),
            "matches": matches,
        }

    @staticmethod
    def _summary_response(
        application_id: str,
        threshold: int,
        entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "applicationId": application_id,
            "thresholdPercentage": threshold,
            "similarityMetric": "cosine",
            "queryEmbeddingSource": "semanticCatalog",
            "totalUniqueEntities": len(entities),
            "entities": entities,
        }

    def _application_entities(
        self,
        application_id: str,
    ) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        after_key = None

        while True:
            composite: dict[str, Any] = {
                "size": ENTITY_PAGE_SIZE,
                "sources": [
                    {"entityId": {"terms": {"field": "entityId"}}}
                ],
            }
            if after_key:
                composite["after"] = after_key
            response = self.store.search_occurrences(
                {
                    "size": 0,
                    "track_total_hits": False,
                    "query": {"term": {"applicationId": application_id}},
                    "aggs": {
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
                    },
                }
            )
            result = response["aggregations"]["entities"]
            for bucket in result["buckets"]:
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

            after_key = result.get("after_key")
            if not after_key or len(result["buckets"]) < ENTITY_PAGE_SIZE:
                break

        entities.sort(
            key=lambda item: (
                -item["countInCurrentCase"],
                item["normalizedText"],
                item["entityId"],
            )
        )
        return entities

    def _stored_vector_matches(
        self,
        source_by_key: dict[str, str],
        vectors: dict[str, list[float]],
        threshold: int,
    ) -> dict[str, list[dict[str, Any]]]:
        matches = {key: [] for key in source_by_key}
        pending = [
            (key, text, vectors[key], None)
            for key, text in source_by_key.items()
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
                    )
                    for source_key, _, vector, after_key in batch
                ]
                responses = self.store.multi_search_catalog(bodies)
                if len(responses) != len(batch):
                    raise RuntimeError(
                        "OpenSearch returned an invalid msearch response"
                    )
                for state, response in zip(batch, responses):
                    source_key, text, vector, _ = state
                    result = self._catalog_result(response)
                    matches[source_key].extend(
                        _catalog_candidate(
                            source_key,
                            hit["_source"],
                            hit["_score"],
                            threshold,
                        )
                        for hit in self._catalog_hits(result)
                    )
                    after_key = result.get("after_key")
                    if after_key and len(result["buckets"]) == CATALOG_PAGE_SIZE:
                        next_pending.append(
                            (source_key, text, vector, after_key)
                        )
            pending = next_pending

        for source_matches in matches.values():
            source_matches.sort(key=_match_sort_key)
        return matches

    def _single_stored_vector_matches(
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
            ),
        )

    def _catalog_text_matches(
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
    ) -> dict[str, Any]:
        return _catalog_search_body(
            source_key,
            {
                "knn": {
                    CATALOG_VECTOR_FIELD: {
                        "vector": vector,
                        "min_score": _minimum_opensearch_score(threshold),
                    }
                }
            },
            after_key,
        )

    @staticmethod
    def _catalog_text_body(
        text: str,
        source_key: str,
        threshold: int,
        after_key: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _catalog_search_body(
            source_key,
            {
                "neural": {
                    SEMANTIC_FIELD: {
                        "query_text": text,
                        "min_score": _minimum_opensearch_score(threshold),
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

    def _matching_occurrences(
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
) -> dict[str, Any]:
    candidate_key = candidate["semanticKey"]
    if source_key == candidate_key:
        percentage = 100.0
        match_type = "exact"
    else:
        percentage = _cosine_percentage(opensearch_score)
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


def _minimum_opensearch_score(threshold: int) -> float:
    return (1.0 + (threshold / 100.0)) / 2.0


def _cosine_percentage(opensearch_score: float) -> float:
    cosine_similarity = max(-1.0, min(1.0, (2.0 * opensearch_score) - 1.0))
    return round(max(0.0, cosine_similarity) * 100.0, 2)


def _match_sort_key(match: dict[str, Any]):
    return (-match["matchPercentage"], match["semanticKey"])
