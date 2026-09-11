import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _aws_hostname(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(
            "OPENSEARCH_HOST must be an HTTPS Amazon OpenSearch hostname"
        )
    return parsed.hostname


def _integer_setting(
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < minimum or (maximum is not None and value > maximum):
        expected = f"at least {minimum}"
        if maximum is not None:
            expected = f"between {minimum} and {maximum}"
        raise ValueError(f"{name} must be {expected}")
    return value


@dataclass(frozen=True)
class Config:
    """Load all AWS OpenSearch and index settings from the environment."""

    opensearch_host: str
    opensearch_port: int
    opensearch_service: str
    aws_region: str
    iam_access_role: str
    semantic_model_id: str
    occurrence_index: str
    occurrence_alias: str
    catalog_index: str
    catalog_alias: str
    index_shards: int
    index_replicas: int
    seed_batch_size: int

    @classmethod
    @lru_cache
    def load(cls) -> "Config":
        config = cls(
            opensearch_host=_aws_hostname(
                os.getenv("OPENSEARCH_HOST", "").strip()
            ),
            opensearch_port=_integer_setting(
                "OPENSEARCH_PORT",
                443,
                1,
                65_535,
            ),
            opensearch_service=os.getenv(
                "OPENSEARCH_SERVICE",
                "es",
            ).strip(),
            aws_region=os.getenv("AWS_REGION", "us-east-1").strip(),
            iam_access_role=os.getenv("IAM_ACCESS_ROLE", "").strip(),
            semantic_model_id=os.getenv(
                "OPENSEARCH_SEMANTIC_MODEL_ID",
                "",
            ).strip(),
            occurrence_index=os.getenv(
                "OPENSEARCH_INDEX",
                "ner_entity_occurrences-v1",
            ).strip(),
            occurrence_alias=os.getenv(
                "OPENSEARCH_ALIAS",
                "ner_entity_occurrences",
            ).strip(),
            catalog_index=os.getenv(
                "OPENSEARCH_CATALOG_INDEX",
                "ner_entity_semantic_catalog-v1",
            ).strip(),
            catalog_alias=os.getenv(
                "OPENSEARCH_CATALOG_ALIAS",
                "ner_entity_semantic_catalog",
            ).strip(),
            index_shards=_integer_setting("INDEX_SHARDS", 1, 1),
            index_replicas=_integer_setting("INDEX_REPLICAS", 1, 0),
            seed_batch_size=_integer_setting("SEED_BATCH_SIZE", 100, 1),
        )
        config._validate()
        return config

    def _validate(self) -> None:
        if self.opensearch_service != "es":
            raise ValueError(
                "OPENSEARCH_SERVICE must be es for this managed-domain app"
            )
        required = {
            "AWS_REGION": self.aws_region,
            "OPENSEARCH_INDEX": self.occurrence_index,
            "OPENSEARCH_ALIAS": self.occurrence_alias,
            "OPENSEARCH_CATALOG_INDEX": self.catalog_index,
            "OPENSEARCH_CATALOG_ALIAS": self.catalog_alias,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "Environment settings cannot be empty: " + ", ".join(missing)
            )
        if self.occurrence_index == self.catalog_index:
            raise ValueError("Occurrence and catalog indexes must be different")
        if self.occurrence_alias == self.catalog_alias:
            raise ValueError("Occurrence and catalog aliases must be different")


config = Config.load()
