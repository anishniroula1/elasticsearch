import csv
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Lock

from semantic_search import cli


class FakeSeedStore:
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
        self.catalog_keys.clear()
        self.occurrence_ids.clear()

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
            self.catalog_keys.update(source["semanticKey"] for source in sources)
            self._active_catalog_calls -= 1

    def bulk_index_occurrences(self, sources):
        required_keys = {source["semanticKey"] for source in sources}
        assert required_keys <= self.catalog_keys
        self.occurrence_ids.extend(source["sentenceEntityId"] for source in sources)

    def refresh_indices(self):
        self.refresh_calls += 1


def test_seed_parallelizes_catalog_and_commits_occurrences_in_csv_order(
    monkeypatch,
):
    workers = 3
    monkeypatch.setattr(
        cli,
        "config",
        replace(cli.config, seed_batch_size=5, seed_workers=workers),
    )
    seed_path = Path(__file__).resolve().parents[1] / "data/seed.csv"
    target_store = FakeSeedStore(workers)

    result = cli.seed_from_csv(seed_path, target_store=target_store)

    with seed_path.open(newline="", encoding="utf-8-sig") as input_file:
        expected_ids = [
            int(row["sentenceEntityId"]) for row in csv.DictReader(input_file)
        ]

    assert target_store.occurrence_ids == expected_ids
    assert target_store.max_active_catalog_calls == workers
    assert target_store.refresh_calls == 1
    assert result["recordsRead"] == len(expected_ids)
    assert result["occurrencesIndexed"] == len(expected_ids)
    assert result["catalogDocumentsIndexed"] == len(target_store.catalog_keys)
    assert result["seedWorkers"] == workers
