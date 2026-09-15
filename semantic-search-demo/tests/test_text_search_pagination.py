import pytest

from semantic_search.pagination import (
    decode_text_search_page_token,
    encode_text_search_page_token,
)


def test_text_search_token_keeps_the_page_state():
    token = encode_text_search_page_token(
        application_id="A1",
        threshold=90,
        query_key="text-key",
        after_key={"entityId": "E0100"},
        total_matches=153,
        returned_matches=100,
    )

    state = decode_text_search_page_token(
        token,
        application_id="A1",
        threshold=90,
        query_key="text-key",
    )

    assert state["afterKey"] == {"entityId": "E0100"}
    assert state["totalMatches"] == 153
    assert state["returnedMatches"] == 100


def test_text_search_token_cannot_be_used_for_different_text():
    token = encode_text_search_page_token(
        application_id="A1",
        threshold=90,
        query_key="first-text-key",
        after_key={"entityId": "E0100"},
        total_matches=153,
        returned_matches=100,
    )

    with pytest.raises(ValueError, match="different search text"):
        decode_text_search_page_token(
            token,
            application_id="A1",
            threshold=90,
            query_key="second-text-key",
        )
