from semantic_search.text import normalize_text, semantic_key


def test_semantic_key_deduplicates_case_accents_and_punctuation():
    assert normalize_text("  ÁCME, Inc. ") == "acme inc"
    assert semantic_key("ÁCME, Inc.") == semantic_key("acme inc")
