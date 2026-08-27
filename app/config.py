import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    """Keep all settings used by this app."""

    host: str
    port: int
    use_ssl: bool
    verify_certs: bool
    auth_mode: str
    username: str
    password: str
    region: str
    service_name: str
    iam_access_role: str
    physical_index: str
    index_alias: str
    auto_seed: bool
    seed_count: int

    @classmethod
    @lru_cache
    def get_config(cls):
        """Load the settings once and use them across the app."""

        raw_host = os.getenv("OPENSEARCH_HOST", "localhost").strip()
        parsed_host = urlparse(
            raw_host if "://" in raw_host else f"//{raw_host}"
        )
        if not parsed_host.hostname:
            raise ValueError("OPENSEARCH_HOST is invalid")

        host = parsed_host.hostname
        ssl_setting = os.getenv("OPENSEARCH_USE_SSL")
        use_ssl = (
            ssl_setting.lower() == "true"
            if ssl_setting
            else parsed_host.scheme == "https"
        )
        default_port = 443 if use_ssl else 9200
        host_port = parsed_host.port if parsed_host.port else default_port

        config = cls(
            host=host,
            port=int(
                os.getenv(
                    "OPENSEARCH_PORT",
                    str(host_port),
                )
            ),
            use_ssl=use_ssl,
            verify_certs=os.getenv("OPENSEARCH_VERIFY_CERTS", "true").lower()
            == "true",
            auth_mode=os.getenv("OPENSEARCH_AUTH_MODE", "none").lower(),
            username=os.getenv("OPENSEARCH_USERNAME", ""),
            password=os.getenv("OPENSEARCH_PASSWORD", ""),
            region=os.getenv("AWS_REGION", "us-east-1"),
            service_name=os.getenv("OPENSEARCH_SERVICE", "es"),
            iam_access_role=os.getenv("IAM_ACCESS_ROLE", ""),
            physical_index=os.getenv(
                "OPENSEARCH_INDEX",
                "ner_entity_occurrences-v1",
            ),
            index_alias=os.getenv(
                "OPENSEARCH_ALIAS",
                "ner_entity_occurrences",
            ),
            auto_seed=os.getenv("AUTO_SEED", "false").lower() == "true",
            seed_count=int(os.getenv("SEED_COUNT", "10000")),
        )
        config._validate()
        return config

    def _validate(self):
        """Check only the settings needed by the selected login mode."""

        if self.auth_mode not in {"none", "basic", "iam"}:
            raise ValueError("OPENSEARCH_AUTH_MODE must be none, basic, or iam")
        if self.auth_mode == "basic" and not (self.username and self.password):
            raise ValueError("OpenSearch username and password are required")
        if self.auth_mode == "iam" and not self.iam_access_role:
            raise ValueError("IAM_ACCESS_ROLE is required for IAM auth")
        if self.service_name not in {"es", "aoss"}:
            raise ValueError("OPENSEARCH_SERVICE must be es or aoss")


config = Config.get_config()
