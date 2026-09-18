from sentence_search.seed_service import (
    _canonical_headers,
    csv_batches,
)


def test_legacy_headers_are_corrected():
    assert _canonical_headers(
        ["application_Id", "local_globa_id", "senetence_content"]
    ) == ["applicationId", "sentIdLocal", "sentenceContent"]


def test_sample_csv_loads_without_sorting(tmp_path):
    csv_file = tmp_path / "sentences.csv"
    csv_file.write_text(
        "applicationId,tspId,sectionName,globalId,sentIdLocal,"
        "sentenceContent,isTracer,isFormLanguage,sentenceKey,"
        "sourceType,createdAt,updatedAt,analysisGroup\n"
        "A1,T1,Statement,SENT-9,9,Last sentence,false,false,,document,,,G1\n"
        "A1,T1,Statement,SENT-2,2,Earlier ID,false,false,,document,,,G1\n",
        encoding="utf-8",
    )

    records = []
    for batch in csv_batches(csv_file, 1):
        records.extend(batch)

    assert [record.globalId for record in records] == ["SENT-9", "SENT-2"]
