import csv
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Lock

from semantic_search import seed_service
from semantic_search.text import semantic_key


class FakeSeedStore:
    def __init__(self, workers):
        self.catalog_keys = set()
        self.occurrence_ids = []
        self.refresh_calls = 0
        self.recreate_calls = 0
        self.ensure_calls = 0
        self._workers = workers
        self._first_wave = Barrier(workers)
        self._catalog_calls = 0
        self._active_catalog_calls = 0
        self.max_active_catalog_calls = 0
        self._lock = Lock()

    def recreate_indices(self):
        self.recreate_calls += 1
        self.catalog_keys.clear()
        self.occurrence_ids.clear()

    def ensure_indices(self):
        self.ensure_calls += 1

    def existing_catalog_keys(self, semantic_keys):
        return set(semantic_keys) & self.catalog_keys

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


def test_seed_parallelizes_catalog_and_commits_occurrences_in_original_csv_order(
    monkeypatch,
):
    workers = 3
    monkeypatch.setattr(
        seed_service,
        "config",
        replace(
            seed_service.config,
            seed_batch_size=5,
            seed_workers=workers,
        ),
    )
    seed_path = Path(__file__).resolve().parents[1] / "data/seed.csv"
    target_store = FakeSeedStore(workers)

    result = seed_service.seed_from_csv(
        seed_path,
        target_store=target_store,
    )

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
    assert result["catalogDocumentsReused"] == 0
    assert result["seedWorkers"] == workers
    assert result["reset"] is True
    assert target_store.recreate_calls == 1
    assert target_store.ensure_calls == 0


def test_seed_without_reset_reuses_catalog_and_upserts_occurrences(monkeypatch):
    monkeypatch.setattr(
        seed_service,
        "config",
        replace(seed_service.config, seed_batch_size=5, seed_workers=1),
    )
    seed_path = Path(__file__).resolve().parents[1] / "data/seed.csv"
    target_store = FakeSeedStore(workers=1)
    target_store.catalog_keys.add(semantic_key("jack x"))

    result = seed_service.seed_from_csv(
        seed_path,
        target_store=target_store,
        reset=False,
    )

    assert result["reset"] is False
    assert result["catalogDocumentsReused"] == 1
    assert result["catalogDocumentsIndexed"] == 3
    assert result["embeddingsGeneratedByOpenSearch"] == 3
    assert target_store.recreate_calls == 0
    assert target_store.ensure_calls == 1
    assert len(target_store.catalog_keys) == 4
    assert target_store.occurrence_ids == list(range(1, 27))


def test_seed_preserves_unordered_csv_row_order(monkeypatch, tmp_path):
    monkeypatch.setattr(
        seed_service,
        "config",
        replace(seed_service.config, seed_batch_size=3, seed_workers=1),
    )
    seed_path = Path(__file__).resolve().parents[1] / "data/seed.csv"
    unordered_path = tmp_path / "unordered.csv"
    with seed_path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        rows = list(reader)
        fieldnames = reader.fieldnames
    with unordered_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(reversed(rows))

    target_store = FakeSeedStore(workers=1)
    result = seed_service.seed_from_csv(
        unordered_path,
        target_store=target_store,
        reset=False,
    )

    expected_ids = list(reversed(range(1, 27)))
    assert target_store.occurrence_ids == expected_ids
    assert result["recordsRead"] == len(expected_ids)
