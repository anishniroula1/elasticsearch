from enum import StrEnum
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from opensearchpy.exceptions import OpenSearchException
from sqlalchemy.exc import SQLAlchemyError

from sentence_search.components import (
    deletion_service,
    opensearch_store,
    postgres_store,
    seed_service,
    sentence_key_search_service,
    sentence_service,
    sentence_summary_service,
)
from sentence_search.config import config
from sentence_search.models import SeedRequest, SentenceOccurrence

router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class IndexSelection(StrEnum):
    occurrences = config.occurrence_alias
    semantic_catalog = config.catalog_alias


@router.get("/health", tags=["System"])
def health():
    """Check OpenSearch and PostgreSQL without calling Titan."""

    try:
        opensearch_ready = opensearch_store.client.ping()
        postgres_stats = postgres_store.stats()
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "status": "ok" if opensearch_ready else "down",
        "opensearch": (f"{config.opensearch_host}:{config.opensearch_port}"),
        "occurrenceIndex": config.occurrence_index,
        "occurrenceAlias": config.occurrence_alias,
        "catalogIndex": config.catalog_index,
        "catalogAlias": config.catalog_alias,
        "semanticModelId": config.semantic_model_id,
        "ingestPipeline": config.ingest_pipeline,
        "postgres": postgres_stats,
        "modelInvoked": False,
    }


@router.get("/stats", tags=["System"])
def stats():
    """Show OpenSearch and PostgreSQL record counts."""

    try:
        return {
            "opensearch": opensearch_store.stats(),
            "postgres": postgres_store.stats(),
        }
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/index-documents", tags=["System"])
def index_documents(
    index: Annotated[
        IndexSelection,
        Query(description="Choose the occurrence or catalog alias."),
    ],
    count: Annotated[int, Query(ge=1, le=100)] = 10,
):
    """Preview unfiltered OpenSearch documents without catalog vectors."""

    try:
        return {
            "index": index.value,
            **opensearch_store.preview(index.value, count),
        }
    except OpenSearchException as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/admin/init", tags=["Admin"])
def initialize_storage():
    """Create two OpenSearch indexes and two PostgreSQL tables."""

    try:
        opensearch_store.ensure_indices()
        postgres_store.init_schema()
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "message": "Sentence semantic-search storage is ready",
        "occurrenceIndex": config.occurrence_index,
        "catalogIndex": config.catalog_index,
        "postgresTables": [
            "sentence_key_matches",
            "application_match_summary",
        ],
    }


@router.delete("/admin/storage", tags=["Admin"])
def delete_storage(
    confirm: Annotated[
        bool,
        Query(description="Must be true to delete all project data."),
    ] = False,
):
    """Delete both OpenSearch indexes and all PostgreSQL project rows."""

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to delete all sentence-search data.",
        )
    try:
        deleted = opensearch_store.delete_indices()
        postgres_store.reset_data()
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "message": "OpenSearch and PostgreSQL sentence data were deleted",
        **deleted,
    }


@router.post("/admin/seed", tags=["Admin"])
def seed(request: SeedRequest):
    """Load the CSV in its existing order and calculate matches."""

    path = Path(request.csvPath).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        return seed_service.seed(
            path,
            request.reset,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/sentences", tags=["Sentences"])
def add_sentence(sentence: SentenceOccurrence):
    """Add one sentence and calculate its matches."""

    try:
        return sentence_service.add_sentence(sentence)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.delete("/applications/{applicationId}", tags=["Admin"])
def delete_application(
    applicationId: str,
    confirm: Annotated[
        bool,
        Query(description="Must be true to delete the application sentences."),
    ] = False,
):
    """Delete one application's sentences and unused key relationships."""

    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to delete.")
    try:
        return deletion_service.delete_application(applicationId)
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.delete(
    "/documents/{tspId}",
    tags=["Admin"],
)
def delete_tsp_document(
    tspId: str,
    applicationId: Annotated[
        str | None,
        Query(description="Optional safety filter when a TSP ID is not unique."),
    ] = None,
    confirm: Annotated[
        bool,
        Query(description="Must be true to delete the TSP document sentences."),
    ] = False,
):
    """Delete one TSP document and its unused key relationships."""

    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to delete.")
    try:
        return deletion_service.delete_tsp(tspId, applicationId)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get(
    "/applications/{applicationId}/sentences/semantic-summary",
    tags=["Sentence matches"],
)
def application_semantic_summary(
    applicationId: str,
    analysisGroup: Annotated[str, Query(min_length=1)] = "Asylee",
    pageSize: Annotated[int, Query(ge=1, le=100)] = 100,
    nextToken: str | None = None,
):
    """Get 100 application sentences and each sentence's match counts."""

    try:
        return sentence_summary_service.application_summary(
            applicationId,
            analysisGroup,
            pageSize,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get(
    "/applications/{applicationId}/sentences/semantic-search",
    tags=["Sentence matches"],
)
def sentence_key_semantic_search(
    applicationId: str,
    sentenceKey: Annotated[
        str,
        Query(
            min_length=64,
            max_length=64,
            description="SHA-256 sentenceKey already saved during ingestion.",
        ),
    ],
    analysisGroup: Annotated[str, Query(min_length=1)] = "Asylee",
    pageSize: Annotated[int, Query(ge=1, le=100)] = 100,
    nextToken: str | None = None,
):
    """Find exact and similar sentences by key without running neural search."""

    try:
        return sentence_key_search_service.search(
            applicationId,
            sentenceKey,
            analysisGroup,
            pageSize,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
