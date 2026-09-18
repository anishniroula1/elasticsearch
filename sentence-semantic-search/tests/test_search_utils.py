import pytest

from sentence_search.search_utils import (
    cosine_percentage,
    decode_id_token,
    encode_id_token,
    minimum_opensearch_score,
)


def test_ninety_percent_uses_faiss_cosine_score():
    score = minimum_opensearch_score(90)

    assert score == pytest.approx(1 / 1.1)
    assert cosine_percentage(score) == 90.0


def test_application_page_token_round_trip():
    token = encode_id_token("SENT-22")
    assert decode_id_token(token) == "SENT-22"


def test_invalid_threshold_is_rejected():
    with pytest.raises(ValueError):
        minimum_opensearch_score(0)
