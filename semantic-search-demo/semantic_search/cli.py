import argparse
import csv
import json
from itertools import batched
from pathlib import Path
from typing import Iterator

from semantic_search.components import store
from semantic_search.config import (
    CATALOG_ALIAS,
    OCCURRENCE_ALIAS,
    SEED_BATCH_SIZE,
    config,
)
from semantic_search.models import EntityOccurrence
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
    values["documentType"] = (
        values["documentType"].strip() or "Written Statement"
    )
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
            raise ValueError(
                "CSV is missing required columns: " + ", ".join(missing)
            )

        batch: list[EntityOccurrence] = []
        for line_number, row in enumerate(reader, start=2):
            batch.append(_record(row, line_number))
            if len(batch) == SEED_BATCH_SIZE:
                yield batch
                batch = []
        if batch:
            yield batch


def seed_from_csv(path: Path) -> dict:
    """Recreate both indexes and load all validated CSV records."""

    if not path.is_file():
        raise ValueError(f"CSV file does not exist: {path}")

    # Validate the complete file and deduplicate text before changing indexes.
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
                    "normalizedText": normalize_text(
                        record.entitySearchText
                    ),
                    "entitySearchText": record.entitySearchText,
                },
            )

    # Recreate both indexes. Only unique catalog texts go through the
    # semantic field, so Titan generates one fresh vector per unique text.
    store.recreate_indices()
    for batch in batched(catalog.values(), SEED_BATCH_SIZE):
        store.bulk_index_catalog(list(batch))

    indexed = 0
    for batch in _csv_batches(path):
        sources = []
        for record in batch:
            source = record.model_dump(mode="json")
            source["semanticKey"] = semantic_key(record.entitySearchText)
            sources.append(source)
        store.bulk_index_occurrences(sources)
        indexed += len(sources)

    totals = {
        "recordsRead": records_read,
        "occurrencesIndexed": indexed,
        "uniqueSemanticTexts": len(catalog),
        "embeddingsGeneratedByOpenSearch": len(catalog),
        "reset": True,
        "semanticField": "entitySearchText",
        "semanticModelId": config.semantic_model_id,
        "occurrenceIndex": OCCURRENCE_ALIAS,
        "semanticCatalogIndex": CATALOG_ALIAS,
    }
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage entity vector data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create the index and alias")
    subparsers.add_parser("reset", help="Delete and recreate the physical index")
    seed_parser = subparsers.add_parser(
        "seed",
        help="Recreate the index, generate fresh vectors, and load a CSV",
    )
    seed_parser.add_argument("path", type=Path)
    args = parser.parse_args()

    store.wait_until_ready()
    if args.command == "init":
        store.ensure_indices()
        result = {"message": "Indexes and aliases are ready"}
    elif args.command == "reset":
        store.recreate_indices()
        result = {"message": "Semantic search indexes were recreated"}
    else:
        result = seed_from_csv(args.path)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
