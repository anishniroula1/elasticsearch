import base64
import binascii


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


def encode_id_token(global_id: str) -> str:
    """Create a cursor for an application sentence page."""

    return base64.urlsafe_b64encode(global_id.encode("utf-8")).decode("ascii")


def decode_id_token(token: str) -> str:
    """Read an application sentence cursor."""

    try:
        value = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ValueError("Invalid nextToken") from error
    if not value:
        raise ValueError("Invalid nextToken")
    return value
