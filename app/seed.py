import os
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from elasticsearch import helpers

from app.config import config
from app.search_client import client, ensure_index, recreate_index


ENTITY_CATALOG = [
    ("E001", "VIKOTIC, Vlatko", "vikotic vlatko", "PERSON", True),
    ("E002", "GARCIA, Marvin", "garcia marvin", "PERSON", True),
    ("E003", "Eleanor Roosevelt", "eleanor roosevelt", "PERSON", False),
    ("E004", "Finch D. Star", "finch d star", "PERSON", False),
    ("E005", "John Doe", "john doe", "PERSON", False),
    ("E006", "Translators Worldwide", "translators worldwide", "ORGANIZATION", True),
    ("E007", "United Arab Group", "united arab group", "ORGANIZATION", False),
    ("E008", "Translators Guild", "translators guild", "ORGANIZATION", False),
    ("E009", "James International Law", "james international law", "ORGANIZATION", False),
    ("E010", "Al-Qaeda", "al qaeda", "ORGANIZATION", True),
    ("E011", "AQAP", "aqap", "ORGANIZATION", True),
    ("E012", "Mohammed", "mohammed", "PERSON", False),
    ("E013", "Mohammad", "mohammad", "PERSON", False),
    ("E014", "Muhammad", "muhammad", "PERSON", False),
    ("E015", "Kabul", "kabul", "LOCATION", False),
    ("E016", "Mexico", "mexico", "LOCATION", False),
    ("E017", "Russia", "russia", "LOCATION", False),
    ("E018", "Cuba", "cuba", "LOCATION", False),
    ("E019", "John Preparer", "john preparer", "PERSON", False),
    ("E020", "Andrew M. Smith", "andrew m smith", "PERSON", False),
]

DOCUMENT_TYPES = [
    "Written Statement",
    "I-589 Section B",
    "Supplement B",
    "Interview",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_ROOT = Path(os.getenv("SEED_CSV_ROOT", PROJECT_ROOT))
DEFAULT_FAKE_COUNT = 10_000
CSV_CHUNK_SIZE = 10_000
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


def seed_documents(count=None, csv_path=None, reset=False):
    """Add CSV data when path is given, otherwise create fake data."""

    if count is not None and count < 1:
        raise ValueError("count must be 1 or more")

    # Check the requested CSV before reset has a chance to delete current data.
    csv_file = (
        _find_seed_csv()
        if csv_path is None
        else _csv_file_from_path(csv_path)
    )
    if csv_file:
        _check_csv_columns(csv_file)

    if reset:
        recreate_index()
    else:
        ensure_index()

    timestamp = datetime.now(timezone.utc).isoformat()
    start_id = _next_sentence_entity_id()

    # None is used during app startup so it can find the bundled seed.csv.
    # An empty path from the API means the user wants fake data.
    if csv_file:
        print(f"Seeding from CSV: {csv_file}")
        actions = _csv_actions(csv_file, count, start_id, timestamp)
        source = "csv"
    else:
        fake_count = count or DEFAULT_FAKE_COUNT
        print(f"Generating {fake_count:,} fake records")
        actions = _fake_actions(fake_count, start_id, timestamp)
        source = "fake"

    # Send generated documents to Elasticsearch in batches of 1,000.
    success_count, _ = helpers.bulk(
        client,
        actions,
        chunk_size=1_000,
        request_timeout=120,
    )
    client.indices.refresh(index=config.index_alias)
    return {
        "indexed": success_count,
        "source": source,
        "csvPath": str(csv_file) if csv_file else None,
    }


def _next_sentence_entity_id():
    """Find the next ID so new fake data does not replace old data."""

    # Elasticsearch calculates only the highest ID and does not return documents.
    response = client.search(
        index=config.index_alias,
        size=0,
        aggs={
            "highestId": {
                "max": {
                    "field": "sentenceEntityId",
                }
            }
        },
    )
    highest_id = response["aggregations"]["highestId"]["value"]
    return int(highest_id or 0) + 1


def _find_seed_csv():
    """Find seed.csv or first CSV file in project root."""

    seed_file = CSV_ROOT / "seed.csv"
    if seed_file.exists():
        return seed_file

    csv_files = sorted(CSV_ROOT.glob("*.csv"))
    return csv_files[0] if csv_files else None


def _csv_file_from_path(csv_path):
    """Open a CSV path supplied by the seed API."""

    if not csv_path:
        return None

    csv_file = Path(csv_path).expanduser()
    if not csv_file.is_absolute():
        csv_file = CSV_ROOT / csv_file
    csv_file = csv_file.resolve()

    if csv_file.suffix.lower() != ".csv":
        raise ValueError("csvPath must point to a .csv file")
    if not csv_file.is_file():
        raise ValueError(f"CSV file was not found: {csv_path}")

    return csv_file


def _check_csv_columns(csv_file):
    """Make sure the CSV has all the columns needed by the index."""

    # Reading zero rows lets Pandas check only the header.
    columns = pd.read_csv(
        csv_file,
        encoding="utf-8-sig",
        nrows=0,
    ).columns
    missing_columns = CSV_COLUMNS - set(columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CSV is missing columns: {missing}")


def _csv_actions(csv_file, count, start_id, timestamp):
    """Read CSV rows and change them into Elasticsearch documents."""

    # Pandas reads part of the file at a time, so large CSV files stay manageable.
    chunks = pd.read_csv(
        csv_file,
        chunksize=CSV_CHUNK_SIZE,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )

    loaded_count = 0
    for chunk in chunks:
        # Each DataFrame row becomes one entity occurrence document.
        for row in chunk.to_dict(orient="records"):
            if count and loaded_count >= count:
                return
            if not any(str(value or "").strip() for value in row.values()):
                continue

            row_number = loaded_count + 2
            sentence_entity_id = int(
                _csv_value(
                    row,
                    "sentenceEntityId",
                    start_id + loaded_count,
                )
            )
            application_id = _required_csv_value(
                row,
                "applicationId",
                row_number,
            )
            entity_id = _required_csv_value(row, "entityId", row_number)
            raw_entity = _required_csv_value(row, "rawEntity", row_number)
            normalized_text = _required_csv_value(
                row,
                "normalizedText",
                row_number,
            )
            entity_type = _required_csv_value(
                row,
                "entityType",
                row_number,
            )
            tsp_id = _csv_value(
                row,
                "tspId",
                f"TSP-{application_id}-01",
            )
            global_id = _csv_value(
                row,
                "globalId",
                f"G-{tsp_id}-{sentence_entity_id}",
            )
            begin_offset = int(_csv_value(row, "beginOffset", 0))

            yield {
                "_op_type": "index",
                "_index": config.index_alias,
                "_id": str(sentence_entity_id),
                "_source": {
                    "sentenceEntityId": sentence_entity_id,
                    "applicationId": application_id,
                    "tspId": tsp_id,
                    "globalId": global_id,
                    "entityId": entity_id,
                    "rawEntity": raw_entity,
                    "normalizedText": normalized_text,
                    "entitySearchText": _csv_value(
                        row,
                        "entitySearchText",
                        normalized_text,
                    ),
                    "entityType": entity_type,
                    "possibleSanction": _csv_bool(
                        row.get("possibleSanction")
                    ),
                    "beginOffset": begin_offset,
                    "endOffset": int(
                        _csv_value(
                            row,
                            "endOffset",
                            begin_offset + len(raw_entity),
                        )
                    ),
                    "score": float(_csv_value(row, "score", 1.0)),
                    "source": _csv_value(row, "source", "csv"),
                    "documentType": _csv_value(
                        row,
                        "documentType",
                        "Written Statement",
                    ),
                    "createdAt": _csv_value(row, "createdAt", timestamp),
                    "updatedAt": _csv_value(row, "updatedAt", timestamp),
                },
            }
            loaded_count += 1


def _fake_actions(count, start_id, timestamp):
    """Create fake documents when CSV is not provided."""

    random_values = random.Random(42)
    application_count = max(250, count // 16)

    # Each loop creates one entity occurrence document.
    for offset in range(count):
        sentence_entity_id = start_id + offset
        application_number = random_values.randint(1, application_count)
        application_id = f"A{application_number:09d}"

        # Popular entities create overlap between applications.
        if random_values.random() < 0.72:
            entity = random_values.choice(ENTITY_CATALOG[:10])
        else:
            entity = random_values.choice(ENTITY_CATALOG)

        entity_id, raw, normalized, entity_type, sanction = entity
        tsp_id = f"TSP-{application_id}-{random_values.randint(1, 5):02d}"
        global_id = f"G-{tsp_id}-{random_values.randint(1, 80):04d}"
        begin_offset = random_values.randint(0, 150)

        yield {
            "_op_type": "index",
            "_index": config.index_alias,
            "_id": str(sentence_entity_id),
            "_source": {
                "sentenceEntityId": sentence_entity_id,
                "applicationId": application_id,
                "tspId": tsp_id,
                "globalId": global_id,
                "entityId": entity_id,
                "rawEntity": raw,
                "normalizedText": normalized,
                "entitySearchText": normalized,
                "entityType": entity_type,
                "possibleSanction": sanction,
                "beginOffset": begin_offset,
                "endOffset": begin_offset + len(raw),
                "score": round(random_values.uniform(0.82, 0.999), 3),
                "source": "aws_comprehend",
                "documentType": random_values.choice(DOCUMENT_TYPES),
                "createdAt": timestamp,
                "updatedAt": timestamp,
            },
        }


def _required_csv_value(row, column, row_number):
    """Get required CSV value and show row number when its missing."""

    value = (row.get(column) or "").strip()
    if not value:
        raise ValueError(f"CSV row {row_number} is missing {column}")
    return value


def _csv_value(row, column, default):
    """Get CSV value or use the default when its empty."""

    value = (row.get(column) or "").strip()
    return value or default


def _csv_bool(value):
    """Change common CSV boolean values into true or false."""

    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}
