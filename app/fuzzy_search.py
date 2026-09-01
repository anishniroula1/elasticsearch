"""Fuzzy entity searches used by the application APIs."""

import re
import unicodedata

from app.search_client import search_index


FUZZINESS = "AUTO:5,8"
ENTITY_BUCKET_SIZE = 200
SOURCE_LOCATION_SIZE = 100
APPLICATION_COUNT_PRECISION = 1_000

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
    """Find fuzzy matches for every entity in one application."""

    # First query groups this application's rows by entity ID. top_hits keeps
    # the actual rows because they are returned as sourceLocations.
    buckets = _search_application_entities(application_id)
    application_entities = [_application_entity(bucket) for bucket in buckets]
    if not application_entities:
        return {
            "applicationId": application_id,
            "totalUniqueEntities": 0,
            "entities": [],
        }

    # An entity can appear more than once. A set in _build_query_string removes
    # repeated spellings before the second OpenSearch request is made.
    source_entities = [
        {
            "entityId": entity["entityId"],
            "entitySearchText": _entity_text(location),
        }
        for entity in application_entities
        for location in entity["sourceLocations"]
        if _entity_text(location)
    ]

    # Second query searches all unique entity text together and excludes the
    # application that supplied the search values.
    candidates = _search_candidates(application_id, source_entities)
    matches = _calculate_matches(source_entities, candidates, threshold)

    counts = {
        entity["entityId"]: {
            "applications": 0,
            "verbatim": 0,
            "similar": 0,
        }
        for entity in application_entities
    }
    for match in matches:
        entity_counts = counts[match["sourceEntityId"]]
        entity_counts["applications"] += match["uniqueApplicationIdCount"]
        entity_counts[match["matchType"]] += match["occurrenceCount"]

    for entity in application_entities:
        entity_counts = counts[entity["entityId"]]
        entity["matchingOtherCaseCount"] = entity_counts["applications"]
        entity["verbatimMatchCount"] = entity_counts["verbatim"]
        entity["similarMatchCount"] = entity_counts["similar"]

    application_entities.sort(
        key=lambda entity: (
            -entity["countInCurrentCase"],
            entity["normalizedText"],
        )
    )
    return {
        "applicationId": application_id,
        "totalUniqueEntities": len(application_entities),
        "entities": application_entities,
    }


def find_fuzzy_matches_by_text(application_id, text, threshold):
    """Find one text outside the application ID passed in the URL."""

    source_entities = [
        {
            "entityId": "searched-text",
            "entitySearchText": text,
        }
    ]
    candidates = _search_candidates(application_id, source_entities)
    entity_matches = _calculate_matches(source_entities, candidates, threshold)

    # Get all locations for the accepted IDs in one query. This avoids running
    # one OpenSearch request per match and does not need a thread pool.
    locations_by_entity_id = _search_occurrences_by_entity_ids(
        application_id,
        [match["matchedEntityId"] for match in entity_matches],
    )

    matches = []
    for entity_match in entity_matches:
        entity_id = entity_match["matchedEntityId"]
        source_locations = locations_by_entity_id.get(entity_id, [])
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


def _search_application_entities(application_id):
    """Get one bucket per entity ID for the current application."""

    response = search_index(
        size=0,
        track_total_hits=False,
        query={"term": {"applicationId": application_id}},
        aggs={
            "entities": {
                "terms": {
                    "field": "entityId",
                    "size": ENTITY_BUCKET_SIZE,
                },
                "aggs": {
                    "sample": {
                        "top_hits": {
                            "size": SOURCE_LOCATION_SIZE,
                            "_source": OCCURRENCE_FIELDS,
                        }
                    }
                },
            }
        },
    )
    return response["aggregations"]["entities"]["buckets"]


def _application_entity(bucket):
    """Turn one application bucket into the API response shape."""

    source_locations = _bucket_sources(bucket)
    sample = source_locations[0]
    return {
        "entityId": sample["entityId"],
        "normalizedText": sample.get("normalizedText", ""),
        "rawEntity": sample.get("rawEntity", ""),
        "entityType": sample.get("entityType", ""),
        "possibleSanction": sample.get("possibleSanction", False),
        "countInCurrentCase": bucket["doc_count"],
        "sourceLocations": source_locations,
    }


def _search_candidates(application_id, source_entities):
    """Search every unique entity text in one OpenSearch request."""

    query_string = _build_query_string(
        entity["entitySearchText"] for entity in source_entities
    )
    if not query_string:
        return []

    response = search_index(
        size=0,
        track_total_hits=False,
        query={
            "bool": {
                "must": [
                    {
                        "query_string": {
                            "query": query_string,
                            "default_field": "entitySearchText",
                            "fuzziness": FUZZINESS,
                            "fuzzy_max_expansions": 10,
                            "fuzzy_prefix_length": 2,
                        }
                    }
                ],
                "must_not": [
                    {"term": {"applicationId": application_id}}
                ],
            }
        },
        aggs={
            "entities": {
                "terms": {
                    "field": "entityId",
                    "size": ENTITY_BUCKET_SIZE,
                },
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
                    },
                    "applications": {
                        "cardinality": {
                            "field": "applicationId",
                            "precision_threshold": (
                                APPLICATION_COUNT_PRECISION
                            ),
                        }
                    },
                },
            }
        },
    )

    candidates = []
    for bucket in response["aggregations"]["entities"]["buckets"]:
        candidate = _bucket_sources(bucket)[0]
        candidate["occurrenceCount"] = bucket["doc_count"]
        candidate["uniqueApplicationIdCount"] = bucket["applications"][
            "value"
        ]
        candidates.append(candidate)
    return candidates


def _build_query_string(search_texts):
    """Clean duplicate text and build one safe query string."""

    clean_texts = {
        _clean_search_text(text)
        for text in search_texts
        if text
    }
    formatted_terms = []
    for text in sorted(clean_texts):
        if not text:
            continue

        words = text.split()
        if len(words) == 1:
            formatted_terms.append(f"{_escape_query_word(words[0])}~")
            continue

        # Quoted text is an exact phrase in query_string. Fuzz each word and
        # keep the words together with AND so spelling mistakes still match.
        fuzzy_words = " AND ".join(
            f"{_escape_query_word(word)}~" for word in words
        )
        formatted_terms.append(f"({fuzzy_words})")

    return " OR ".join(formatted_terms)


def _clean_search_text(value):
    """Remove artifacts and make repeated spellings look the same."""

    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    plain_text = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    clean_text = re.sub(r"[^\w\s.,'-]", " ", plain_text)
    clean_text = " ".join(clean_text.split())
    return clean_text.strip(" -,.()[]{}")


def _escape_query_word(value):
    """Escape characters that have meaning in a query string."""

    return re.sub(r'([+\-=&|><!(){}\[\]^"~*?:\\/])', r"\\\1", value)


def _calculate_matches(source_entities, candidates, threshold):
    """Keep candidates that pass the real edit-distance percentage."""

    if not source_entities:
        return []

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
                    "uniqueApplicationIdCount": candidate[
                        "uniqueApplicationIdCount"
                    ],
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
    """Get every location for all accepted entity IDs with one query."""

    unique_ids = sorted(set(entity_ids))
    if not unique_ids:
        return {}

    buckets = _read_all_buckets(
        query={
            "bool": {
                "filter": [{"terms": {"entityId": unique_ids}}],
                "must_not": [
                    {"term": {"applicationId": application_id}}
                ],
            }
        },
        aggregation_name="occurrences",
        group_field="sentenceEntityId",
        source_fields=OCCURRENCE_FIELDS,
    )

    locations = {entity_id: [] for entity_id in unique_ids}
    for bucket in buckets:
        source = _bucket_sources(bucket)[0]
        locations[source["entityId"]].append(source)
    return locations


def _read_all_buckets(
    query,
    aggregation_name,
    group_field,
    source_fields,
    page_size=1_000,
):
    """Read all composite pages so the API needs no pagination."""

    buckets = []
    after_key = None
    while True:
        composite = {
            "size": page_size,
            "sources": [
                {group_field: {"terms": {"field": group_field}}}
            ],
        }
        if after_key:
            composite["after"] = after_key

        response = search_index(
            size=0,
            track_total_hits=False,
            query=query,
            aggs={
                aggregation_name: {
                    "composite": composite,
                    "aggs": {
                        "sample": {
                            "top_hits": {
                                "size": 1,
                                "_source": source_fields,
                            }
                        }
                    },
                }
            },
        )
        result = response["aggregations"][aggregation_name]
        buckets.extend(result["buckets"])
        after_key = result.get("after_key")
        if not after_key:
            return buckets


def _bucket_sources(bucket):
    """Get source items stored inside an aggregation bucket."""

    return [hit["_source"] for hit in bucket["sample"]["hits"]["hits"]]


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
        for character in unicodedata.normalize("NFKD", str(value).casefold())
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
