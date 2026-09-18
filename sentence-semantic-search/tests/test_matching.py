from sentence_search.postgres_store import PostgresStore, complete_match_lists


class ConnectionThatMustNotRun:
    def execute(self, statement):
        raise AssertionError("Duplicate batch should fail before PostgreSQL query")


class ExistingSentenceConnection:
    def execute(self, statement):
        return self

    def mappings(self):
        return self

    def __iter__(self):
        return iter(
            [
                {
                    "globalId": "1",
                    "applicationId": "A1",
                    "tspId": "T1",
                    "sectionName": "Affidavit",
                    "analysisGroup": "Asylee",
                    "matchStatus": "completed",
                }
            ]
        )


def test_connected_group_is_saved_on_every_global_id():
    lists = complete_match_lists({"1", "10", "40", "78"})

    assert lists["1"] == ["10", "40", "78"]
    assert lists["10"] == ["1", "40", "78"]
    assert lists["40"] == ["1", "10", "78"]
    assert lists["78"] == ["1", "10", "40"]


def test_same_global_id_cannot_have_different_metadata_in_one_batch():
    first = _record()
    second = _record()
    second["sectionName"] = "B1"

    try:
        PostgresStore._validate_sentence_identity(
            ConnectionThatMustNotRun(),
            [first, second],
        )
    except ValueError as error:
        assert "different metadata" in str(error)
    else:
        raise AssertionError("Expected duplicate globalId validation error")


def test_global_id_cannot_change_matching_metadata():
    record = _record()
    record["applicationId"] = "A2"

    try:
        PostgresStore._validate_sentence_identity(
            ExistingSentenceConnection(),
            [record],
        )
    except ValueError as error:
        assert "cannot change matching metadata" in str(error)
    else:
        raise AssertionError("Expected matching metadata validation error")


def _record() -> dict:
    return {
        "globalId": "1",
        "applicationId": "A1",
        "tspId": "T1",
        "sectionName": "Affidavit",
        "analysisGroup": "Asylee",
        "isTracer": False,
        "isFormLanguage": False,
    }
