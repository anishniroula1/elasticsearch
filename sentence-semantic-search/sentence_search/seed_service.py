import csv
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sentence_search.config import Config
from sentence_search.models import SentenceOccurrence
from sentence_search.opensearch_store import OpenSearchStore

REQUIRED_COLUMNS = {
    "applicationId",
    "tspId",
    "sectionName",
    "globalId",
    "sentIdLocal",
    "sentenceContent",
    "isTracer",
    "isFormLanguage",
    "sourceType",
    "createdAt",
    "updatedAt",
    "analysisGroup",
}

HEADER_ALIASES = {
    "application_Id": "applicationId",
    "application_id": "applicationId",
    "tsp_id": "tspId",
    "section_name": "sectionName",
    "global_id": "globalId",
    "local_globa_id": "sentIdLocal",
    "local_global_id": "sentIdLocal",
    "sentence_content": "sentenceContent",
    "senetence_content": "sentenceContent",
    "is_tracer": "isTracer",
    "is_form_language": "isFormLanguage",
    "sentence_key": "sentenceKey",
    "source_type": "sourceType",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
    "analysis_group": "analysisGroup",
}


def _as_bool(value: str) -> bool:
    """Convert common CSV boolean values into true or false."""

    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _canonical_headers(fieldnames: list) -> list:
    """Correct the known legacy header spellings."""

    return [HEADER_ALIASES.get(name, name) for name in fieldnames]


def _sentence(row: dict, line_number: int) -> SentenceOccurrence:
    """Convert one canonical CSV row into a validated sentence."""

    values = dict(row)
    try:
        values["globalId"] = int(values["globalId"])
        values["sentIdLocal"] = int(values["sentIdLocal"])
        values["isTracer"] = _as_bool(values["isTracer"])
        values["isFormLanguage"] = _as_bool(values["isFormLanguage"])
        values["sentenceKey"] = values.get("sentenceKey") or None
        values["createdAt"] = values.get("createdAt") or None
        values["updatedAt"] = values.get("updatedAt") or None
        return SentenceOccurrence.model_validate(values)
    except ValueError as error:
        raise ValueError(f"Invalid CSV row {line_number}: {error}") from error


def csv_batches(path: Path, batch_size: int) -> Iterator:
    """Stream validated sentence batches without sorting the CSV."""

    with path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        original_headers = reader.fieldnames or []
        canonical_headers = _canonical_headers(original_headers)
        if len(set(canonical_headers)) != len(canonical_headers):
            raise ValueError("CSV contains duplicate canonical columns")
        missing = sorted(REQUIRED_COLUMNS - set(canonical_headers))
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(missing))

        batch = []
        for line_number, original_row in enumerate(reader, start=2):
            row = {}
            for old_name, new_name in zip(
                original_headers,
                canonical_headers,
                strict=True,
            ):
                row[new_name] = original_row.get(old_name, "")
            batch.append(_sentence(row, line_number))
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


class SeedService:
    def __init__(
        self,
        config: Config,
        opensearch: OpenSearchStore,
    ):
        """Save the stores used by CSV seeding."""

        self.config = config
        self.opensearch = opensearch

    def _complete_wave(self, pending_batches: list) -> dict:
        """Finish one bounded group of catalog and occurrence work.

        Input: up to SEED_WORKERS batches whose Titan work is running.
        Output: counts for records fully saved in this wave.
        """

        occurrence_count = 0
        catalog_count = 0

        # Futures are read in CSV order. This means an occurrence batch is
        # saved only after its own catalog vectors have finished successfully.
        for pending in pending_batches:
            pending["catalogFuture"].result()
            batch_sources = pending["sources"]
            occurrence_count += self.opensearch.bulk_index_occurrences(batch_sources)
            catalog_count += pending["catalogCount"]

        # One refresh makes the entire wave visible. With 16 workers and the
        # default batch size, this replaces 16 separate refresh operations.
        self.opensearch.refresh_indices()
        return {
            "occurrencesIndexed": occurrence_count,
            "catalogDocumentsIndexed": catalog_count,
        }

    def seed(
        self,
        path: Path,
        reset: bool,
    ) -> dict:
        """Index the CSV in order without running sentence matching."""

        if not path.is_file():
            raise ValueError(f"CSV file does not exist: {path}")
        if reset:
            self.opensearch.recreate_indices()
        else:
            self.opensearch.ensure_indices()

        records_read = 0
        occurrences_indexed = 0
        catalog_indexed = 0
        catalog_reused = 0
        completed_batches = 0
        completed_waves = 0
        known_catalog_keys = set()
        pending_batches = []

        with ThreadPoolExecutor(
            max_workers=self.config.seed_workers,
            thread_name_prefix="sentence-seed",
        ) as executor:
            for batch in csv_batches(path, self.config.seed_batch_size):
                sources = [record.model_dump(mode="json") for record in batch]
                records_read += len(sources)

                # Stop this batch before indexing if a global ID changed its text.
                self.opensearch.validate_occurrence_identity(sources)

                catalog_by_key = {}
                for source in sources:
                    key = source["sentenceKey"]
                    if key not in known_catalog_keys:
                        catalog_by_key.setdefault(
                            key,
                            self._catalog_source(source),
                        )

                candidate_keys = list(catalog_by_key)
                existing = self.opensearch.existing_catalog_keys(candidate_keys)
                new_catalog_sources = [
                    catalog_by_key[key] for key in candidate_keys if key not in existing
                ]
                catalog_reused += len(existing)
                known_catalog_keys.update(candidate_keys)

                # Only catalog work runs here. The bounded pending list keeps
                # at most SEED_WORKERS batches in memory at one time.
                catalog_future = executor.submit(
                    self.opensearch.bulk_index_catalog,
                    new_catalog_sources,
                )
                pending_batches.append(
                    {
                        "catalogFuture": catalog_future,
                        "catalogCount": len(new_catalog_sources),
                        "sources": sources,
                    }
                )

                if len(pending_batches) == self.config.seed_workers:
                    result = self._complete_wave(pending_batches)
                    occurrences_indexed += result["occurrencesIndexed"]
                    catalog_indexed += result["catalogDocumentsIndexed"]
                    completed_batches += len(pending_batches)
                    completed_waves += 1
                    pending_batches = []

            if pending_batches:
                result = self._complete_wave(pending_batches)
                occurrences_indexed += result["occurrencesIndexed"]
                catalog_indexed += result["catalogDocumentsIndexed"]
                completed_batches += len(pending_batches)
                completed_waves += 1

        return {
            "recordsRead": records_read,
            "occurrencesIndexed": occurrences_indexed,
            "catalogDocumentsIndexed": catalog_indexed,
            "catalogDocumentsReused": catalog_reused,
            "completedBatches": completed_batches,
            "completedWaves": completed_waves,
            "seedBatchSize": self.config.seed_batch_size,
            "seedWorkers": self.config.seed_workers,
            "reset": reset,
            "matchProcessing": "searchTime",
        }

    @staticmethod
    def _catalog_source(source: dict) -> dict:
        """Keep only fields allowed by the strict catalog mapping."""

        return {
            "sentenceContent": source["sentenceContent"],
            "sentenceKey": source["sentenceKey"],
            "createdAt": source["createdAt"],
            "updatedAt": source["updatedAt"],
        }
