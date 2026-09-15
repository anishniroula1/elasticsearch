from enum import StrEnum
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from opensearchpy.exceptions import OpenSearchException

from semantic_search.components import (
    paginated_summary_service,
    store,
    summary_service,
    text_search_service,
)
from semantic_search.config import config
from semantic_search.seed_service import seed_from_csv

router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class IndexSelection(StrEnum):
    occurrences = config.occurrence_alias
    semantic_catalog = config.catalog_alias


@router.get("/health", tags=["System"])
def health():
    """Check if OpenSearch is working. This does not call Titan."""

    is_ready = store.client.ping()
    response = {
        "status": "ok" if is_ready else "down",
        "opensearch": (
            f"{config.opensearch_host}:{config.opensearch_port}"
        ),
        "occurrenceIndex": config.occurrence_index,
        "occurrenceAlias": config.occurrence_alias,
        "semanticCatalogIndex": config.catalog_index,
        "semanticCatalogAlias": config.catalog_alias,
        "semanticModelId": config.semantic_model_id,
        "ingestPipeline": config.ingest_pipeline,
        "semanticTextField": "entitySearchText",
        "semanticVectorField": "entitySearchTextVector",
        "modelInvoked": False,
    }
    if is_ready:
        response.update(store.stats())
    return response


@router.get("/stats", tags=["System"])
def stats():
    """Count the records in both indexes."""

    try:
        return {
            "occurrenceIndex": config.occurrence_index,
            "occurrenceAlias": config.occurrence_alias,
            "semanticCatalogIndex": config.catalog_index,
            "semanticCatalogAlias": config.catalog_alias,
            **store.stats(),
        }
    except OpenSearchException as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch stats failed: {error}",
        ) from error


@router.get("/index-documents", tags=["System"])
def index_documents(
    index: Annotated[
        IndexSelection,
        Query(description="Select one of the two indexes to preview."),
    ],
    count: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Number of unfiltered documents to return.",
        ),
    ] = 10,
):
    """Show sample records from the selected index."""

    physical_index = (
        config.occurrence_index
        if index is IndexSelection.occurrences
        else config.catalog_index
    )
    try:
        preview = store.preview_documents(index.value, size=count)
    except OpenSearchException as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch index preview failed: {error}",
        ) from error
    return {
        "selectedIndex": physical_index,
        "selectedAlias": index.value,
        **preview,
    }


@router.post("/admin/init", tags=["Admin"])
def init_indices():
    """Create both indexes and aliases when they are missing."""

    try:
        store.ensure_indices()
    except (OpenSearchException, RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch initialization failed: {error}",
        ) from error
    return {
        "message": "Indexes and aliases are ready",
        "occurrenceIndex": config.occurrence_index,
        "occurrenceAlias": config.occurrence_alias,
        "semanticCatalogIndex": config.catalog_index,
        "semanticCatalogAlias": config.catalog_alias,
        "ingestPipeline": config.ingest_pipeline,
    }


@router.delete("/admin/indexes", tags=["Admin"])
def delete_indices(
    confirm: Annotated[
        bool,
        Query(
            description=(
                "Must be true to delete both indexes, their data, and aliases."
            ),
        ),
    ] = False,
):
    """Delete both indexes, their records, and their aliases."""

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to delete both indexes and aliases.",
        )
    try:
        result = store.delete_indices_and_aliases()
    except OpenSearchException as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch index deletion failed: {error}",
        ) from error
    return {
        "message": "Semantic-search indexes and aliases were deleted",
        **result,
    }


@router.post("/admin/seed", tags=["Admin"])
def seed(
    reset: Annotated[
        bool,
        Query(
            description=(
                "true recreates both indexes; false preserves existing data "
                "and only embeds catalog texts that do not already exist."
            ),
        ),
    ],
    csvPath: str = Query(
        default="data/seed.csv",
        min_length=1,
        description=(
            "Absolute CSV path or a path relative to this project."
        ),
    ),
):
    """Load a CSV and choose whether to replace the old data."""

    csv_path = Path(csvPath).expanduser()
    if not csv_path.is_absolute():
        csv_path = PROJECT_ROOT / csv_path
    try:
        return seed_from_csv(
            csv_path,
            target_store=store,
            reset=reset,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch seeding failed: {error}",
        ) from error


@router.get(
    "/applications/{application_id}/entities/semantic-summary",
    tags=["Semantic search"],
)
def application_semantic_summary(
    application_id: str,
    threshold: int = Query(default=90, ge=1, le=100),
):
    """Get all application entities and their match counts."""

    try:
        return summary_service.application_summary(
            application_id,
            threshold,
        )
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch search failed: {error}",
        ) from error


@router.get(
    "/applications/{application_id}/entities/semantic-matches",
    tags=["Semantic search"],
)
def paginated_application_semantic_matches(
    application_id: str,
    threshold: int = Query(default=90, ge=1, le=100),
    nextToken: str | None = Query(
        default=None,
        description=(
            "Use the nextToken from the last response. Leave it empty for "
            "the first page."
        ),
    ),
):
    """Get 100 application entities and their match counts at a time."""

    try:
        return paginated_summary_service.application_matches(
            application_id,
            threshold,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch search failed: {error}",
        ) from error


@router.get(
    "/applications/{application_id}/entities/semantic-search",
    tags=["Semantic search"],
)
def semantic_entity_text_search(
    application_id: str,
    text: str = Query(min_length=2),
    threshold: int = Query(default=90, ge=1, le=100),
):
    """Find all text matches outside the current application."""

    try:
        return text_search_service.search_text(
            application_id,
            text,
            threshold,
        )
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch search failed: {error}",
        ) from error


@router.get(
    "/applications/{application_id}/entities/semantic-search-paginated",
    tags=["Semantic search"],
)
def paginated_semantic_entity_text_search(
    application_id: str,
    text: str = Query(min_length=2),
    threshold: int = Query(default=90, ge=1, le=100),
    nextToken: str | None = Query(
        default=None,
        description="Use the nextToken from the last response.",
    ),
):
    """Return up to 100 text matches at a time."""

    try:
        return text_search_service.search_text_page(
            application_id,
            text,
            threshold,
            nextToken,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch search failed: {error}",
        ) from error
