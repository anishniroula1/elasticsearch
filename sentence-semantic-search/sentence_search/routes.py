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
    sentence_service,
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
    """Show OpenSearch, PostgreSQL, and worker record counts."""

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
    """Create the two indexes, aliases, and PostgreSQL tables."""

    try:
        opensearch_store.ensure_indices()
        postgres_store.init_schema()
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "message": "Sentence semantic-search storage is ready",
        "occurrenceIndex": config.occurrence_index,
        "catalogIndex": config.catalog_index,
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
    """Load the CSV in its existing order and queue match jobs."""

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


@router.post("/admin/jobs/retry-failed", tags=["Admin"])
def retry_failed_jobs():
    """Retry jobs that reached the ten-attempt failure limit."""

    try:
        count = postgres_store.retry_failed_jobs()
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"jobsRequeued": count}


@router.post("/sentences", tags=["Sentences"])
def add_sentence(sentence: SentenceOccurrence):
    """Add one sentence and queue its background matching job."""

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
    """Delete one application's sentences, relationships, and summaries."""

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
    """Delete one TSP document and every relationship using its sentences."""

    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to delete.")
    try:
        return deletion_service.delete_tsp(tspId, applicationId)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get(
    "/applications/{applicationId}/summary",
    tags=["Sentence matches"],
)
def application_sentence_summary(
    applicationId: str,
    analysisGroup: Annotated[str, Query(min_length=1)] = "Asylee",
):
    """Return prepared counts for one application and analysis group."""

    try:
        result = postgres_store.application_summary(applicationId, analysisGroup)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Application and analysis group not found",
        )
    return result


@router.get(
    "/applications/{applicationId}/sentences",
    tags=["Sentence matches"],
)
def application_sentences(
    applicationId: str,
    analysisGroup: Annotated[str, Query(min_length=1)] = "Asylee",
    pageSize: Annotated[int, Query(ge=1, le=100)] = 100,
    nextToken: str | None = None,
):
    """Return 100 application sentences and their saved match counts."""

    try:
        return postgres_store.application_sentences(
            applicationId,
            analysisGroup,
            pageSize,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get(
    "/sentences/{globalId}/matches",
    tags=["Sentence matches"],
)
def sentence_matches(
    globalId: str,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 100,
    nextToken: str | None = None,
):
    """Return one sentence's exact count and paginated candidate list."""

    try:
        result = postgres_store.sentence_matches(
            globalId,
            pageSize,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="Sentence not found")
    return result
