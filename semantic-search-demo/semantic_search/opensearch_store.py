"""AWS OpenSearch storage for semantic catalog and entity occurrences."""

import time
from typing import Any

from opensearchpy import (
    NotFoundError,
    helpers,
)

from semantic_search.config import Config
from semantic_search.open_search_client import OpenSearchClient

SEMANTIC_FIELD = "entitySearchText"
SEMANTIC_INFO_FIELD = f"{SEMANTIC_FIELD}_semantic_info"
CATALOG_VECTOR_FIELD = f"{SEMANTIC_INFO_FIELD}.embedding"


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
    """One semantic document per normalized entity search text."""

    if not config.semantic_model_id:
        raise ValueError(
            "OPENSEARCH_SEMANTIC_MODEL_ID is required. Use the model ID "
            "registered/deployed in OpenSearch, not the Bedrock foundation "
            "model ID."
        )
    return {
        "settings": {
            "index.knn": True,
            "number_of_shards": config.index_shards,
            "number_of_replicas": config.index_replicas,
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "semanticKey": {"type": "keyword"},
                "normalizedText": {"type": "keyword"},
                SEMANTIC_FIELD: {
                    "type": "semantic",
                    "model_id": config.semantic_model_id,
                },
            },
        },
    }


class OpenSearchStore:
    def __init__(self, config: Config, client=None):
        self.config = config
        self.client = client or OpenSearchClient(config).create_client()
        self.vector_space_type: str | None = None

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
        mapping = self.client.indices.get_mapping(
            index=self.config.catalog_index
        )
        properties = mapping[self.config.catalog_index]["mappings"][
            "properties"
        ]
        semantic_field = properties.get(SEMANTIC_FIELD, {})
        if semantic_field.get("type") != "semantic":
            raise RuntimeError(
                "Existing catalog entitySearchText is not a semantic field. "
                "Run `make reset`."
            )
        actual_model_id = semantic_field.get("model_id")
        if actual_model_id != self.config.semantic_model_id:
            raise RuntimeError(
                "Existing semantic model ID does not match "
                f"OPENSEARCH_SEMANTIC_MODEL_ID ({actual_model_id} != "
                f"{self.config.semantic_model_id}). Run `make reset`."
            )

        embedding = properties.get(SEMANTIC_INFO_FIELD, {}).get(
            "properties",
            {},
        ).get("embedding", {})
        space_type = embedding.get("space_type") or embedding.get(
            "method",
            {},
        ).get("space_type")
        if space_type not in {"cosinesimil", "l2"}:
            raise RuntimeError(
                "Semantic threshold percentages support registered model "
                "space_type values cosinesimil and l2; found "
                f"{space_type or 'no space_type'}."
            )
        self.vector_space_type = space_type

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
        self._bulk(actions)

    def _bulk(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        helpers.bulk(
            self.client,
            actions,
            chunk_size=self.config.seed_batch_size,
            request_timeout=120,
            refresh="wait_for",
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

    def catalog_vectors(
        self,
        semantic_keys: list[str],
    ) -> dict[str, list[float]]:
        if not semantic_keys:
            return {}
        response = self.client.mget(
            index=self.config.catalog_alias,
            body={"ids": semantic_keys},
            _source_includes=["semanticKey", CATALOG_VECTOR_FIELD],
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
        return source.get(SEMANTIC_INFO_FIELD, {}).get("embedding")

    def stats(self) -> dict[str, Any]:
        return {
            "occurrenceDocuments": self._document_count(
                self.config.occurrence_alias
            ),
            "semanticCatalogDocuments": self._document_count(
                self.config.catalog_alias
            ),
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
