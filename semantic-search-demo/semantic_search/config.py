import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse


# This project targets an IAM-protected Amazon OpenSearch Service domain only.
# These values are intentionally fixed so there is one supported runtime path.
OPENSEARCH_PORT = 443
OPENSEARCH_SERVICE = "es"
OCCURRENCE_INDEX = "ner_entity_occurrences-v1"
OCCURRENCE_ALIAS = "ner_entity_occurrences"
CATALOG_INDEX = "ner_entity_semantic_catalog-v1"
CATALOG_ALIAS = "ner_entity_semantic_catalog"
INDEX_SHARDS = 1
INDEX_REPLICAS = 1
SEED_BATCH_SIZE = 100


def _aws_hostname(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(
            "OPENSEARCH_HOST must be an HTTPS Amazon OpenSearch hostname"
        )
    return parsed.hostname


@dataclass(frozen=True)
class Config:
    """Values that differ between AWS environments."""

    opensearch_host: str
    aws_region: str
    semantic_model_id: str

    @classmethod
    @lru_cache
    def load(cls) -> "Config":
        return cls(
            opensearch_host=_aws_hostname(
                os.getenv("OPENSEARCH_HOST", "").strip()
            ),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            semantic_model_id=os.getenv(
                "OPENSEARCH_SEMANTIC_MODEL_ID",
                "",
            ).strip(),
        )


config = Config.load()
