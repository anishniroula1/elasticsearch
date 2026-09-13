"""AWS OpenSearch storage for semantic catalog and entity occurrences."""

import logging
import threading
import time
from typing import Any

from opensearchpy import (
    NotFoundError,
    TransportError,
    helpers,
)
from opensearchpy.helpers.errors import BulkIndexError

from semantic_search.config import Config
from semantic_search.open_search_client import OpenSearchClient

SEMANTIC_FIELD = "entitySearchText"
CATALOG_VECTOR_FIELD = "entitySearchTextVector"
CATALOG_VECTOR_DIMENSION = 1024
CATALOG_VECTOR_SPACE_TYPE = "cosinesimil"
CATALOG_MAX_ATTEMPTS = 10
CATALOG_RETRY_DELAY_SECONDS = 5
RETRYABLE_CATALOG_STATUSES = {408, 429, 500, 502, 503, 504}
CATALOG_EXISTENCE_BATCH_SIZE = 1_000

logger = logging.getLogger(__name__)


def occurrence_index_definition(config: Config) -> dict[str, Any]:
    """Original occurrence fields without duplicated vector storage."""

    return {
        "settings": {
            "number_of_shards": config.index_shards,
            "number_of_replicas": config.index_replicas,
            "analysis": {
                "normalizer": {
                    "lowercase_ascii": {
                        "type": "custom",
                        "filter": ["lowercase", "asciifolding"],
                    }
                }
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "sentenceEntityId": {"type": "long"},
                "applicationId": {"type": "keyword"},
                "tspId": {"type": "keyword"},
                "globalId": {"type": "keyword"},
                "entityId": {"type": "keyword"},
                "semanticKey": {"type": "keyword"},
                "rawEntity": {"type": "text"},
                "normalizedText": {
                    "type": "keyword",
                    "normalizer": "lowercase_ascii",
                },
                "entitySearchText": {"type": "text"},
                "entityType": {"type": "keyword"},
                "possibleSanction": {"type": "boolean"},
                "beginOffset": {"type": "integer"},
                "endOffset": {"type": "integer"},
                "score": {"type": "float"},
                "source": {"type": "keyword"},
                "documentType": {"type": "keyword"},
                "createdAt": {"type": "date"},
                "updatedAt": {"type": "date"},
            },
        },
    }


def catalog_index_definition(config: Config) -> dict[str, Any]:
    """One pipeline-embedded vector document per normalized entity text."""

    if not config.semantic_model_id:
        raise ValueError(
            "OPENSEARCH_SEMANTIC_MODEL_ID is required. Use the model ID "
            "registered/deployed in OpenSearch, not the Bedrock foundation "
            "model ID."
        )
    if not config.ingest_pipeline:
        raise ValueError("OPENSEARCH_INGEST_PIPELINE is required")
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
                "semanticKey": {"type": "keyword"},
                "normalizedText": {"type": "keyword"},
                SEMANTIC_FIELD: {"type": "text"},
                CATALOG_VECTOR_FIELD: {
                    "type": "knn_vector",
                    "dimension": CATALOG_VECTOR_DIMENSION,
                    "method": {
                        "name": "hnsw",
                        "space_type": CATALOG_VECTOR_SPACE_TYPE,
                        "engine": "lucene",
                    },
                },
            },
        },
    }


class OpenSearchStore:
    def __init__(self, config: Config, client=None):
        self.config = config
        self.client = client or OpenSearchClient(config).create_client()
        self.vector_space_type: str | None = None
        self._catalog_backoff_lock = threading.Lock()
        self._catalog_cooldown_until = 0.0

    def wait_until_ready(
        self,
        max_attempts: int = 60,
        delay_seconds: int = 2,
    ) -> None:
        for _ in range(max_attempts):
            if self.client.ping():
                return
            time.sleep(delay_seconds)
        raise RuntimeError("OpenSearch did not become ready")

    def ensure_indices(self) -> None:
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
        self._validate_catalog_mapping()

    def _ensure_index(
        self,
        physical_index: str,
        alias: str,
        definition: dict[str, Any],
    ) -> None:
        if not self.client.indices.exists(index=physical_index):
            body = {**definition, "aliases": {alias: {"is_write_index": True}}}
            self.client.indices.create(index=physical_index, body=body)
        if not self.client.indices.exists_alias(name=alias):
            self.client.indices.update_aliases(
                body={
                    "actions": [
                        {
                            "add": {
                                "index": physical_index,
                                "alias": alias,
                                "is_write_index": True,
                            }
                        }
                    ]
                }
            )

    def _validate_catalog_mapping(self) -> None:
        mapping = self.client.indices.get_mapping(index=self.config.catalog_index)
        properties = mapping[self.config.catalog_index]["mappings"]["properties"]
        text_field = properties.get(SEMANTIC_FIELD, {})
        if text_field.get("type") != "text":
            raise RuntimeError(
                "Existing catalog entitySearchText is not a text field. "
                "Run `make reset`."
            )
        vector_field = properties.get(CATALOG_VECTOR_FIELD, {})
        if vector_field.get("type") != "knn_vector":
            raise RuntimeError(
                f"Existing catalog {CATALOG_VECTOR_FIELD} is not a "
                "knn_vector. Run `make reset`."
            )
        dimension = vector_field.get("dimension")
        if dimension != CATALOG_VECTOR_DIMENSION:
            raise RuntimeError(
                f"Existing catalog vector dimension is {dimension}; expected "
                f"{CATALOG_VECTOR_DIMENSION}. Run `make reset`."
            )

        settings = self.client.indices.get_settings(
            index=self.config.catalog_index,
            params={"flat_settings": "true"},
        )[self.config.catalog_index]["settings"]
        pipeline = settings.get("index.default_pipeline")
        if pipeline != self.config.ingest_pipeline:
            raise RuntimeError(
                "Existing catalog default pipeline does not match "
                f"OPENSEARCH_INGEST_PIPELINE ({pipeline} != "
                f"{self.config.ingest_pipeline}). Run `make reset`."
            )
        space_type = (
            vector_field.get("space_type")
            or vector_field.get(
                "method",
                {},
            ).get("space_type")
        )
        if space_type != CATALOG_VECTOR_SPACE_TYPE:
            raise RuntimeError(
                "Catalog vector space_type must be cosinesimil; found "
                f"{space_type or 'no space_type'}. Run `make reset`."
            )
        self.vector_space_type = CATALOG_VECTOR_SPACE_TYPE

    def recreate_indices(self) -> None:
        catalog_index_definition(self.config)
        for index in (
            self.config.catalog_index,
            self.config.occurrence_index,
        ):
            if self.client.indices.exists(index=index):
                self.client.indices.delete(index=index)
        self.ensure_indices()

    def bulk_index_occurrences(self, sources: list[dict[str, Any]]) -> None:
        actions = [
            {
                "_op_type": "index",
                "_index": self.config.occurrence_alias,
                "_id": str(source["sentenceEntityId"]),
                "_source": source,
            }
            for source in sources
        ]
        self._bulk(actions)

    def bulk_index_catalog(self, sources: list[dict[str, Any]]) -> None:
        actions = [
            {
                "_op_type": "index",
                "_index": self.config.catalog_alias,
                "_id": source["semanticKey"],
                "_source": source,
            }
            for source in sources
        ]
        self._bulk_catalog_with_retry(actions)

    def _bulk_catalog_with_retry(
        self,
        actions: list[dict[str, Any]],
    ) -> None:
        if not actions:
            return

        pending_actions = actions
        for attempt in range(1, CATALOG_MAX_ATTEMPTS + 1):
            # If another catalog worker is backing off, do not send another
            # request through the Lasso proxy until its delay has completed.
            self._wait_for_catalog_cooldown()

            try:
                self._bulk(pending_actions)
                return
            except (BulkIndexError, TransportError) as error:
                retry_actions = self._retryable_catalog_actions(
                    error,
                    pending_actions,
                )
                if retry_actions is None or attempt == CATALOG_MAX_ATTEMPTS:
                    raise
                pending_actions = retry_actions
                logger.warning(
                    "Catalog batch attempt %s/%s failed; retrying %s "
                    "document(s) after %s seconds",
                    attempt,
                    CATALOG_MAX_ATTEMPTS,
                    len(pending_actions),
                    CATALOG_RETRY_DELAY_SECONDS,
                )
                self._start_catalog_cooldown()

    def _start_catalog_cooldown(self) -> None:
        with self._catalog_backoff_lock:
            self._catalog_cooldown_until = max(
                self._catalog_cooldown_until,
                time.monotonic() + CATALOG_RETRY_DELAY_SECONDS,
            )
        self._wait_for_catalog_cooldown()

    def _wait_for_catalog_cooldown(self) -> None:
        while True:
            with self._catalog_backoff_lock:
                remaining = self._catalog_cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(remaining)

    @staticmethod
    def _retryable_catalog_actions(
        error: BulkIndexError | TransportError,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        if isinstance(error, TransportError):
            status = error.status_code
            if status == "N/A":
                return actions
            try:
                status = int(status)
            except (TypeError, ValueError):
                return None
            if status in RETRYABLE_CATALOG_STATUSES:
                return actions
            return None

        failed_ids = set()
        for item in error.errors:
            details = next(iter(item.values()), {})
            status = details.get("status")
            try:
                status = int(status)
            except (TypeError, ValueError):
                return None
            failed_id = details.get("_id")
            if status not in RETRYABLE_CATALOG_STATUSES or failed_id is None:
                return None
            failed_ids.add(str(failed_id))

        if not failed_ids:
            return None
        retry_actions = [
            action for action in actions if str(action.get("_id")) in failed_ids
        ]
        retry_ids = {str(action.get("_id")) for action in retry_actions}
        if retry_ids != failed_ids:
            return None
        return retry_actions

    def _bulk(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        helpers.bulk(
            self.client,
            actions,
            chunk_size=self.config.seed_batch_size,
            request_timeout=120,
        )

    def refresh_indices(self) -> None:
        """Make the completed seed immediately visible to searches and counts."""

        self.client.indices.refresh(
            index=(f"{self.config.occurrence_alias},{self.config.catalog_alias}")
        )

    def search_occurrences(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.client.search(
            index=self.config.occurrence_alias,
            body=body,
        )

    def search_catalog(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.client.search(index=self.config.catalog_alias, body=body)

    def multi_search_catalog(
        self,
        bodies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        request: list[dict[str, Any]] = []
        for body in bodies:
            request.extend(({"index": self.config.catalog_alias}, body))
        response = self.client.msearch(
            body=request,
            request_timeout=120,
        )
        return response["responses"]

    def existing_catalog_keys(self, semantic_keys: list[str]) -> set[str]:
        """Return catalog IDs that already exist without loading vectors."""

        existing = set()
        for offset in range(0, len(semantic_keys), CATALOG_EXISTENCE_BATCH_SIZE):
            key_batch = semantic_keys[
                offset : offset + CATALOG_EXISTENCE_BATCH_SIZE
            ]
            response = self.client.mget(
                index=self.config.catalog_alias,
                body={"ids": key_batch},
                params={"_source": "false"},
            )
            existing.update(
                str(document["_id"])
                for document in response["docs"]
                if document.get("found")
            )
        return existing

    def catalog_vectors(
        self,
        semantic_keys: list[str],
    ) -> dict[str, list[float]]:
        if not semantic_keys:
            return {}
        response = self.client.mget(
            index=self.config.catalog_alias,
            body={"ids": semantic_keys},
            params={
                "_source_includes": (
                    f"semanticKey,{CATALOG_VECTOR_FIELD}"
                )
            },
        )
        vectors: dict[str, list[float]] = {}
        for document in response["docs"]:
            if not document.get("found"):
                continue
            source = document["_source"]
            vector = self._catalog_vector_from_source(source)
            if vector:
                vectors[source["semanticKey"]] = vector
        missing = sorted(set(semantic_keys) - set(vectors))
        if missing:
            raise RuntimeError(
                "Semantic catalog vectors are missing for keys: "
                + ", ".join(missing[:5])
            )
        return vectors

    def catalog_vector(self, semantic_key: str) -> list[float] | None:
        try:
            result = self.client.get(
                index=self.config.catalog_alias,
                id=semantic_key,
                _source_includes=["semanticKey", CATALOG_VECTOR_FIELD],
            )
        except NotFoundError:
            return None
        return self._catalog_vector_from_source(result["_source"])

    @staticmethod
    def _catalog_vector_from_source(
        source: dict[str, Any],
    ) -> list[float] | None:
        return source.get(CATALOG_VECTOR_FIELD)

    def stats(self) -> dict[str, Any]:
        return {
            "occurrenceDocuments": self._document_count(self.config.occurrence_alias),
            "semanticCatalogDocuments": self._document_count(self.config.catalog_alias),
            "clusterHealth": self.client.cluster.health()["status"],
        }

    def _document_count(self, index: str) -> int:
        try:
            return self.client.count(index=index)["count"]
        except NotFoundError:
            return 0

    def delete_indices_and_aliases(self) -> dict[str, list[str]]:
        """Delete both configured aliases and physical indexes."""

        aliases = (
            self.config.occurrence_alias,
            self.config.catalog_alias,
        )
        indices = (
            self.config.occurrence_index,
            self.config.catalog_index,
        )
        deleted_aliases = []
        missing_aliases = []
        for alias in aliases:
            if not self.client.indices.exists_alias(name=alias):
                missing_aliases.append(alias)
                continue
            try:
                alias_targets = self.client.indices.get_alias(name=alias)
            except NotFoundError:
                missing_aliases.append(alias)
                continue
            for target_index in alias_targets:
                self.client.indices.delete_alias(
                    index=target_index,
                    name=alias,
                )
            deleted_aliases.append(alias)

        deleted_indices = []
        missing_indices = []
        for index in indices:
            if self.client.indices.exists(index=index):
                self.client.indices.delete(index=index)
                deleted_indices.append(index)
            else:
                missing_indices.append(index)

        self.vector_space_type = None
        return {
            "deletedIndices": deleted_indices,
            "missingIndices": missing_indices,
            "deletedAliases": deleted_aliases,
            "missingAliases": missing_aliases,
        }

    def preview_documents(
        self,
        index: str,
        size: int = 10,
    ) -> dict[str, Any]:
        """Return unfiltered documents and the exact index count."""

        response = self.client.search(
            index=index,
            body={
                "size": size,
                "track_total_hits": True,
                "query": {"match_all": {}},
            },
        )
        hits = response["hits"]
        total = hits["total"]
        if isinstance(total, dict):
            total = total["value"]
        documents = hits["hits"]
        return {
            "totalDocuments": total,
            "returnedDocuments": len(documents),
            "documents": documents,
        }
