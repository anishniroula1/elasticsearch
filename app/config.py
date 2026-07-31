import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Keep all settings used by this app."""

    elasticsearch_url: str = os.getenv(
        "ELASTICSEARCH_URL",
        "http://localhost:9200",
    )
    elasticsearch_username: str = os.getenv("ELASTICSEARCH_USERNAME")
    elasticsearch_password: str = os.getenv("ELASTICSEARCH_PASSWORD")
    physical_index: str = os.getenv(
        "ELASTICSEARCH_INDEX",
        "ner_entity_occurrences-v1",
    )
    index_alias: str = os.getenv(
        "ELASTICSEARCH_ALIAS",
        "ner_entity_occurrences",
    )
    auto_seed: bool = os.getenv("AUTO_SEED", "true").lower() == "true"
    seed_count: int = int(os.getenv("SEED_COUNT", "10000"))


config = Config()
