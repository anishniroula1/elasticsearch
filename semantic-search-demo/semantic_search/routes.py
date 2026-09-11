from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from opensearchpy.exceptions import OpenSearchException

from semantic_search.cli import seed_from_csv
from semantic_search.components import service, store
from semantic_search.config import (
    CATALOG_ALIAS,
    CATALOG_INDEX,
    OCCURRENCE_ALIAS,
    OCCURRENCE_INDEX,
    OPENSEARCH_PORT,
    config,
)


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@router.get("/health", tags=["System"])
def health():
    """Check OpenSearch without running model inference."""

    is_ready = store.client.ping()
    response = {
        "status": "ok" if is_ready else "down",
        "opensearch": f"{config.opensearch_host}:{OPENSEARCH_PORT}",
        "occurrenceIndex": OCCURRENCE_INDEX,
        "occurrenceAlias": OCCURRENCE_ALIAS,
        "semanticCatalogIndex": CATALOG_INDEX,
        "semanticCatalogAlias": CATALOG_ALIAS,
        "semanticModelId": config.semantic_model_id,
        "semanticField": "entitySearchText",
        "modelInvoked": False,
    }
    if is_ready:
        response.update(store.stats())
    return response


@router.post("/admin/init", tags=["Admin"])
def init_indices():
    """Create the two indexes and aliases when they do not exist."""

    try:
        store.ensure_indices()
    except (OpenSearchException, RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch initialization failed: {error}",
        ) from error
    return {
        "message": "Indexes and aliases are ready",
        "occurrenceIndex": OCCURRENCE_INDEX,
        "occurrenceAlias": OCCURRENCE_ALIAS,
        "semanticCatalogIndex": CATALOG_INDEX,
        "semanticCatalogAlias": CATALOG_ALIAS,
    }


@router.post("/admin/seed", tags=["Admin"])
def seed(
    csvPath: str = Query(
        default="data/seed.csv",
        min_length=1,
        description=(
            "Absolute CSV path or a path relative to this project. "
            "Seeding recreates both indexes."
        ),
    ),
):
    """Recreate both indexes and generate fresh catalog embeddings."""

    csv_path = Path(csvPath).expanduser()
    if not csv_path.is_absolute():
        csv_path = PROJECT_ROOT / csv_path
    try:
        return seed_from_csv(csv_path)
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
    """Get application entities with exact and semantic match counts."""

    try:
        return service.application_summary(application_id, threshold)
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
    """Find exact and semantic text matches outside the application."""

    try:
        return service.search_text(application_id, text, threshold)
    except (OpenSearchException, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"OpenSearch search failed: {error}",
        ) from error
