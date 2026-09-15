from semantic_search.opensearch_store import OpenSearchStore

COSINE_SPACE_TYPE = "cosinesimil"


def vector_space_type(store: OpenSearchStore) -> str:
    """Get the cosine setting. Stop when the store is not ready."""

    space_type = store.vector_space_type
    if space_type != COSINE_SPACE_TYPE:
        raise RuntimeError("Semantic catalog vector space is not initialized")
    return space_type


def response_metadata(
    store: OpenSearchStore,
    application_id: str,
    threshold: int,
    query_embedding_source: str,
) -> dict:
    """Make the common fields used in semantic responses."""

    return {
        "applicationId": application_id,
        "thresholdPercentage": threshold,
        "similarityMetric": "cosine",
        "vectorSpaceType": vector_space_type(store),
        "queryEmbeddingSource": query_embedding_source,
    }


def minimum_opensearch_score(
    threshold: int,
    vector_space_type: str = COSINE_SPACE_TYPE,
) -> float:
    """Change the API percentage into the score OpenSearch needs.

    Input:
        threshold=90
    Output:
        0.95
    """

    if vector_space_type != COSINE_SPACE_TYPE:
        raise ValueError(f"Unsupported vector space: {vector_space_type}")
    cosine_threshold = threshold / 100.0
    return (1.0 + cosine_threshold) / 2.0


def cosine_percentage(
    opensearch_score: float,
    vector_space_type: str = COSINE_SPACE_TYPE,
) -> float:
    """Change an OpenSearch score into the percentage shown by the API.

    Input:
        opensearch_score=0.95
    Output:
        90.0
    """

    if vector_space_type != COSINE_SPACE_TYPE:
        raise ValueError(f"Unsupported vector space: {vector_space_type}")
    cosine_similarity = (2.0 * opensearch_score) - 1.0
    cosine_similarity = max(-1.0, min(1.0, cosine_similarity))
    return round(max(0.0, cosine_similarity) * 100.0, 2)
