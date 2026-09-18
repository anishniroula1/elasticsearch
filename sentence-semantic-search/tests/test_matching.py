from sentence_search.postgres_store import PostgresStore


class ConnectionThatMustNotRun:
    def execute(self, statement):
        raise AssertionError("Duplicate batch should fail before PostgreSQL query")


class ExistingSentenceConnection:
    def execute(self, statement):
        return self

    def mappings(self):
        return self

    def __iter__(self):
        return [
            {
                "globalId": "SENT-1",
                "applicationId": "A1",
                "sentenceKey": "1" * 64,
                "analysisGroup": "Asylee",
                "isTracer": False,
                "isFormLanguage": False,
            }
        ].__iter__()


def test_edge_always_puts_smaller_global_id_first():
    source = {
        "globalId": "SENT-9",
        "applicationId": "A9",
        "sentenceKey": "9" * 64,
    }
    target = {
        "globalId": "SENT-2",
        "applicationId": "A2",
        "sentenceKey": "2" * 64,
    }
    match = {
        "similarityPercentage": 92.4,
        "matchType": "semantic",
    }

    edge = PostgresStore._edge(source, target, match, "test-model")

    assert edge["sentenceIdLow"] == "SENT-2"
    assert edge["sentenceIdHigh"] == "SENT-9"
    assert edge["similarityPercentage"] == 92.4
    assert edge["matchType"] == "semantic"


def test_same_global_id_cannot_have_two_sentence_keys_in_one_batch():
    records = [
        {
            "globalId": "SENT-1",
            "applicationId": "A1",
            "sentenceKey": "1" * 64,
            "analysisGroup": "Asylee",
            "isTracer": False,
            "isFormLanguage": False,
        },
        {
            "globalId": "SENT-1",
            "applicationId": "A1",
            "sentenceKey": "2" * 64,
            "analysisGroup": "Asylee",
            "isTracer": False,
            "isFormLanguage": False,
        },
    ]

    try:
        PostgresStore._validate_sentence_identity(ConnectionThatMustNotRun(), records)
    except ValueError as error:
        assert "different matching identity values" in str(error)
    else:
        raise AssertionError("Expected duplicate globalId validation error")


def test_global_id_cannot_move_to_another_application():
    records = [
        {
            "globalId": "SENT-1",
            "applicationId": "A2",
            "sentenceKey": "1" * 64,
            "analysisGroup": "Asylee",
            "isTracer": False,
            "isFormLanguage": False,
        }
    ]

    try:
        PostgresStore._validate_sentence_identity(
            ExistingSentenceConnection(),
            records,
        )
    except ValueError as error:
        assert "different application" in str(error)
    else:
        raise AssertionError("Expected application identity validation error")


def test_global_id_cannot_change_matching_eligibility():
    records = [
        {
            "globalId": "SENT-1",
            "applicationId": "A1",
            "sentenceKey": "1" * 64,
            "analysisGroup": "Asylee",
            "isTracer": True,
            "isFormLanguage": False,
        }
    ]

    try:
        PostgresStore._validate_sentence_identity(
            ExistingSentenceConnection(),
            records,
        )
    except ValueError as error:
        assert "cannot change analysisGroup, isTracer, or isFormLanguage" in str(error)
    else:
        raise AssertionError("Expected matching eligibility validation error")
