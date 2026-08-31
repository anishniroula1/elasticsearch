"""Find fuzzy entity text matches for one application."""

import unicodedata
from concurrent.futures import ThreadPoolExecutor

from app.search_client import search_index
from app.services import get_application_entities


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
    """Show fuzzy match counts for every entity in one application."""

    application_entities = get_application_entities(application_id)
    if not application_entities:
        return {
            "applicationId": application_id,
            "totalUniqueEntities": 0,
            "entities": [],
        }

    source_entities = [
        {
            "entityId": entity["entityId"],
            "entitySearchText": _application_entity_text(entity),
        }
        for entity in application_entities
    ]
    matches = _find_matches(
        application_id,
        source_entities,
        threshold,
    )
    counts = {
        entity["entityId"]: {"verbatim": 0, "similar": 0}
        for entity in source_entities
    }
    for match in matches:
        counts[match["sourceEntityId"]][match["matchType"]] += match[
            "occurrenceCount"
        ]

    entities = []
    for entity in application_entities:
        entity_counts = counts[entity["entityId"]]
        entities.append(
            {
                **entity,
                "verbatimMatchCount": entity_counts["verbatim"],
                "similarMatchCount": entity_counts["similar"],
            }
        )

    return {
        "applicationId": application_id,
        "totalUniqueEntities": len(entities),
        "entities": entities,
    }


def find_fuzzy_matches_by_text(application_id, text, threshold):
    """Find all fuzzy entity matches for one search text."""

    source_entities = [
        {
            "entityId": "searched-text",
            "entitySearchText": text,
        }
    ]
    entity_matches = _find_matches(
        application_id,
        source_entities,
        threshold,
    )
    match_by_entity_id = {
        match["matchedEntityId"]: match
        for match in entity_matches
    }
    locations_by_entity_id = _search_occurrences_by_entity_ids(
        application_id,
        list(match_by_entity_id),
    )

    # Keep one match per entity and put every location under it.
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


def _find_matches(application_id, source_entities, threshold):
    """Get fuzzy candidates and keep the ones above the threshold."""

    if not source_entities:
        return []

    if len(source_entities) == 1:
        return _find_matches_for_entity(
            application_id,
            source_entities[0],
            threshold,
        )

    # Each application entity can be searched separately, so run a few at the
    # same time instead of waiting for every AWS request one by one.
    with ThreadPoolExecutor(
        max_workers=min(MAX_WORKERS, len(source_entities))
    ) as executor:
        jobs = [
            executor.submit(
                _find_matches_for_entity,
                application_id,
                source_entity,
                threshold,
            )
            for source_entity in source_entities
        ]
        matches = [
            match
            for job in jobs
            for match in job.result()
        ]

    matches.sort(
        key=lambda item: (
            item["sourceEntityId"],
            -item["matchPercentage"],
            item["matchedEntityId"],
        )
    )
    return matches


def _find_matches_for_entity(application_id, source_entity, threshold):
    """Find matches for one entity text."""

    candidates = _search_candidates(
        application_id,
        source_entity["entitySearchText"],
    )
    matches = []
    # OpenSearch finds possible matches. We check the complete text here.
    for candidate in candidates:
        percentage = _similarity_percentage(
            source_entity["entitySearchText"],
            _entity_text(candidate),
        )
        if percentage < threshold:
            continue

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
        key=lambda item: (
            -item["matchPercentage"],
            item["matchedEntityId"],
        )
    )
    return matches


def _search_candidates(application_id, search_text):
    """Ask OpenSearch for fuzzy candidates for one text."""

    fuzzy_query = {
        "match": {
            "entitySearchText": {
                "query": search_text,
                "fuzziness": FUZZINESS,
                "prefix_length": 1,
                "max_expansions": 25,
                "operator": "and",
            }
        }
    }

    query = {
        "bool": {
            "must": [fuzzy_query],
            "must_not": [{"term": {"applicationId": application_id}}],
        }
    }
    candidate_list = []
    after_key = None

    # Read every unique candidate internally so the API needs no pagination.
    while True:
        composite = {
            "size": 1_000,
            "sources": [
                {"entityId": {"terms": {"field": "entityId"}}}
            ],
        }
        if after_key:
            composite["after"] = after_key

        response = search_index(
            size=0,
            query=query,
            track_total_hits=False,
            aggs={
                "candidates": {
                    "composite": composite,
                    "aggs": {
                        "sample": {
                            "top_hits": {
                                "size": 1,
                                "_source": [
                                    "entityId",
                                    "entitySearchText",
                                    "normalizedText",
                                    "rawEntity",
                                ],
                            }
                        }
                    },
                }
            },
        )
        candidate_buckets = response["aggregations"]["candidates"]
        for bucket in candidate_buckets["buckets"]:
            candidate = bucket["sample"]["hits"]["hits"][0]["_source"]
            candidate["occurrenceCount"] = bucket["doc_count"]
            candidate_list.append(candidate)
        after_key = candidate_buckets.get("after_key")
        if not after_key:
            return candidate_list


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
    """Get every location for one entity outside the passed application."""

    query = {
        "bool": {
            "filter": [{"term": {"entityId": entity_id}}],
            "must_not": [{"term": {"applicationId": application_id}}],
        }
    }
    occurrences = []
    after_key = None

    # Fuzzy matching is already done. Read every location for this entity.
    while True:
        composite = {
            "size": 1_000,
            "sources": [
                {
                    "sentenceEntityId": {
                        "terms": {"field": "sentenceEntityId"}
                    }
                }
            ],
        }
        if after_key:
            composite["after"] = after_key

        response = search_index(
            size=0,
            query=query,
            track_total_hits=False,
            aggs={
                "occurrences": {
                    "composite": composite,
                    "aggs": {
                        "location": {
                            "top_hits": {
                                "size": 1,
                                "_source": OCCURRENCE_FIELDS,
                            }
                        }
                    },
                }
            },
        )
        result = response["aggregations"]["occurrences"]
        occurrences.extend(
            bucket["location"]["hits"]["hits"][0]["_source"]
            for bucket in result["buckets"]
        )
        after_key = result.get("after_key")
        if not after_key:
            return occurrences


def _application_entity_text(entity):
    """Get search text from the full application entity response."""

    if entity["sourceLocations"]:
        return _entity_text(entity["sourceLocations"][0])
    return _entity_text(entity)


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
    """Use the best available text field from an entity document."""

    return (
        entity.get("entitySearchText")
        or entity.get("normalizedText")
        or entity.get("rawEntity")
        or ""
    )
