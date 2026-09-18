import csv
from collections.abc import Iterator
from pathlib import Path

from sentence_search.config import Config
from sentence_search.models import SentenceOccurrence
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.sentence_service import SentenceService

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
    "document_id": "documentId",
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
    values["sentIdLocal"] = int(values["sentIdLocal"])
    values["isTracer"] = _as_bool(values["isTracer"])
    values["isFormLanguage"] = _as_bool(values["isFormLanguage"])
    values["sentenceKey"] = values.get("sentenceKey") or None
    values["createdAt"] = values.get("createdAt") or None
    values["updatedAt"] = values.get("updatedAt") or None
    try:
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
        postgres: PostgresStore,
    ):
        """Save the stores used by CSV seeding."""

        self.config = config
        self.opensearch = opensearch
        self.postgres = postgres

    def seed(
        self,
        path: Path,
        reset: bool,
    ) -> dict:
        """Index the CSV in order and queue one job per new global ID."""

        if not path.is_file():
            raise ValueError(f"CSV file does not exist: {path}")
        if reset:
            self.opensearch.recreate_indices()
            self.postgres.reset_data()
        else:
            self.opensearch.ensure_indices()

        records_read = 0
        occurrences_indexed = 0
        catalog_indexed = 0
        catalog_reused = 0
        jobs_queued = 0
        completed_batches = 0
        known_catalog_keys = set()

        sentence_service = SentenceService(
            self.config,
            self.opensearch,
            self.postgres,
        )
        for batch in csv_batches(path, self.config.seed_batch_size):
            sources = [record.model_dump(mode="json") for record in batch]
            records_read += len(sources)

            # Stop this batch before indexing if a global ID changed its text.
            self.postgres.validate_sentence_identity(sources)
            self.opensearch.validate_occurrence_identity(sources)

            catalog_by_key = {}
            for source in sources:
                key = source["sentenceKey"]
                if key not in known_catalog_keys:
                    catalog_by_key.setdefault(
                        key,
                        sentence_service._catalog_source(source),
                    )

            candidate_keys = list(catalog_by_key)
            existing = self.opensearch.existing_catalog_keys(candidate_keys)
            new_catalog_sources = [
                catalog_by_key[key] for key in candidate_keys if key not in existing
            ]
            catalog_indexed += self.opensearch.bulk_index_catalog(new_catalog_sources)
            catalog_reused += len(existing)
            known_catalog_keys.update(candidate_keys)

            occurrences_indexed += self.opensearch.bulk_index_occurrences(sources)
            # Jobs are inserted only after their vectors are searchable.
            self.opensearch.refresh_indices()
            registration = self.postgres.register_sentences(
                sources,
                self.config.match_threshold,
            )
            jobs_queued += registration["jobsQueued"]
            completed_batches += 1

        return {
            "recordsRead": records_read,
            "occurrencesIndexed": occurrences_indexed,
            "catalogDocumentsIndexed": catalog_indexed,
            "catalogDocumentsReused": catalog_reused,
            "matchJobsQueued": jobs_queued,
            "completedBatches": completed_batches,
            "threshold": self.config.match_threshold,
            "reset": reset,
            "matchProcessing": "background",
        }
