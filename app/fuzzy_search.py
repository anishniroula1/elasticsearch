"""Find fuzzy entity text matches for one application."""

import unicodedata
from concurrent.futures import ThreadPoolExecutor

from rapidfuzz.distance import Levenshtein

from app.search_client import multi_search, search_index


FUZZINESS = "AUTO:5,8"
OCCURRENCE_PAGE_SIZE = 25_000
ENTITY_PAGE_SIZE = 1_000
SUMMARY_CANDIDATE_SIZE = 1_000
MSEARCH_BATCH_SIZE = 300
MSEARCH_MAX_WORKERS = 8
CASE_COUNT_PRECISION = 40_000

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

    # First OpenSearch call gets the current application's entities.
    application_entities, source_entities = _search_application_entities(
        application_id
    )
    if not application_entities:
        return {
            "applicationId": application_id,
            "totalUniqueEntities": 0,
            "entities": [],
        }

    # The second call is one msearch request. It contains the outside-case
    # count and one fuzzy search for each unique entity search text.
    outside_case_counts, candidate_groups = _search_summary_data(
        application_id,
        source_entities,
    )
    counts = {
        entity["entityId"]: {"verbatim": 0, "similar": 0}
        for entity in source_entities
    }
    for source_entity, candidates in zip(
        source_entities,
        candidate_groups,
    ):
        for match in _matches_from_candidates(
            source_entity,
            candidates,
            threshold,
        ):
            counts[match["sourceEntityId"]][match["matchType"]] += match[
                "occurrenceCount"
            ]

    for entity in application_entities:
        entity_counts = counts[entity["entityId"]]
        entity["matchingOtherCaseCount"] = outside_case_counts.get(
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


def _search_application_entities(application_id):
    """Get all application entities without loading source locations."""

    entities = []
    source_entities = []
    after_key = None

    # Composite aggregation lets us read every entity in smaller pages.
    while True:
        composite = {
            "size": ENTITY_PAGE_SIZE,
            "sources": [
                {"entityId": {"terms": {"field": "entityId"}}}
            ],
        }
        if after_key:
            composite["after"] = after_key

        response = search_index(
            size=0,
            track_total_hits=False,
            query={"term": {"applicationId": application_id}},
            aggs={
                "entities": {
                    "composite": composite,
                    "aggs": {
                        # We only need one row to show the entity information
                        # and get its text for the fuzzy search.
                        "sample": {
                            "top_hits": {
                                "size": 1,
                                "_source": [
                                    "entityId",
                                    "normalizedText",
                                    "rawEntity",
                                    "entitySearchText",
                                    "entityType",
                                    "possibleSanction",
                                ],
                            }
                        }
                    },
                },
            }
        )

        result = response["aggregations"]["entities"]
        for bucket in result["buckets"]:
            sample = bucket["sample"]["hits"]["hits"][0]["_source"]
            entity_id = bucket["key"]["entityId"]
            entities.append(
                {
                    "entityId": entity_id,
                    "normalizedText": sample.get("normalizedText", ""),
                    "rawEntity": sample.get("rawEntity", ""),
                    "entityType": sample.get("entityType", ""),
                    "possibleSanction": sample.get(
                        "possibleSanction",
                        False,
                    ),
                    "countInCurrentCase": bucket["doc_count"],
                }
            )
            source_entities.append(
                {
                    "entityId": entity_id,
                    "entitySearchText": _entity_text(sample),
                }
            )

        after_key = result.get("after_key")
        if not after_key or len(result["buckets"]) < ENTITY_PAGE_SIZE:
            break

    entities.sort(
        key=lambda item: (
            -item["countInCurrentCase"],
            item["normalizedText"],
        )
    )
    source_by_id = {item["entityId"]: item for item in source_entities}
    source_entities = [source_by_id[item["entityId"]] for item in entities]
    return entities, source_entities


def _search_summary_data(application_id, source_entities):
    """Get outside counts and fuzzy candidates with threaded msearch calls."""

    entity_ids = [entity["entityId"] for entity in source_entities]
    searches = [
        {
            "size": 0,
            "track_total_hits": False,
            "query": {
                "bool": {
                    "filter": [{"terms": {"entityId": entity_ids}}],
                    "must_not": [
                        {"term": {"applicationId": application_id}}
                    ],
                }
            },
            "aggs": {
                "entities": {
                    "terms": {
                        "field": "entityId",
                        # The query only allows these IDs, so this returns a
                        # bucket for every entity from the first search.
                        "size": len(entity_ids),
                    },
                    "aggs": {
                        "cases": {
                            "cardinality": {
                                "field": "applicationId",
                                "precision_threshold": (
                                    CASE_COUNT_PRECISION
                                ),
                            }
                        }
                    },
                }
            },
        }
    ]
    # Do not search the same text twice when entity IDs share a name.
    unique_search_texts = list(
        dict.fromkeys(
            entity["entitySearchText"] for entity in source_entities
        )
    )
    searches.extend(
        _summary_candidate_search(
            application_id,
            search_text,
        )
        for search_text in unique_search_texts
    )

    responses = _run_msearch_batches(searches)
    count_response = responses[0]
    outside_case_counts = {
        bucket["key"]: bucket["cases"]["value"]
        for bucket in count_response["aggregations"]["entities"]["buckets"]
    }
    candidates_by_text = {
        search_text: _candidates_from_summary_response(response)
        for search_text, response in zip(
            unique_search_texts,
            responses[1:],
        )
    }
    return outside_case_counts, [
        candidates_by_text[entity["entitySearchText"]]
        for entity in source_entities
    ]


def _run_msearch_batches(searches):
    """Send 300 searches per request and run the requests in threads."""

    batches = [
        searches[index : index + MSEARCH_BATCH_SIZE]
        for index in range(0, len(searches), MSEARCH_BATCH_SIZE)
    ]
    worker_count = min(MSEARCH_MAX_WORKERS, len(batches))

    # executor.map keeps results in the same order as the request batches.
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        batch_responses = executor.map(multi_search, batches)
        return [
            response
            for batch_response in batch_responses
            for response in batch_response["responses"]
        ]


def get_application_source_locations(application_id, entity_ids):
    """Get all source locations for the selected application entities."""

    unique_entity_ids = list(
        dict.fromkeys(
            entity_id.strip()
            for entity_id in entity_ids
            if entity_id.strip()
        )
    )
    if not unique_entity_ids:
        return {
            "applicationId": application_id,
            "totalSourceLocations": 0,
            "entities": [],
        }

    locations = {entity_id: [] for entity_id in unique_entity_ids}
    after_key = None

    while True:
        composite = {
            "size": OCCURRENCE_PAGE_SIZE,
            "sources": [
                {"entityId": {"terms": {"field": "entityId"}}},
                {
                    "sentenceEntityId": {
                        "terms": {"field": "sentenceEntityId"}
                    }
                },
            ],
        }
        if after_key:
            composite["after"] = after_key

        # Filter by the application and all requested IDs in one query.
        response = search_index(
            size=0,
            track_total_hits=False,
            query={
                "bool": {
                    "filter": [
                        {"term": {"applicationId": application_id}},
                        {"terms": {"entityId": unique_entity_ids}},
                    ]
                }
            },
            aggs={
                "locations": {
                    "composite": composite,
                    "aggs": {
                        "source": {
                            "top_hits": {
                                "size": 1,
                                "_source": OCCURRENCE_FIELDS,
                            }
                        }
                    },
                }
            },
        )

        result = response["aggregations"]["locations"]
        for bucket in result["buckets"]:
            source = bucket["source"]["hits"]["hits"][0]["_source"]
            locations[source["entityId"]].append(source)

        after_key = result.get("after_key")
        if not after_key or len(result["buckets"]) < OCCURRENCE_PAGE_SIZE:
            break

    entities = [
        {
            "entityId": entity_id,
            "totalSourceLocations": len(locations[entity_id]),
            "sourceLocations": locations[entity_id],
        }
        for entity_id in unique_entity_ids
    ]
    return {
        "applicationId": application_id,
        "totalSourceLocations": sum(
            entity["totalSourceLocations"] for entity in entities
        ),
        "entities": entities,
    }


def _summary_candidate_search(application_id, search_text):
    """Build one fuzzy search used inside msearch."""

    return {
        "size": 0,
        "track_total_hits": False,
        "query": _fuzzy_query(application_id, search_text),
        "aggs": {
            "candidates": {
                "terms": {
                    "field": "entityId",
                    "size": SUMMARY_CANDIDATE_SIZE,
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
                    }
                },
            }
        },
    }


def _candidates_from_summary_response(response):
    """Read fuzzy candidates returned by one msearch item."""

    return [
        _candidate_from_bucket(bucket)
        for bucket in response["aggregations"]["candidates"]["buckets"]
    ]


def find_fuzzy_matches_by_text(application_id, text, threshold):
    """Find all fuzzy entity matches for one search text."""

    source_entity = {
        "entityId": "searched-text",
        "entitySearchText": text,
    }
    entity_matches = _matches_from_candidates(
        source_entity,
        _search_candidates(application_id, text),
        threshold,
    )
    locations_by_entity_id = _search_occurrences_by_entity_ids(
        application_id,
        [match["matchedEntityId"] for match in entity_matches],
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


def _matches_from_candidates(source_entity, candidates, threshold):
    """Calculate the final percentage for fuzzy candidates."""

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
            query=_fuzzy_query(application_id, search_text),
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
        candidate_list.extend(
            _candidate_from_bucket(bucket)
            for bucket in candidate_buckets["buckets"]
        )
        after_key = candidate_buckets.get("after_key")
        if (
            not after_key
            or len(candidate_buckets["buckets"]) < composite["size"]
        ):
            return candidate_list


def _candidate_from_bucket(bucket):
    """Get the candidate text and occurrence count from one bucket."""

    candidate = bucket["sample"]["hits"]["hits"][0]["_source"]
    candidate["occurrenceCount"] = bucket["doc_count"]
    return candidate


def _fuzzy_query(application_id, search_text):
    """Build the fuzzy query shared by normal search and msearch."""

    return {
        "bool": {
            "must": [
                {
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
            ],
            "must_not": [
                {"term": {"applicationId": application_id}}
            ],
        }
    }


def _search_occurrences_by_entity_ids(application_id, entity_ids):
    """Get locations for all matched entity IDs in one search."""

    unique_entity_ids = sorted(set(entity_ids))
    if not unique_entity_ids:
        return {}

    # One terms query replaces one OpenSearch query for every entity ID.
    query = {
        "bool": {
            "filter": [
                {"terms": {"entityId": unique_entity_ids}}
            ],
            "must_not": [{"term": {"applicationId": application_id}}],
        }
    }
    locations = {entity_id: [] for entity_id in unique_entity_ids}
    after_key = None

    # Most requests finish here in one call. Continue only when there are more
    # than 25,000 locations because the API still returns every result.
    while True:
        composite = {
            "size": OCCURRENCE_PAGE_SIZE,
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
        for bucket in result["buckets"]:
            location = bucket["location"]["hits"]["hits"][0][
                "_source"
            ]
            locations[location["entityId"]].append(location)

        after_key = result.get("after_key")
        if not after_key or len(result["buckets"]) < composite["size"]:
            return locations


def _similarity_percentage(left, right):
    """Compare two texts and return a percentage."""

    left = _normalize_text(left)
    right = _normalize_text(right)
    if left == right:
        return 100.0
    if not left or not right:
        return 0.0

    similarity = Levenshtein.normalized_similarity(left, right)
    return round(similarity * 100, 2)


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


def _entity_text(entity):
    """Use the best available text field from an entity document."""

    return (
        entity.get("entitySearchText")
        or entity.get("normalizedText")
        or entity.get("rawEntity")
        or ""
    )
