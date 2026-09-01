"""Fuzzy entity searches used by the application APIs."""

import unicodedata
from concurrent.futures import ThreadPoolExecutor

from app.search_client import search_index


FUZZINESS = "AUTO:5,8"
MAX_WORKERS = 8

OCCURRENCE_FIELDS = [
    "sentenceEntityId",
    "applicationId",
    "tspId",
    "globalId",
    "entityId",
    "rawEntity",
    "normalizedText",
    "entitySearchText",
    "entityType",
    "possibleSanction",
    "beginOffset",
    "endOffset",
    "score",
    "documentType",
]


def get_application_fuzzy_summary(application_id, threshold):
    """Get application items, fuzzy matches, and then calculate the counts."""

    # First query gets every entity occurrence for this application.
    application_items = _search_application_items(application_id)
    application_entities = _group_application_items(application_items)
    if not application_entities:
        return {
            "applicationId": application_id,
            "totalUniqueEntities": 0,
            "entities": [],
        }

    source_entities = [
        {
            "entityId": entity["entityId"],
            "entitySearchText": _entity_text(entity["sourceLocations"][0]),
        }
        for entity in application_entities
    ]

    # Second query sends every entitySearchText in one fuzzy bool query.
    candidates = _search_candidates(application_id, source_entities)

    # OpenSearch returns possible candidates. Calculate the final percentage
    # and counts after the complete second response is available.
    matches = _calculate_matches(source_entities, candidates, threshold)
    counts = {
        entity["entityId"]: {"verbatim": 0, "similar": 0}
        for entity in source_entities
    }
    for match in matches:
        counts[match["sourceEntityId"]][match["matchType"]] += match[
            "occurrenceCount"
        ]

    application_counts = {
        candidate["entityId"]: candidate["uniqueApplicationIdCount"]
        for candidate in candidates
    }
    for entity in application_entities:
        entity_counts = counts[entity["entityId"]]
        entity["matchingOtherCaseCount"] = application_counts.get(
            entity["entityId"],
            0,
        )
        entity["verbatimMatchCount"] = entity_counts["verbatim"]
        entity["similarMatchCount"] = entity_counts["similar"]

    return {
        "applicationId": application_id,
        "totalUniqueEntities": len(application_entities),
        "entities": application_entities,
    }


def find_fuzzy_matches_by_text(application_id, text, threshold):
    """Search one text and exclude the passed application from the result."""

    source_entities = [
        {
            "entityId": "searched-text",
            "entitySearchText": text,
        }
    ]
    candidates = _search_candidates(application_id, source_entities)
    entity_matches = _calculate_matches(
        source_entities,
        candidates,
        threshold,
    )
    locations_by_entity_id = _search_occurrences_by_entity_ids(
        application_id,
        [match["matchedEntityId"] for match in entity_matches],
    )

    matches = []
    for entity_match in entity_matches:
        entity_id = entity_match["matchedEntityId"]
        source_locations = locations_by_entity_id[entity_id]
        matches.append(
            {
                "entityId": entity_id,
                "matchPercentage": entity_match["matchPercentage"],
                "matchType": entity_match["matchType"],
                "uniqueApplicationIdCount": len(
                    {
                        location["applicationId"]
                        for location in source_locations
                    }
                ),
                "totalCount": len(source_locations),
                "sourceLocations": source_locations,
            }
        )

    return {
        "applicationId": application_id,
        "searchedText": text,
        "thresholdPercentage": threshold,
        "totalMatches": len(matches),
        "totalSourceLocations": sum(
            len(locations)
            for locations in locations_by_entity_id.values()
        ),
        "matches": matches,
    }


def _search_application_items(application_id):
    """First query gets every item for the application ID."""

    buckets = _read_all_buckets(
        query={"term": {"applicationId": application_id}},
        aggregation_name="applicationItems",
        group_field="sentenceEntityId",
        source_fields=OCCURRENCE_FIELDS,
    )
    return [_bucket_source(bucket) for bucket in buckets]


def _group_application_items(application_items):
    """Group the application items by entity ID."""

    entities = {}
    for item in application_items:
        entity_id = item["entityId"]
        if entity_id not in entities:
            entities[entity_id] = {
                "entityId": entity_id,
                "normalizedText": item.get("normalizedText", ""),
                "rawEntity": item.get("rawEntity", ""),
                "entityType": item.get("entityType", ""),
                "possibleSanction": item.get("possibleSanction", False),
                "countInCurrentCase": 0,
                "sourceLocations": [],
            }

        entities[entity_id]["countInCurrentCase"] += 1
        entities[entity_id]["sourceLocations"].append(item)

    result = list(entities.values())
    result.sort(
        key=lambda entity: (
            -entity["countInCurrentCase"],
            entity["normalizedText"],
        )
    )
    return result


def _search_candidates(application_id, source_entities):
    """Second query searches all entity text with AUTO fuzziness."""

    search_texts = list(
        dict.fromkeys(
            entity["entitySearchText"]
            for entity in source_entities
            if entity["entitySearchText"]
        )
    )
    if not search_texts:
        return []

    fuzzy_queries = [
        {
            "match": {
                "entitySearchText": {
                    "query": text,
                    "fuzziness": FUZZINESS,
                    "prefix_length": 1,
                    "max_expansions": 25,
                    "operator": "and",
                }
            }
        }
        for text in search_texts
    ]
    query = {
        "bool": {
            "should": fuzzy_queries,
            "minimum_should_match": 1,
            "must_not": [{"term": {"applicationId": application_id}}],
        }
    }
    buckets = _read_all_buckets(
        query=query,
        aggregation_name="candidates",
        group_field="entityId",
        source_fields=[
            "entityId",
            "entitySearchText",
            "normalizedText",
            "rawEntity",
        ],
        extra_aggs={
            "applications": {
                "cardinality": {
                    "field": "applicationId",
                    "precision_threshold": 40_000,
                }
            }
        },
    )

    candidates = []
    for bucket in buckets:
        candidate = _bucket_source(bucket)
        candidate["occurrenceCount"] = bucket["doc_count"]
        candidate["uniqueApplicationIdCount"] = bucket["applications"][
            "value"
        ]
        candidates.append(candidate)
    return candidates


def _calculate_matches(source_entities, candidates, threshold):
    """Calculate which application entity is closest to each candidate."""

    matches = []
    for candidate in candidates:
        candidate_text = _entity_text(candidate)
        source_entity = source_entities[0]
        percentage = _similarity_percentage(
            source_entity["entitySearchText"],
            candidate_text,
        )

        for entity in source_entities[1:]:
            current_percentage = _similarity_percentage(
                entity["entitySearchText"],
                candidate_text,
            )
            if current_percentage > percentage:
                source_entity = entity
                percentage = current_percentage

        if percentage >= threshold:
            matches.append(
                {
                    "sourceEntityId": source_entity["entityId"],
                    "matchedEntityId": candidate["entityId"],
                    "matchPercentage": percentage,
                    "matchType": (
                        "verbatim" if percentage == 100 else "similar"
                    ),
                    "occurrenceCount": candidate["occurrenceCount"],
                }
            )

    matches.sort(
        key=lambda match: (
            -match["matchPercentage"],
            match["matchedEntityId"],
        )
    )
    return matches


def _search_occurrences_by_entity_ids(application_id, entity_ids):
    """Get each matched entity's locations at the same time."""

    if not entity_ids:
        return {}

    if len(entity_ids) == 1:
        entity_id = entity_ids[0]
        return {
            entity_id: _search_entity_occurrences(
                application_id,
                entity_id,
            )
        }

    with ThreadPoolExecutor(
        max_workers=min(MAX_WORKERS, len(entity_ids))
    ) as executor:
        jobs = {
            entity_id: executor.submit(
                _search_entity_occurrences,
                application_id,
                entity_id,
            )
            for entity_id in entity_ids
        }
        return {
            entity_id: job.result()
            for entity_id, job in jobs.items()
        }


def _search_entity_occurrences(application_id, entity_id):
    """Get every location outside the passed application ID."""

    buckets = _read_all_buckets(
        query={
            "bool": {
                "filter": [{"term": {"entityId": entity_id}}],
                "must_not": [
                    {"term": {"applicationId": application_id}}
                ],
            }
        },
        aggregation_name="occurrences",
        group_field="sentenceEntityId",
        source_fields=OCCURRENCE_FIELDS,
    )
    return [_bucket_source(bucket) for bucket in buckets]


def _read_all_buckets(
    query,
    aggregation_name,
    group_field,
    source_fields,
    extra_aggs=None,
):
    """Read all composite pages so the API needs no pagination."""

    buckets = []
    after_key = None
    while True:
        composite = {
            "size": 1_000,
            "sources": [
                {group_field: {"terms": {"field": group_field}}}
            ],
        }
        if after_key:
            composite["after"] = after_key

        inner_aggs = {
            "sample": {
                "top_hits": {
                    "size": 1,
                    "_source": source_fields,
                }
            }
        }
        if extra_aggs:
            inner_aggs.update(extra_aggs)

        response = search_index(
            size=0,
            query=query,
            track_total_hits=False,
            aggs={
                aggregation_name: {
                    "composite": composite,
                    "aggs": inner_aggs,
                }
            },
        )
        result = response["aggregations"][aggregation_name]
        buckets.extend(result["buckets"])
        after_key = result.get("after_key")
        if not after_key:
            return buckets


def _bucket_source(bucket):
    """Get the source item kept in an aggregation bucket."""

    return bucket["sample"]["hits"]["hits"][0]["_source"]


def _similarity_percentage(left, right):
    """Compare two texts and return a percentage."""

    left = _normalize_text(left)
    right = _normalize_text(right)
    if left == right:
        return 100.0
    if not left or not right:
        return 0.0

    distance = _levenshtein_distance(left, right)
    return round((1 - distance / max(len(left), len(right))) * 100, 2)


def _normalize_text(value):
    """Make text lowercase and remove accents before comparing it."""

    plain_text = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in plain_text
        ).split()
    )


def _levenshtein_distance(left, right):
    """Count the smallest number of edits between two texts."""

    previous_row = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current_row = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current_row.append(
                min(
                    current_row[-1] + 1,
                    previous_row[right_index] + 1,
                    previous_row[right_index - 1]
                    + (left_character != right_character),
                )
            )
        previous_row = current_row
    return previous_row[-1]


def _entity_text(entity):
    """Use the first available text from an entity."""

    return (
        entity.get("entitySearchText")
        or entity.get("normalizedText")
        or entity.get("rawEntity")
        or ""
    )
