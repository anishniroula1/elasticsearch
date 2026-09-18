from sentence_search.text import normalize_sentence, sentence_key


def test_normalize_sentence_keeps_punctuation():
    assert normalize_sentence("  The  U.S. Government  ") == ("the u.s. government")


def test_sentence_key_ignores_case_and_extra_spaces():
    assert sentence_key("Government of the United States") == sentence_key(
        "  government   OF the united states "
    )


def test_sentence_key_does_not_remove_punctuation():
    assert sentence_key("Let's eat, Grandma.") != sentence_key("Let's eat Grandma.")
