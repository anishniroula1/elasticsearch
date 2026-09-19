import pytest

from sentence_search.search_utils import (
    cosine_percentage,
    decode_page_token,
    encode_page_token,
    minimum_opensearch_score,
    vector_cosine_percentage,
)


def test_ninety_percent_uses_opensearch_cosine_score():
    score = minimum_opensearch_score(90)

    assert score == pytest.approx(0.95)
    assert cosine_percentage(score) == 90.0


def test_application_page_token_round_trip():
    state = {
        "type": "summary",
        "afterGlobalId": "SENT-22",
        "totalSentences": 120,
    }
    token = encode_page_token(state)
    assert decode_page_token(token) == state


def test_invalid_threshold_is_rejected():
    with pytest.raises(ValueError):
        minimum_opensearch_score(0)


def test_saved_vectors_are_converted_to_a_percentage():
    percentage = vector_cosine_percentage(
        [1.0, 0.0],
        [0.9, 0.435889894],
    )

    assert percentage == 90.0
