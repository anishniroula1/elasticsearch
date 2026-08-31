from fastapi import APIRouter, HTTPException, Query

from app.config import config
from app.fuzzy_search import (
    find_fuzzy_matches_by_text,
    get_application_fuzzy_summary,
)
from app.search_client import client, ensure_index
from app.seed import seed_documents
from app.services import (
    find_entity_matching_cases,
    find_matching_entities,
    find_shared_entities,
    find_similar_cases,
    find_similar_entity_cases,
    get_application_entities,
    health_status,
    index_stats,
    search_fuzzy_entities,
)


router = APIRouter()


@router.get("/health", tags=["System"])
def health():
    """Check if API is able to connect with OpenSearch."""

    return health_status()


@router.get("/stats", tags=["System"])
def stats():
    """Get total document count and cluster status."""

    return index_stats()


@router.post("/admin/init", tags=["Admin"])
def init_index():
    """Create index and alias if they are not there."""

    ensure_index()
    return {
        "message": "Index and alias are ready",
        "index": config.physical_index,
        "alias": config.index_alias,
    }


@router.post("/admin/seed", tags=["Admin"])
def seed(
    count: int = Query(
        default=None,
        ge=1,
        description=(
            "Number of fake records, or maximum CSV rows. "
            "Leave empty to load every CSV row."
        ),
    ),
    reset: bool = Query(default=False),
    csvPath: str = Query(
        default="",
        description=(
            "CSV path inside the project. Leave empty to generate fake data."
        ),
    ),
):
    """Add fake or CSV data. Reset can recreate the index first."""

    try:
        seed_result = seed_documents(count, csvPath, reset)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        **seed_result,
        "totalDocuments": client.count(index=config.index_alias)["count"],
        "reset": reset,
    }


@router.get(
    "/applications/{application_id}/entities",
    tags=["Case entities"],
)
def application_entities(application_id: str):
    """Get all unique entities for an application."""

    entities = get_application_entities(application_id)
    return {
        "applicationId": application_id,
        "totalUniqueEntities": len(entities),
        "entities": entities,
    }


@router.get(
    "/applications/{application_id}/similar-entity-cases",
    tags=["Case entities"],
)
def similar_entity_cases(application_id: str):
    """Get other cases ordered by how many entity matches they have."""

    return find_similar_entity_cases(application_id)


@router.get(
    "/applications/{application_id}/entities/fuzzy-summary",
    tags=["Fuzzy search"],
)
def fuzzy_entity_summary(
    application_id: str,
    threshold: int = Query(default=90, ge=1, le=100),
):
    """Get each application entity with fuzzy match counts."""

    return get_application_fuzzy_summary(application_id, threshold)


@router.get(
    "/applications/{application_id}/entities/fuzzy-search",
    tags=["Fuzzy search"],
)
def fuzzy_entity_text_search(
    application_id: str,
    text: str = Query(min_length=2),
    threshold: int = Query(default=90, ge=1, le=100),
):
    """Find all entity text matches above the threshold."""

    return find_fuzzy_matches_by_text(application_id, text, threshold)


@router.get(
    "/applications/{application_id}/matching-entities",
    tags=["Case entities"],
)
def application_matching_entities(
    application_id: str,
    size: int = Query(default=20, ge=1, le=100),
    nextToken=None,
):
    """Get matching applications with their shared entity details."""

    return find_matching_entities(application_id, size, nextToken)


@router.get(
    "/applications/{application_id}/entities/{entity_id}/matching-cases",
    tags=["Case entities"],
)
def matching_cases(
    application_id: str,
    entity_id: str,
    size: int = Query(default=20, ge=1, le=100),
    nextToken=None,
):
    """Get other applications that have this entity."""

    return find_entity_matching_cases(
        application_id,
        entity_id,
        size,
        nextToken,
    )


@router.get(
    "/applications/{application_id}/similar-cases",
    tags=["Similar cases"],
)
def similar_cases(
    application_id: str,
    minSharedEntities: int = Query(default=2, ge=1, le=20),
    size: int = Query(default=20, ge=1, le=100),
):
    """Get cases that have the minimum number of shared entities."""

    return find_similar_cases(
        application_id,
        minSharedEntities,
        size,
    )


@router.get(
    "/applications/{application_id}/similar-cases/"
    "{other_application_id}/shared-entities",
    tags=["Similar cases"],
)
def shared_entities(
    application_id: str,
    other_application_id: str,
):
    """Get the entities shared between two applications."""

    return find_shared_entities(application_id, other_application_id)


@router.get("/entities/fuzzy", tags=["Fuzzy search"])
def fuzzy_entities(
    text: str = Query(min_length=2),
    size: int = Query(default=10, ge=1, le=50),
):
    """Search entity names and allow small spelling mistakes."""

    return search_fuzzy_entities(text, size)
