import base64
import binascii
import struct
import time

from opensearchpy import NotFoundError, RequestError, TransportError, helpers
from opensearchpy.helpers.errors import BulkIndexError

from sentence_search.config import Config
from sentence_search.open_search_client import OpenSearchClient
from sentence_search.search_utils import (
    cosine_percentage,
    minimum_opensearch_score,
)

SEMANTIC_TEXT_FIELD = "sentenceContent"
VECTOR_FIELD = "sentenceContentVector"
CATALOG_PAGE_SIZE = 1_000  # Catalog matches read from one aggregation page.
OCCURRENCE_PAGE_SIZE = 5_000  # Occurrence candidates read per search page.
TERMS_BATCH_SIZE = 10_000  # Sentence keys sent in one terms filter.
CATALOG_RETRY_ATTEMPTS = 10  # Remote embedding failures allowed per batch.
CATALOG_RETRY_SECONDS = 5  # Pause before retrying a failed Titan batch.
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def occurrence_index_definition(config: Config) -> dict:
    """Make the mapping for every sentence occurrence."""

    return {
        "settings": {
            "number_of_shards": config.index_shards,
            "number_of_replicas": config.index_replicas,
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "applicationId": {"type": "keyword"},
                "tspId": {"type": "keyword"},
                "documentId": {"type": "keyword"},
                "sectionName": {"type": "keyword"},
                "globalId": {"type": "keyword"},
                "sentIdLocal": {"type": "long"},
                "sentenceContent": {"type": "text"},
                "isTracer": {"type": "boolean"},
                "isFormLanguage": {"type": "boolean"},
                "sentenceKey": {"type": "keyword"},
                "sourceType": {"type": "keyword"},
                "createdAt": {"type": "date"},
                "updatedAt": {"type": "date"},
                "analysisGroup": {"type": "keyword"},
            },
        },
    }


def catalog_index_definition(config: Config) -> dict:
    """Make the Titan-backed vector catalog mapping."""

    return {
        "settings": {
            "index.knn": True,
            "index.default_pipeline": config.ingest_pipeline,
            "number_of_shards": config.index_shards,
            "number_of_replicas": config.index_replicas,
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "sentenceContent": {"type": "text"},
                "sentenceContentVector": {
                    "type": "knn_vector",
                    "dimension": config.vector_dimension,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "faiss",
                        "parameters": {
                            "ef_construction": 100,
                            "m": 16,
                        },
                    },
                },
                "sentenceKey": {"type": "keyword"},
                "createdAt": {"type": "date"},
                "updatedAt": {"type": "date"},
            },
        },
    }


class OpenSearchStore:
    def __init__(self, config: Config, client=None):
        """Create the store with an injected client or an AWS client."""

        self.config = config
        self.client = client or OpenSearchClient(config).create_client()

    def wait_until_ready(self, attempts: int = 60):
        """Wait for OpenSearch during application startup."""

        for _ in range(attempts):
            if self.client.ping():
                return
            time.sleep(2)
        raise RuntimeError("OpenSearch did not become ready")

    def ensure_indices(self):
        """Create both physical indexes and aliases when missing."""

        self._ensure_index(
            self.config.occurrence_index,
            self.config.occurrence_alias,
            occurrence_index_definition(self.config),
        )
        self._ensure_index(
            self.config.catalog_index,
            self.config.catalog_alias,
            catalog_index_definition(self.config),
        )

    def _ensure_index(self, index: str, alias: str, definition: dict):
        """Create one physical index and point its write alias at it."""

        if not self.client.indices.exists(index=index):
            try:
                self.client.indices.create(index=index, body=definition)
            except RequestError:
                # The API and worker can start together. Continue only when
                # the other process successfully created this same index.
                if not self.client.indices.exists(index=index):
                    raise
        if not self.client.indices.exists_alias(name=alias):
            self.client.indices.put_alias(
                index=index,
                name=alias,
                body={"is_write_index": True},
            )

    def recreate_indices(self):
        """Delete and recreate both OpenSearch indexes."""

        self.delete_indices()
        self.ensure_indices()

    def delete_indices(self) -> dict:
        """Delete the two physical indexes and their attached aliases."""

        deleted = []
        for index in (
            self.config.occurrence_index,
            self.config.catalog_index,
        ):
            if self.client.indices.exists(index=index):
                self.client.indices.delete(index=index)
                deleted.append(index)
        return {"deletedIndexes": deleted}

    def existing_catalog_keys(self, keys: list) -> set:
        """Return catalog document IDs that already exist."""

        existing = set()
        for offset in range(0, len(keys), 1_000):
            batch = keys[offset : offset + 1_000]
            response = self.client.mget(
                index=self.config.catalog_alias,
                body={"ids": batch},
                params={"_source": "false"},
            )
            for document in response["docs"]:
                if document.get("found"):
                    existing.add(str(document["_id"]))
        return existing

    def validate_occurrence_identity(self, records: list):
        """Stop a global ID from being reused for different sentence data."""

        incoming = {}
        identity_fields = (
            "applicationId",
            "tspId",
            "sectionName",
            "analysisGroup",
            "sentenceKey",
            "isTracer",
            "isFormLanguage",
        )
        for record in records:
            global_id = record["globalId"]
            identity = {field: record[field] for field in identity_fields}
            previous = incoming.get(global_id)
            if previous and previous != identity:
                raise ValueError(
                    f"The same globalId has different sentence data: {global_id}"
                )
            incoming[global_id] = identity

        global_ids = list(incoming)
        for offset in range(0, len(global_ids), 1_000):
            batch = global_ids[offset : offset + 1_000]
            response = self.client.mget(
                index=self.config.occurrence_alias,
                body={"ids": batch},
            )
            for document in response["docs"]:
                if not document.get("found"):
                    continue
                global_id = str(document["_id"])
                existing = document["_source"]
                if any(
                    existing[field] != incoming[global_id][field]
                    for field in identity_fields
                ):
                    raise ValueError(
                        "globalId cannot change sentence or matching metadata "
                        f"without reset: {global_id}"
                    )

    def bulk_index_catalog(self, documents: list):
        """Index new catalog texts and retry temporary embedding failures."""

        if not documents:
            return 0
        actions = []
        for document in documents:
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self.config.catalog_alias,
                    "_id": document["sentenceKey"],
                    "_source": document,
                }
            )
        self._bulk_with_retry(actions, "catalog")
        return len(actions)

    def bulk_index_occurrences(self, documents: list):
        """Index sentence occurrences by globally unique sentence ID."""

        if not documents:
            return 0
        actions = []
        for document in documents:
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self.config.occurrence_alias,
                    "_id": document["globalId"],
                    "_source": document,
                }
            )
        helpers.bulk(
            self.client,
            actions,
            chunk_size=self.config.seed_batch_size,
            raise_on_error=True,
        )
        return len(actions)

    def _bulk_with_retry(self, actions: list, label: str):
        """Retry a complete idempotent bulk batch after temporary failures."""

        last_error = None
        for attempt in range(1, CATALOG_RETRY_ATTEMPTS + 1):
            try:
                helpers.bulk(
                    self.client,
                    actions,
                    chunk_size=self.config.seed_batch_size,
                    raise_on_error=True,
                )
                return
            except BulkIndexError as error:
                last_error = error
                statuses = self._bulk_error_statuses(error)
                if not statuses or not statuses.issubset(RETRYABLE_STATUSES):
                    raise RuntimeError(
                        f"Non-retryable OpenSearch {label} bulk failure: {error}"
                    ) from error
            except TransportError as error:
                last_error = error
                status = getattr(error, "status_code", None)
                if status not in RETRYABLE_STATUSES:
                    raise

            if attempt == CATALOG_RETRY_ATTEMPTS:
                break
            time.sleep(CATALOG_RETRY_SECONDS)

        raise RuntimeError(
            f"OpenSearch {label} bulk failed after "
            f"{CATALOG_RETRY_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _bulk_error_statuses(error: BulkIndexError) -> set:
        """Read HTTP statuses from a BulkIndexError."""

        statuses = set()
        for item in error.errors:
            operation = next(iter(item.values()))
            status = operation.get("status")
            if status is not None:
                statuses.add(int(status))
        return statuses

    def refresh_indices(self):
        """Make newly indexed catalog and occurrence records searchable."""

        self.client.indices.refresh(
            index=(f"{self.config.catalog_alias},{self.config.occurrence_alias}")
        )

    def catalog_vector(self, key: str) -> list:
        """Read one saved vector using fast binary doc values."""

        response = self.client.search(
            index=self.config.catalog_alias,
            body={
                "size": 1,
                "track_total_hits": False,
                "_source": False,
                "stored_fields": "_none_",
                "docvalue_fields": [
                    {
                        "field": VECTOR_FIELD,
                        "format": "binary",
                    }
                ],
                "query": {"ids": {"values": [key]}},
            },
        )
        hits = response["hits"]["hits"]
        if not hits:
            raise RuntimeError(f"Catalog vector does not exist for {key}")
        values = hits[0].get("fields", {}).get(VECTOR_FIELD)
        if not values:
            raise RuntimeError(f"Catalog vector is missing for {key}")
        return self._decode_binary_vector(values[0])

    def _decode_binary_vector(self, encoded_vector: str) -> list:
        """Convert an OpenSearch binary vector into float values."""

        try:
            vector_bytes = base64.b64decode(
                encoded_vector,
                validate=True,
            )
        except (binascii.Error, ValueError) as error:
            raise RuntimeError("OpenSearch returned an invalid vector") from error

        expected_bytes = self.config.vector_dimension * 4
        if len(vector_bytes) != expected_bytes:
            raise RuntimeError(
                "OpenSearch returned a vector with "
                f"{len(vector_bytes)} bytes; expected {expected_bytes}"
            )
        return list(
            struct.unpack(
                f"<{self.config.vector_dimension}f",
                vector_bytes,
            )
        )

    def catalog_matches(
        self,
        source_key: str,
        vector: list,
        threshold: int,
    ) -> list:
        """Return every unique catalog key meeting the cosine threshold."""

        matches = []
        after_key = None
        while True:
            composite = {
                "size": CATALOG_PAGE_SIZE,
                "sources": [{"sentenceKey": {"terms": {"field": "sentenceKey"}}}],
            }
            if after_key:
                composite["after"] = after_key
            response = self.client.search(
                index=self.config.catalog_alias,
                body={
                    "size": 0,
                    "track_total_hits": False,
                    "query": {
                        "bool": {
                            "should": [
                                {"term": {"sentenceKey": source_key}},
                                {
                                    "knn": {
                                        VECTOR_FIELD: {
                                            "vector": vector,
                                            "min_score": minimum_opensearch_score(
                                                threshold
                                            ),
                                        }
                                    }
                                },
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
                                        "_source": [
                                            "sentenceKey",
                                            "sentenceContent",
                                        ],
                                    }
                                }
                            },
                        }
                    },
                },
            )
            result = response["aggregations"]["matches"]
            for bucket in result["buckets"]:
                hit = bucket["sample"]["hits"]["hits"][0]
                candidate = hit["_source"]
                candidate_key = candidate["sentenceKey"]
                if candidate_key == source_key:
                    percentage = 100.0
                    match_type = "exact"
                else:
                    percentage = cosine_percentage(float(hit["_score"]))
                    match_type = "semantic"
                if percentage >= threshold:
                    matches.append(
                        {
                            "sentenceKey": candidate_key,
                            "sentenceContent": candidate["sentenceContent"],
                            "similarityPercentage": percentage,
                            "matchType": match_type,
                        }
                    )

            after_key = result.get("after_key")
            if not after_key or not result["buckets"]:
                break
        return matches

    def occurrence(self, global_id: str) -> dict | None:
        """Return one occurrence document by its global ID."""

        try:
            response = self.client.get(
                index=self.config.occurrence_alias,
                id=global_id,
            )
        except NotFoundError:
            return None
        return response["_source"]

    def matching_occurrences(self, source: dict, catalog_matches: list) -> list:
        """Find eligible occurrence IDs for the catalog keys and analysis group."""

        candidate_keys = sorted({match["sentenceKey"] for match in catalog_matches})
        targets = []
        for offset in range(0, len(candidate_keys), TERMS_BATCH_SIZE):
            key_batch = candidate_keys[offset : offset + TERMS_BATCH_SIZE]
            search_after = None
            while True:
                must_not = [{"ids": {"values": [source["globalId"]]}}]
                if self.config.match_across_applications_only:
                    must_not.append(
                        {"term": {"applicationId": source["applicationId"]}}
                    )
                body = {
                    "size": OCCURRENCE_PAGE_SIZE,
                    "track_total_hits": False,
                    "_source": [
                        "globalId",
                        "tspId",
                        "applicationId",
                        "sectionName",
                        "analysisGroup",
                    ],
                    "sort": [{"globalId": "asc"}],
                    "query": {
                        "bool": {
                            "filter": [
                                {"terms": {"sentenceKey": key_batch}},
                                {"term": {"analysisGroup": source["analysisGroup"]}},
                                {"term": {"isTracer": False}},
                                {"term": {"isFormLanguage": False}},
                            ],
                            "must_not": must_not,
                        }
                    },
                }
                if search_after:
                    body["search_after"] = search_after
                response = self.client.search(
                    index=self.config.occurrence_alias,
                    body=body,
                )
                hits = response["hits"]["hits"]
                for hit in hits:
                    targets.append(hit["_source"])
                if len(hits) < OCCURRENCE_PAGE_SIZE:
                    break
                search_after = hits[-1]["sort"]
        return targets

    def stats(self) -> dict:
        """Return both OpenSearch document counts and cluster health."""

        return {
            "occurrenceDocuments": self._count(self.config.occurrence_alias),
            "catalogDocuments": self._count(self.config.catalog_alias),
            "clusterHealth": self.client.cluster.health()["status"],
        }

    def _count(self, index: str) -> int:
        """Count an index or return zero before it exists."""

        try:
            return int(self.client.count(index=index)["count"])
        except NotFoundError:
            return 0

    def preview(self, index: str, size: int) -> dict:
        """Return unfiltered documents without returning catalog vectors."""

        body = {"size": size, "query": {"match_all": {}}}
        if index == self.config.catalog_alias:
            body["_source"] = {"excludes": [VECTOR_FIELD]}
        response = self.client.search(index=index, body=body)
        return {
            "count": len(response["hits"]["hits"]),
            "documents": [
                {"id": hit["_id"], **hit["_source"]} for hit in response["hits"]["hits"]
            ],
        }

    def delete_occurrences(
        self,
        application_id: str | None = None,
        tsp_id: str | None = None,
    ) -> int:
        """Delete occurrence documents for one application or TSP document."""

        filters = []
        if application_id is not None:
            filters.append({"term": {"applicationId": application_id}})
        if tsp_id is not None:
            filters.append({"term": {"tspId": tsp_id}})
        if not filters:
            raise ValueError("applicationId or tspId is required for deletion")
        response = self.client.delete_by_query(
            index=self.config.occurrence_alias,
            body={"query": {"bool": {"filter": filters}}},
            params={
                "conflicts": "proceed",
                "refresh": "true",
                "wait_for_completion": "true",
            },
        )
        return int(response.get("deleted", 0))

    def deletion_catalog_keys(
        self,
        application_id: str | None = None,
        tsp_id: str | None = None,
    ) -> list:
        """Return unique sentence keys before deleting occurrence documents."""

        filters = []
        if application_id is not None:
            filters.append({"term": {"applicationId": application_id}})
        if tsp_id is not None:
            filters.append({"term": {"tspId": tsp_id}})
        if not filters:
            raise ValueError("applicationId or tspId is required for deletion")

        keys = []
        after_key = None
        while True:
            composite = {
                "size": CATALOG_PAGE_SIZE,
                "sources": [{"sentenceKey": {"terms": {"field": "sentenceKey"}}}],
            }
            if after_key:
                composite["after"] = after_key
            response = self.client.search(
                index=self.config.occurrence_alias,
                body={
                    "size": 0,
                    "track_total_hits": False,
                    "query": {"bool": {"filter": filters}},
                    "aggs": {"keys": {"composite": composite}},
                },
            )
            result = response["aggregations"]["keys"]
            keys.extend(bucket["key"]["sentenceKey"] for bucket in result["buckets"])
            after_key = result.get("after_key")
            if not after_key or not result["buckets"]:
                break
        return keys

    def delete_unused_catalog_keys(self, keys: list) -> int:
        """Delete catalog vectors only when no occurrence still uses them."""

        deleted = 0
        for offset in range(0, len(keys), 1_000):
            batch = keys[offset : offset + 1_000]
            response = self.client.search(
                index=self.config.occurrence_alias,
                body={
                    "size": 0,
                    "track_total_hits": False,
                    "query": {"terms": {"sentenceKey": batch}},
                    "aggs": {
                        "usedKeys": {
                            "terms": {
                                "field": "sentenceKey",
                                "size": len(batch),
                            }
                        }
                    },
                },
            )
            used_keys = {
                bucket["key"]
                for bucket in response["aggregations"]["usedKeys"]["buckets"]
            }
            unused_keys = [key for key in batch if key not in used_keys]
            if not unused_keys:
                continue
            delete_response = self.client.delete_by_query(
                index=self.config.catalog_alias,
                body={"query": {"ids": {"values": unused_keys}}},
                params={
                    "conflicts": "proceed",
                    "refresh": "true",
                    "wait_for_completion": "true",
                },
            )
            deleted += int(delete_response.get("deleted", 0))
        return deleted
