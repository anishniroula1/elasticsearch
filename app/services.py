"""Elasticsearch methods used by the API routes."""



from datetime import datetime, timezone
from typing import Any

from elasticsearch import NotFoundError

from app.config import config
from app.models import EntityEvent
from app.pagination import decode_page_token, encode_page_token
from app.search_client import client, ensure_index


ENTITY_SOURCE_FIELDS = [
    "sentenceEntityId",
    "tspId",
    "globalId",
    "entityId",
    "rawEntity",
    "normalizedText",
    "entityType",
    "possibleSanction",
    "beginOffset",
    "endOffset",
    "score",
    "documentType",
]


def health_status():
    """Check Elasticsearch and return the current status."""

    return {
        "status": "ok" if client.ping() else "down",
        "elasticsearch": config.elasticsearch_url,
        "index": config.physical_index,
        "alias": config.index_alias,
    }


def index_stats():
    """Get document count and Elasticsearch cluster status."""

    ensure_index()
    return {
        "physicalIndex": config.physical_index,
        "alias": config.index_alias,
        "documents": client.count(index=config.index_alias)["count"],
        "clusterHealth": client.cluster.health()["status"],
    }


def apply_entity_event(event: EntityEvent):
    """Create, update or delete one entity occurrence."""

    ensure_index()

    if event.eventType == "SENTENCE_ENTITY_DELETED":
        try:
            # Remove the occurrence by its sentence entity ID.
            client.delete(
                index=config.index_alias,
                id=str(event.sentenceEntityId),
                refresh="wait_for",
            )
            action = "deleted"
        except NotFoundError:
            action = "already_deleted"
        return {
            "action": action,
            "sentenceEntityId": event.sentenceEntityId,
        }

    # Same ID will create a new document or replace the old one.
    client.index(
        index=config.index_alias,
        id=str(event.sentenceEntityId),
        document=_event_document(event),
        refresh="wait_for",
    )
    return {
        "action": "upserted",
        "sentenceEntityId": event.sentenceEntityId,
    }


def get_application_entities(
    application_id: str,
    source_limit: int = 10,
):
    """Get unique entities found in one application."""

    # Get unique entities and few source records for each one.
    # size=0 means we only need the aggregation result.
    response = client.search(
        index=config.index_alias,
        size=0,
        query={"term": {"applicationId": application_id}},
        aggs={
            "entities": {
                "terms": {"field": "entityId", "size": 200},
                "aggs": {
                    "sample": {
                        "top_hits": {
                            "size": source_limit,
                            "_source": ENTITY_SOURCE_FIELDS,
                        }
                    }
                },
            }
        },
    )

    entities: list[dict[str, Any]] = []
    # Each bucket is one unique entity in this application.
    for bucket in response["aggregations"]["entities"]["buckets"]:
        sample_hits = bucket["sample"]["hits"]["hits"]
        sample = sample_hits[0]["_source"]

        # Run one more query for each entity to count other applications.
        count_response = client.search(
            index=config.index_alias,
            size=0,
            query={
                "bool": {
                    "filter": [{"term": {"entityId": bucket["key"]}}],
                    "must_not": [{"term": {"applicationId": application_id}}],
                }
            },
            aggs={
                "cases": {
                    "cardinality": {
                        "field": "applicationId",
                        "precision_threshold": 40_000,
                    }
                }
            },
        )
        entities.append(
            {
                "entityId": bucket["key"],
                "normalizedText": sample["normalizedText"],
                "rawEntity": sample["rawEntity"],
                "entityType": sample["entityType"],
                "possibleSanction": sample["possibleSanction"],
                "countInCurrentCase": bucket["doc_count"],
                "matchingOtherCaseCount": (
                    count_response["aggregations"]["cases"]["value"]
                ),
                "sourceLocations": [
                    hit["_source"]
                    for hit in sample_hits
                ],
            }
        )

    entities.sort(
        key=lambda item: (
            -item["countInCurrentCase"],
            item["normalizedText"],
        )
    )
    return entities


def find_similar_entity_cases(application_id: str):
    """Get other applications ordered by total entity matches."""

    entity_ids = _get_application_entity_ids(application_id)
    if not entity_ids:
        return {
            "applicationId": application_id,
            "basedOnSimilarEntities": 0,
            "otherCases": [],
        }

    # Search all other applications using the current application entities.
    # Aggregation groups matches by application and counts occurrences.
    response = client.search(
        index=config.index_alias,
        size=0,
        query=_other_applications_query(application_id, entity_ids),
        aggs={
            "matchingApplicationCount": {
                "cardinality": {
                    "field": "applicationId",
                    "precision_threshold": 40_000,
                }
            },
            "otherCases": {
                "terms": {
                    "field": "applicationId",
                    "size": 10_000,
                    "order": [
                        {"_count": "desc"},
                        {"_key": "asc"},
                    ],
                }
            },
        },
    )

    return {
        "applicationId": application_id,
        "basedOnSimilarEntities": int(
            response["aggregations"]["matchingApplicationCount"]["value"]
        ),
        # Change each application bucket into the API response format.
        "otherCases": [
            {
                "applicationId": bucket["key"],
                # The filtered documents contain only entities from the
                # searched application, so this count is the requested total.
                "matchingEntityOccurrenceCount": bucket["doc_count"],
            }
            for bucket in response["aggregations"]["otherCases"]["buckets"]
        ],
    }


def find_matching_entities(
    application_id: str,
    page_size: int,
    next_token=None,
):
    """Get applications and entity details shared with one application."""

    current_entities = get_application_entities(
        application_id,
        source_limit=1,
    )
    current_by_id = {
        item["entityId"]: item
        for item in current_entities
    }
    entity_ids = list(current_by_id)

    if not entity_ids:
        return {
            "applicationId": application_id,
            "totalUniqueEntitiesInSearchedApplication": 0,
            "totalMatchingApplications": 0,
            "matchingApplications": [],
            "nextToken": None,
        }

    composite: dict[str, Any] = {
        # Ask for one extra item so we know if another page exists.
        "size": page_size + 1,
        "sources": [
            {"applicationId": {"terms": {"field": "applicationId"}}}
        ],
    }
    after_key = decode_page_token(next_token)
    if after_key:
        composite["after"] = after_key

    # Get one page of applications and entities shared with each one.
    response = client.search(
        index=config.index_alias,
        size=0,
        query=_other_applications_query(application_id, entity_ids),
        aggs={
            "matchingApplicationCount": {
                "cardinality": {
                    "field": "applicationId",
                    "precision_threshold": 40_000,
                }
            },
            "matchingApplications": {
                "composite": composite,
                "aggs": {
                    "entities": {
                        "terms": {
                            "field": "entityId",
                            "size": 200,
                        }
                    }
                },
            },
        },
    )

    all_buckets = response["aggregations"]["matchingApplications"]["buckets"]
    page_buckets = all_buckets[:page_size]
    # Build entity details for every application in this page.
    matching_applications = [
        _matching_application_result(bucket, current_by_id)
        for bucket in page_buckets
    ]

    new_token = None
    if len(all_buckets) > page_size and page_buckets:
        new_token = encode_page_token(page_buckets[-1]["key"])

    return {
        "applicationId": application_id,
        "totalUniqueEntitiesInSearchedApplication": len(current_entities),
        "totalMatchingApplications": (
            response["aggregations"]["matchingApplicationCount"]["value"]
        ),
        "matchingApplications": matching_applications,
        "nextToken": new_token,
    }


def find_entity_matching_cases(
    application_id: str,
    entity_id: str,
    page_size: int,
    next_token=None,
):
    """Get other applications that contain the selected entity."""

    composite: dict[str, Any] = {
        "size": page_size,
        "sources": [
            {"applicationId": {"terms": {"field": "applicationId"}}}
        ],
    }
    after_key = decode_page_token(next_token)
    if after_key:
        composite["after"] = after_key

    # Find other applications with this entity and group by application ID.
    response = client.search(
        index=config.index_alias,
        size=0,
        query={
            "bool": {
                "filter": [{"term": {"entityId": entity_id}}],
                "must_not": [{"term": {"applicationId": application_id}}],
            }
        },
        aggs={"matchingCases": {"composite": composite}},
    )

    matching_cases = response["aggregations"]["matchingCases"]
    return {
        "applicationId": application_id,
        "entityId": entity_id,
        "matchingCases": [
            {
                "applicationId": bucket["key"]["applicationId"],
                "occurrences": bucket["doc_count"],
            }
            for bucket in matching_cases["buckets"]
        ],
        "nextToken": encode_page_token(matching_cases.get("after_key")),
    }


def find_similar_cases(
    application_id: str,
    minimum_shared_entities: int,
    result_size: int,
):
    """Get cases that have the minimum number of unique entities."""

    current_entities = get_application_entities(
        application_id,
        source_limit=1,
    )
    entity_ids = [
        item["entityId"]
        for item in current_entities
    ]
    if not entity_ids:
        return {
            "applicationId": application_id,
            "similarCases": [],
        }

    # Group other applications and count unique shared entities.
    response = client.search(
        index=config.index_alias,
        size=0,
        query=_other_applications_query(application_id, entity_ids),
        aggs={
            "cases": {
                "terms": {
                    "field": "applicationId",
                    "size": min(max(result_size * 10, 100), 1_000),
                    "order": {"sharedEntityCount": "desc"},
                },
                "aggs": {
                    "sharedEntityCount": {
                        "cardinality": {"field": "entityId"}
                    },
                    "sharedEntities": {
                        "terms": {"field": "entityId", "size": 100}
                    },
                },
            }
        },
    )

    similar_cases = []
    # Skip cases below minimum and stop when requested size is reached.
    for bucket in response["aggregations"]["cases"]["buckets"]:
        shared_count = int(bucket["sharedEntityCount"]["value"])
        if shared_count < minimum_shared_entities:
            continue

        similar_cases.append(
            {
                "applicationId": bucket["key"],
                "sharedEntityCount": shared_count,
                "matchingOccurrenceCount": bucket["doc_count"],
                "sharedEntityIds": [
                    entity_bucket["key"]
                    for entity_bucket in bucket["sharedEntities"]["buckets"]
                ],
            }
        )
        if len(similar_cases) >= result_size:
            break

    return {
        "applicationId": application_id,
        "similarCases": similar_cases,
    }


def find_shared_entities(
    application_id: str,
    other_application_id: str,
):
    """Get all entities shared by two applications."""

    # Load both applications and group their records by entity ID.
    response = client.search(
        index=config.index_alias,
        size=0,
        query={
            "terms": {
                "applicationId": [
                    application_id,
                    other_application_id,
                ]
            }
        },
        aggs={
            "entities": {
                "terms": {"field": "entityId", "size": 500},
                "aggs": {
                    "applications": {
                        "terms": {"field": "applicationId", "size": 2}
                    },
                    "sample": {
                        "top_hits": {
                            "size": 1,
                            "_source": [
                                "entityId",
                                "rawEntity",
                                "normalizedText",
                                "entityType",
                                "possibleSanction",
                            ],
                        }
                    },
                    "foundIn": {
                        "terms": {"field": "documentType", "size": 20}
                    },
                },
            }
        },
    )

    shared_entities = []
    # Keep only entity buckets that exist in both applications.
    for bucket in response["aggregations"]["entities"]["buckets"]:
        application_counts = {
            item["key"]: item["doc_count"]
            for item in bucket["applications"]["buckets"]
        }
        if (
            application_id not in application_counts
            or other_application_id not in application_counts
        ):
            continue

        sample = bucket["sample"]["hits"]["hits"][0]["_source"]
        shared_entities.append(
            {
                **sample,
                "occurrenceInCurrentCase": (
                    application_counts[application_id]
                ),
                "occurrenceInOtherCase": (
                    application_counts[other_application_id]
                ),
                "foundIn": [
                    {
                        "documentType": item["key"],
                        "occurrences": item["doc_count"],
                    }
                    for item in bucket["foundIn"]["buckets"]
                ],
            }
        )

    shared_entities.sort(
        key=lambda item: -item["occurrenceInOtherCase"]
    )
    return {
        "applicationId": application_id,
        "otherApplicationId": other_application_id,
        "sharedEntityCount": len(shared_entities),
        "sharedEntities": shared_entities,
    }


def search_fuzzy_entities(text: str, result_size: int):
    """Find entities with name close to the searched text."""

    # Fuzzy match the text and collapse duplicates by entity ID.
    response = client.search(
        index=config.index_alias,
        size=result_size,
        query={
            "match": {
                "entitySearchText": {
                    "query": text,
                    "fuzziness": "AUTO",
                    "prefix_length": 1,
                }
            }
        },
        collapse={"field": "entityId"},
        source=[
            "entityId",
            "rawEntity",
            "normalizedText",
            "entityType",
            "possibleSanction",
        ],
    )
    return {
        "searchedText": text,
        # Return only the fields needed by the API.
        "matches": [
            {
                "score": hit["_score"],
                **hit["_source"],
            }
            for hit in response["hits"]["hits"]
        ],
    }


def _event_document(event: EntityEvent):
    """Convert event data into document we store in Elasticsearch."""

    created_at = event.createdAt or datetime.now(timezone.utc)
    updated_at = event.updatedAt or datetime.now(timezone.utc)
    return {
        "sentenceEntityId": event.sentenceEntityId,
        "applicationId": event.applicationId,
        "tspId": event.tspId,
        "globalId": event.globalId,
        "entityId": event.entityId,
        "rawEntity": event.rawEntity,
        "normalizedText": event.normalizedText,
        "entitySearchText": event.normalizedText,
        "entityType": event.entityType,
        "possibleSanction": event.possibleSanction,
        "beginOffset": event.beginOffset,
        "endOffset": event.endOffset,
        "score": event.score,
        "source": event.source,
        "documentType": event.documentType,
        "createdAt": created_at.isoformat(),
        "updatedAt": updated_at.isoformat(),
    }


def _get_application_entity_ids(application_id: str):
    """Get unique entity IDs for one application."""

    # We only need entity buckets here, no full documents.
    response = client.search(
        index=config.index_alias,
        size=0,
        query={"term": {"applicationId": application_id}},
        aggs={"entities": {"terms": {"field": "entityId", "size": 200}}},
    )
    return [
        bucket["key"]
        for bucket in response["aggregations"]["entities"]["buckets"]
    ]


def _other_applications_query(
    application_id: str,
    entity_ids: list[str],
):
    """Create query used to find matching entities in other applications."""

    return {
        "bool": {
            "filter": [{"terms": {"entityId": entity_ids}}],
            "must_not": [{"term": {"applicationId": application_id}}],
        }
    }


def _matching_application_result(
    application_bucket: dict[str, Any],
    current_by_id: dict[str, dict[str, Any]],
):
    """Change one matching application bucket into API response format."""

    shared_entities = []
    # Add occurrence counts for every shared entity in this application.
    for entity_bucket in application_bucket["entities"]["buckets"]:
        current_entity = current_by_id[entity_bucket["key"]]
        shared_entities.append(
            {
                "entityId": entity_bucket["key"],
                "normalizedText": current_entity["normalizedText"],
                "rawEntity": current_entity["rawEntity"],
                "entityType": current_entity["entityType"],
                "possibleSanction": current_entity["possibleSanction"],
                "countInSearchedApplication": (
                    current_entity["countInCurrentCase"]
                ),
                "countInMatchingApplication": entity_bucket["doc_count"],
            }
        )

    shared_entities.sort(
        key=lambda item: (
            -item["countInMatchingApplication"],
            item["normalizedText"],
        )
    )
    return {
        "applicationId": application_bucket["key"]["applicationId"],
        "sharedEntityCount": len(shared_entities),
        "matchingOccurrenceCount": application_bucket["doc_count"],
        "entities": shared_entities,
    }
