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
        include_total=False,
    ):
        calls.append(
            {
                "afterKey": after_key,
                "size": size,
                "includeTotal": include_total,
            }
        )
        start = 0
        if after_key:
            start = int(after_key["entityId"][1:]) + 1
        page = entities[start : start + size]
        end = start + len(page)
        next_after_key = None
        if page and end < len(entities):
            next_after_key = {"entityId": page[-1]["entityId"]}
        total = len(entities) if include_total else None
        return page, next_after_key, total

    monkeypatch.setattr(
        service.utils,
        "application_entity_summary_page",
        summary_page,
    )


def test_first_page_returns_100_source_entities_and_total(monkeypatch):
    service = PaginatedSemanticSummaryService(
        SimpleNamespace(vector_space_type="cosinesimil")
    )
    entities = [
        _entity(number, matches=number % 3 != 0)
        for number in range(205)
    ]
    calls = []
    _install_composite_pages(monkeypatch, service, entities, calls)

    result = service.application_matches("A1", 90, None)

    assert SEMANTIC_MATCH_PAGE_SIZE == 100
    assert result["totalUniqueEntities"] == 205
    assert "totalMatchingEntities" not in result
    assert result["returnedEntities"] == 100
    assert len(result["entities"]) == 100
    assert result["entities"][0]["exactMatchCount"] == 0
    assert result["entities"][0]["similarMatchCount"] == 0
    assert result["nextToken"]
    assert result["pagination"] == {
        "page": 1,
        "pageSize": 100,
        "totalPages": 3,
        "hasPreviousPage": False,
        "hasNextPage": True,
    }
    assert calls == [
        {
            "afterKey": None,
            "size": 100,
            "includeTotal": True,
        }
    ]


def test_next_token_resumes_one_source_page_without_recount(monkeypatch):
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

    assert second_page["totalUniqueEntities"] == 205
    assert second_page["returnedEntities"] == 100
    assert second_page["entities"][0]["entityId"] == "E0100"
    assert second_page["pagination"]["page"] == 2
    assert second_page["pagination"]["hasNextPage"] is True
    assert calls == [
        {
            "afterKey": {"entityId": "E0099"},
            "size": 100,
            "includeTotal": False,
        }
    ]


def test_last_page_has_no_next_token(monkeypatch):
    service = PaginatedSemanticSummaryService(
        SimpleNamespace(vector_space_type="cosinesimil")
    )
    entities = [_entity(number) for number in range(205)]
    _install_composite_pages(monkeypatch, service, entities, [])
    first = service.application_matches("A1", 90, None)
    second = service.application_matches("A1", 90, first["nextToken"])
    last = service.application_matches("A1", 90, second["nextToken"])

    assert last["returnedEntities"] == 5
    assert last["pagination"]["page"] == 3
    assert last["pagination"]["hasNextPage"] is False
    assert last["nextToken"] is None


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
