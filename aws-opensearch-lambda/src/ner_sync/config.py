import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    """Keep the Lambda settings in one place."""

    host: str
    region: str
    index_name: str
    service_name: str
    iam_access_role: str

    @classmethod
    @lru_cache
    def get_config(cls):
        """Read and check settings when Lambda first needs them."""

        host = os.getenv("OPENSEARCH_HOST", "").strip()
        region = os.getenv("AWS_REGION", "").strip()
        index_name = os.getenv(
            "OPENSEARCH_INDEX",
            "ner_entity_occurrences",
        ).strip()
        service_name = os.getenv("OPENSEARCH_SERVICE", "es").strip()
        iam_access_role = os.getenv("IAM_ACCESS_ROLE", "").strip()

        if not host:
            raise ValueError("OPENSEARCH_HOST is required")
        if not region:
            raise ValueError("AWS_REGION is required")
        if not index_name:
            raise ValueError("OPENSEARCH_INDEX is required")
        if service_name not in {"es", "aoss"}:
            raise ValueError("OPENSEARCH_SERVICE must be es or aoss")
        if not iam_access_role:
            raise ValueError("IAM_ACCESS_ROLE is required")

        # OpenSearch client needs only the hostname, without https or a path.
        parsed_host = urlparse(
            host if "://" in host else f"https://{host}"
        )
        if not parsed_host.hostname:
            raise ValueError("OPENSEARCH_HOST is invalid")
        return cls(
            host=parsed_host.hostname,
            region=region,
            index_name=index_name,
            service_name=service_name,
            iam_access_role=iam_access_role,
        )
