from types import SimpleNamespace

import pytest

from semantic_search.paginated_summary_service import (
    SEMANTIC_MATCH_PAGE_SIZE,
    PaginatedSemanticSummaryService,
)


def _entity(number: int, *, matches: bool = True):
    return {
        "entityId": f"E{number:04d}",
        "normalizedText": f"entity {number}",
        "countInCurrentCase": 1,
        "exactMatchCount": 1 if matches else 0,
        "similarMatchCount": 2 if matches else 0,
    }


def _install_composite_pages(monkeypatch, service, entities, calls):
    def summary_page(
        application_id,
        threshold,
        *,
        after_key,
        size,
    ):
        calls.append({"afterKey": after_key, "size": size})
        start = 0
        if after_key:
            start = int(after_key["entityId"][1:]) + 1
        page = entities[start : start + size]
        end = start + len(page)
        next_after_key = None
        if page and end < len(entities):
            next_after_key = {"entityId": page[-1]["entityId"]}
        return page, next_after_key

    monkeypatch.setattr(
        service.utils,
        "application_entity_summary_page",
        summary_page,
    )


def test_first_page_returns_exact_total_and_opensearch_token(monkeypatch):
    service = PaginatedSemanticSummaryService(
        SimpleNamespace(vector_space_type="cosinesimil")
    )
    entities = [
        *(_entity(number) for number in range(205)),
        *(
            _entity(number, matches=False)
            for number in range(205, 209)
        ),
    ]
    calls = []
    _install_composite_pages(monkeypatch, service, entities, calls)

    result = service.application_matches("A1", 90, None)

    assert SEMANTIC_MATCH_PAGE_SIZE == 100
    assert result["totalUniqueEntities"] == 209
    assert result["totalMatchingEntities"] == 205
    assert result["returnedEntities"] == 100
    assert result["nextToken"]
    assert result["pagination"] == {
        "page": 1,
        "pageSize": 100,
        "totalPages": 3,
        "hasPreviousPage": False,
        "hasNextPage": True,
    }
    assert calls == [
        {"afterKey": None, "size": 100},
        {"afterKey": {"entityId": "E0099"}, "size": 1_000},
    ]


def test_next_token_resumes_without_recomputing_total(monkeypatch):
    service = PaginatedSemanticSummaryService(
        SimpleNamespace(vector_space_type="cosinesimil")
    )
    entities = [_entity(number) for number in range(205)]
    calls = []
    _install_composite_pages(monkeypatch, service, entities, calls)
    first_page = service.application_matches("A1", 90, None)
    calls.clear()

    second_page = service.application_matches(
        "A1",
        90,
        first_page["nextToken"],
    )

    assert second_page["totalMatchingEntities"] == 205
    assert second_page["returnedEntities"] == 100
    assert second_page["pagination"]["page"] == 2
    assert second_page["pagination"]["hasNextPage"] is True
    assert calls == [
        {"afterKey": {"entityId": "E0099"}, "size": 100}
    ]


def test_next_token_is_bound_to_application_and_threshold(monkeypatch):
    service = PaginatedSemanticSummaryService(
        SimpleNamespace(vector_space_type="cosinesimil")
    )
    entities = [_entity(number) for number in range(101)]
    _install_composite_pages(monkeypatch, service, entities, [])
    first_page = service.application_matches("A1", 90, None)

    with pytest.raises(ValueError, match="different application"):
        service.application_matches("A2", 90, first_page["nextToken"])
    with pytest.raises(ValueError, match="threshold"):
        service.application_matches("A1", 80, first_page["nextToken"])
