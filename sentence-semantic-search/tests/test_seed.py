from threading import Barrier, Lock
from types import SimpleNamespace

from sentence_search.seed_service import (
    SeedService,
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


class FakeParallelOpenSearchStore:
    def __init__(self, workers):
        self.catalog_keys = set()
        self.occurrence_ids = []
        self.refresh_calls = 0
        self._workers = workers
        self._first_wave = Barrier(workers)
        self._catalog_calls = 0
        self._active_catalog_calls = 0
        self.max_active_catalog_calls = 0
        self._lock = Lock()

    def recreate_indices(self):
        return None

    def ensure_indices(self):
        return None

    def validate_occurrence_identity(self, sources):
        return None

    def existing_catalog_keys(self, keys):
        return set(keys) & self.catalog_keys

    def bulk_index_catalog(self, sources):
        with self._lock:
            self._catalog_calls += 1
            call_number = self._catalog_calls
            self._active_catalog_calls += 1
            self.max_active_catalog_calls = max(
                self.max_active_catalog_calls,
                self._active_catalog_calls,
            )
        if call_number <= self._workers:
            self._first_wave.wait(timeout=2)
        with self._lock:
            self.catalog_keys.update(source["sentenceKey"] for source in sources)
            self._active_catalog_calls -= 1
        return len(sources)

    def bulk_index_occurrences(self, sources):
        required_keys = {source["sentenceKey"] for source in sources}
        assert required_keys <= self.catalog_keys
        self.occurrence_ids.extend(source["globalId"] for source in sources)
        return len(sources)

    def refresh_indices(self):
        self.refresh_calls += 1


class FakePostgresStore:
    def reset_data(self):
        return None

    def register_sentence_keys(self, sources):
        return {
            "registered": len(sources),
            "newSentenceKeys": list({source["sentenceKey"] for source in sources}),
        }


class FakeParallelMatchService:
    def __init__(self, workers, opensearch):
        self.opensearch = opensearch
        self._workers = workers
        self._first_wave = Barrier(workers)
        self._calls = 0
        self._active_calls = 0
        self.max_active_calls = 0
        self._lock = Lock()

    def match_sentence_key(self, sentence_key, threshold):
        assert sentence_key in self.opensearch.catalog_keys
        assert threshold == 90
        with self._lock:
            self._calls += 1
            call_number = self._calls
            self._active_calls += 1
            self.max_active_calls = max(
                self.max_active_calls,
                self._active_calls,
            )
        if call_number <= self._workers:
            self._first_wave.wait(timeout=2)
        with self._lock:
            self._active_calls -= 1


class FakeSummaryService:
    def __init__(self):
        self.keys = []

    def refresh_affected_applications(self, keys):
        self.keys.extend(keys)
        return {"applicationSummariesRefreshed": 1}


def test_seed_runs_bounded_catalog_and_match_workers(tmp_path):
    workers = 3
    csv_file = tmp_path / "parallel-sentences.csv"
    rows = [
        "A1,T1,Statement,SENT-1,1,Sentence one,false,false,,document,,,Asylee",
        "A1,T1,Statement,SENT-2,2,Sentence two,false,false,,document,,,Asylee",
        "A1,T1,Statement,SENT-3,3,Sentence three,false,false,,document,,,Asylee",
        "A1,T1,Statement,SENT-4,4,Sentence four,false,false,,document,,,Asylee",
        "A1,T1,Statement,SENT-5,5,Sentence five,false,false,,document,,,Asylee",
        "A1,T1,Statement,SENT-6,6,Sentence six,false,false,,document,,,Asylee",
    ]
    csv_file.write_text(
        "applicationId,tspId,sectionName,globalId,sentIdLocal,"
        "sentenceContent,isTracer,isFormLanguage,sentenceKey,"
        "sourceType,createdAt,updatedAt,analysisGroup\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    opensearch = FakeParallelOpenSearchStore(workers)
    match_service = FakeParallelMatchService(workers, opensearch)
    summary_service = FakeSummaryService()
    service = SeedService(
        SimpleNamespace(
            seed_batch_size=2,
            seed_workers=workers,
            match_threshold=90,
        ),
        opensearch,
        FakePostgresStore(),
        match_service,
        summary_service,
    )

    result = service.seed(csv_file, reset=True)

    assert opensearch.occurrence_ids == [f"SENT-{number}" for number in range(1, 7)]
    assert opensearch.max_active_catalog_calls == workers
    assert match_service.max_active_calls == workers
    assert opensearch.refresh_calls == 1
    assert len(summary_service.keys) == 6
    assert result["completedBatches"] == 3
    assert result["completedWaves"] == 1
    assert result["seedBatchSize"] == 2
    assert result["seedWorkers"] == workers
