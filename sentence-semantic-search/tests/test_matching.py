from sentence_search.postgres_store import add_direct_key_matches


def test_new_match_is_saved_in_both_directions():
    result = add_direct_key_matches(
        {"A": [], "D": []},
        "D",
        ["A"],
    )

    assert result["D"] == ["A"]
    assert result["A"] == ["D"]


def test_direct_key_matching_does_not_create_transitive_matches():
    result = add_direct_key_matches(
        {"A": ["B"], "B": ["A"], "D": []},
        "D",
        ["A"],
    )

    assert "D" not in result["B"]
    assert "B" not in result["D"]


def test_lower_score_old_match_is_removed_from_both_directions():
    result = add_direct_key_matches(
        {"A": ["B", "D"], "B": ["A"], "D": ["A"]},
        "A",
        ["D"],
    )

    assert result["A"] == ["D"]
    assert result["B"] == []
    assert result["D"] == ["A"]


def test_source_key_is_not_added_to_its_own_match_list():
    result = add_direct_key_matches({"A": []}, "A", ["A"])

    assert result == {"A": []}
