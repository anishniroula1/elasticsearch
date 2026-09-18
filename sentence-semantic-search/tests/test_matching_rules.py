from sentence_search.matching_rules import is_matchable_sentence


def test_regular_sentence_can_match():
    sentence = {"isTracer": False, "isFormLanguage": False}

    assert is_matchable_sentence(sentence) is True


def test_tracer_and_form_language_cannot_match():
    tracer = {"isTracer": True, "isFormLanguage": False}
    form_language = {"isTracer": False, "isFormLanguage": True}

    assert is_matchable_sentence(tracer) is False
    assert is_matchable_sentence(form_language) is False
