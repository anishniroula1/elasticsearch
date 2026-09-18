def is_matchable_sentence(sentence: dict) -> bool:
    """Return true only for sentence text allowed in vector matching.

    Input: sentence with isTracer=false and isFormLanguage=false.
    Output: true.
    """

    return not sentence["isTracer"] and not sentence["isFormLanguage"]
