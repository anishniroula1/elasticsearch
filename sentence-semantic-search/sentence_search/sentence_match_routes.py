from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from opensearchpy.exceptions import OpenSearchException

from sentence_search.components import (
    paginated_sentence_key_search_service,
    sentence_key_search_service,
)
from sentence_search.config import config

router = APIRouter()

SENTENCE_KEY_QUERY = Query(
    min_length=64,
    max_length=64,
    description="SHA-256 sentenceKey already saved during ingestion.",
)
THRESHOLD_QUERY = Query(
    ge=1,
    le=100,
    description="Minimum cosine percentage for this search.",
)


@router.get(
    "/applications/{applicationId}/sentences/semantic-search",
    tags=["Sentence matches"],
)
def sentence_key_semantic_search(
    applicationId: str,
    sentenceKey: Annotated[str, SENTENCE_KEY_QUERY],
    analysisGroup: Annotated[str, Query(min_length=1)] = "Asylee",
    threshold: Annotated[int, THRESHOLD_QUERY] = config.match_threshold,
):
    """Return every exact and similar sentence for one saved sentence key."""

    try:
        return sentence_key_search_service.search(
            applicationId,
            sentenceKey,
            analysisGroup,
            threshold,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get(
    "/applications/{applicationId}/sentences/semantic-search-paginated",
    tags=["Sentence matches"],
)
def paginated_sentence_key_semantic_search(
    applicationId: str,
    sentenceKey: Annotated[str, SENTENCE_KEY_QUERY],
    analysisGroup: Annotated[str, Query(min_length=1)] = "Asylee",
    threshold: Annotated[int, THRESHOLD_QUERY] = config.match_threshold,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 100,
    nextToken: str | None = None,
):
    """Return one match page and counts for only that page."""

    try:
        return paginated_sentence_key_search_service.search(
            applicationId,
            sentenceKey,
            analysisGroup,
            threshold,
            pageSize,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
