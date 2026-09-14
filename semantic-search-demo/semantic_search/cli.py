import argparse
import csv
import json
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from semantic_search.config import config
from semantic_search.models import EntityOccurrence
from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.text import normalize_text, semantic_key

CSV_COLUMNS = {
    "sentenceEntityId",
    "applicationId",
    "tspId",
    "globalId",
    "entityId",
    "rawEntity",
    "normalizedText",
    "entitySearchText",
    "entityType",
    "possibleSanction",
    "beginOffset",
    "endOffset",
    "score",
    "source",
    "documentType",
    "createdAt",
    "updatedAt",
}


def _as_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _record(row: dict[str, str], line_number: int) -> EntityOccurrence:
    values = dict(row)
    values["sentenceEntityId"] = int(values["sentenceEntityId"])
    values["beginOffset"] = int(values["beginOffset"])
    values["endOffset"] = int(values["endOffset"])
    values["score"] = float(values["score"])
    values["possibleSanction"] = _as_bool(values["possibleSanction"])
    values["documentType"] = values["documentType"].strip() or "Written Statement"
    try:
        return EntityOccurrence.model_validate(values)
    except ValueError as error:
        raise ValueError(f"Invalid CSV row {line_number}: {error}") from error


def _csv_batches(path: Path) -> Iterator[list[EntityOccurrence]]:
    with path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        actual_columns = set(reader.fieldnames or [])
        missing = sorted(CSV_COLUMNS - actual_columns)
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(missing))

        batch: list[EntityOccurrence] = []
        for line_number, row in enumerate(reader, start=2):
            batch.append(_record(row, line_number))
            if len(batch) == config.seed_batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


PendingSeedBatch = tuple[
    Future[None],
    list[dict[str, Any]],
    int,
]


def _default_store() -> OpenSearchStore:
    from semantic_search.components import store

    return store


def _commit_seed_batch(
    target_store: OpenSearchStore,
    pending_batch: PendingSeedBatch,
) -> tuple[int, int]:
    catalog_future, occurrence_sources, catalog_document_count = pending_batch
    catalog_future.result()
    target_store.bulk_index_occurrences(occurrence_sources)
    return len(occurrence_sources), catalog_document_count


def seed_from_csv(
    path: Path,
    target_store: OpenSearchStore | None = None,
    reset: bool = True,
) -> dict:
    """Load validated CSV records, optionally recreating both indexes."""

    if not path.is_file():
        raise ValueError(f"CSV file does not exist: {path}")
    target_store = target_store or _default_store()

    # Validate the selected rows and deduplicate text before changing indexes.
    catalog: dict[str, dict[str, str]] = {}
    records_read = 0
    for batch in _csv_batches(path):
        records_read += len(batch)
        for record in batch:
            key = semantic_key(record.entitySearchText)
            catalog.setdefault(
                key,
                {
                    "semanticKey": key,
                    "normalizedText": normalize_text(record.entitySearchText),
                    "entitySearchText": record.entitySearchText,
                },
            )

    # Catalog batches run concurrently because OpenSearch must call Titan for
    # every unique text. Occurrence batches are committed in their original CSV
    # order only after the catalog documents needed by that batch have succeeded.
    if reset:
        target_store.recreate_indices()
        existing_catalog_keys: set[str] = set()
    else:
        target_store.ensure_indices()
        existing_catalog_keys = target_store.existing_catalog_keys(list(catalog))
    unique_semantic_texts = len(catalog)
    scheduled_keys = set(existing_catalog_keys)
    pending: deque[PendingSeedBatch] = deque()
    indexed = 0
    catalog_indexed = 0
    completed_batches = 0

    with ThreadPoolExecutor(
        max_workers=config.seed_workers,
        thread_name_prefix="semantic-catalog-seed",
    ) as executor:
        for batch in _csv_batches(path):
            catalog_sources = []
            occurrence_sources = []
            for record in batch:
                key = semantic_key(record.entitySearchText)
                if key not in catalog:
                    raise RuntimeError(
                        "CSV changed after validation; stop and run the "
                        "seed again with a stable file."
                    )
                if key not in scheduled_keys:
                    catalog_sources.append(catalog[key])
                    scheduled_keys.add(key)

                source = record.model_dump(mode="json")
                source["semanticKey"] = key
                occurrence_sources.append(source)

            catalog_future = executor.submit(
                target_store.bulk_index_catalog,
                catalog_sources,
            )
            pending.append(
                (
                    catalog_future,
                    occurrence_sources,
                    len(catalog_sources),
                )
            )

            # Keep only a small bounded window of work ahead. Completing the
            # oldest item preserves a contiguous checkpoint in CSV row order.
            if len(pending) >= config.seed_workers:
                occurrence_count, catalog_count = _commit_seed_batch(
                    target_store,
                    pending.popleft(),
                )
                indexed += occurrence_count
                catalog_indexed += catalog_count
                completed_batches += 1

        while pending:
            occurrence_count, catalog_count = _commit_seed_batch(
                target_store,
                pending.popleft(),
            )
            indexed += occurrence_count
            catalog_indexed += catalog_count
            completed_batches += 1

    if len(scheduled_keys) != unique_semantic_texts:
        raise RuntimeError(
            "CSV changed after validation; not every semantic text was seeded."
        )
    target_store.refresh_indices()

    totals = {
        "recordsRead": records_read,
        "occurrencesIndexed": indexed,
        "catalogDocumentsIndexed": catalog_indexed,
        "catalogDocumentsReused": len(existing_catalog_keys),
        "uniqueSemanticTexts": unique_semantic_texts,
        "embeddingsGeneratedByOpenSearch": catalog_indexed,
        "completedBatches": completed_batches,
        "seedBatchSize": config.seed_batch_size,
        "seedWorkers": config.seed_workers,
        "reset": reset,
        "ingestPipeline": config.ingest_pipeline,
        "semanticTextField": "entitySearchText",
        "semanticVectorField": "entitySearchTextVector",
        "semanticModelId": config.semantic_model_id,
        "occurrenceIndex": config.occurrence_alias,
        "semanticCatalogIndex": config.catalog_alias,
    }
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage entity vector data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create the index and alias")
    subparsers.add_parser("reset", help="Delete and recreate the physical index")
    seed_parser = subparsers.add_parser(
        "seed",
        help="Load a CSV and optionally recreate both indexes first",
    )
    seed_parser.add_argument("path", type=Path)
    seed_parser.add_argument(
        "--reset",
        type=_as_bool,
        required=True,
        metavar="{true,false}",
        help="true recreates both indexes; false preserves and upserts data",
    )
    args = parser.parse_args()

    store = _default_store()
    store.wait_until_ready()
    if args.command == "init":
        store.ensure_indices()
        result = {"message": "Indexes and aliases are ready"}
    elif args.command == "reset":
        store.recreate_indices()
        result = {"message": "Semantic search indexes were recreated"}
    else:
        result = seed_from_csv(
            args.path,
            reset=args.reset,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
