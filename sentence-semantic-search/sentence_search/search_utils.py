import base64
import binascii
import json
from math import sqrt


def minimum_opensearch_score(threshold: int) -> float:
    """Convert a cosine percentage into a Faiss OpenSearch score.

    Input:
        threshold=90
    Output:
        0.9090909
    """

    if not 1 <= threshold <= 100:
        raise ValueError("threshold must be between 1 and 100")
    cosine_threshold = threshold / 100.0
    # Faiss returns 1 / (1 + distance), where cosine distance is 1 - cosine.
    return 1.0 / (2.0 - cosine_threshold)


def cosine_percentage(opensearch_score: float) -> float:
    """Convert a Faiss OpenSearch cosine score into a percentage.

    Input:
        opensearch_score=0.9090909
    Output:
        90.0
    """

    if opensearch_score <= 0:
        raise ValueError("opensearch_score must be greater than zero")
    cosine_similarity = 2.0 - (1.0 / opensearch_score)
    cosine_similarity = max(-1.0, min(1.0, cosine_similarity))
    return round(max(0.0, cosine_similarity) * 100.0, 2)


def vector_cosine_percentage(source_vector: list, matching_vector: list) -> float:
    """Calculate a percentage directly from two saved vectors.

    Input: source_vector=[1.0, 0.0], matching_vector=[0.9, 0.436].
    Output: approximately 90.0.
    """

    if not source_vector or len(source_vector) != len(matching_vector):
        raise ValueError("Catalog vectors must have the same non-zero size")
    dot_product = 0.0
    source_length = 0.0
    matching_length = 0.0
    for source_value, matching_value in zip(
        source_vector,
        matching_vector,
        strict=True,
    ):
        dot_product += source_value * matching_value
        source_length += source_value * source_value
        matching_length += matching_value * matching_value
    if source_length == 0 or matching_length == 0:
        raise ValueError("Catalog vectors cannot be empty vectors")
    similarity = dot_product / sqrt(source_length * matching_length)
    similarity = max(-1.0, min(1.0, similarity))
    return round(max(0.0, similarity) * 100.0, 2)


def encode_page_token(state: dict) -> str:
    """Put the OpenSearch cursor and first-page totals into one token."""

    payload = json.dumps(state, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_page_token(token: str) -> dict:
    """Read a pagination token and reject broken values."""

    try:
        payload = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        state = json.loads(payload)
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("Invalid nextToken") from error
    if not isinstance(state, dict) or not state.get("afterGlobalId"):
        raise ValueError("Invalid nextToken")
    return state
