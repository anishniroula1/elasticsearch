# NER Similarities with OpenSearch

A small local project for testing NER entity matching and the proposed case UI flow.

The project uses:

- OpenSearch
- FastAPI with Swagger/OpenAPI
- Pandas for chunked CSV reading
- uv for Python versions, dependencies, and the local virtual environment
- Docker Compose for the complete local stack
- OpenSearch Dashboards for looking at local data
- A versioned physical index: `ner_entity_occurrences-v1`
- A stable read/write alias: `ner_entity_occurrences`
- Automatic startup seed from the included CSV file
- Batch generation of thousands or millions of fake records

The local Docker setup disables the security plugin to keep the demo easy to
run. AWS uses IAM role authentication instead.

## What the demo supports

- One OpenSearch document per `sentence_entities` occurrence
- Case entity list with occurrence count beside each entity
- Matching cases for a selected entity
- Similar cases ranked by overlapping entities
- Shared-entity details between two cases
- Source locations such as TSP ID, global ID, document type, offsets, and NER score
- Fuzzy entity search for spelling variations
- Swagger documentation for all endpoints

## Project structure

```text
ner-similarities-opensearch-demo/
├── app/
│   ├── main.py
│   ├── open_search_client.py
│   ├── pagination.py
│   ├── routes.py
│   ├── search_client.py
│   ├── seed.py
│   ├── services.py
│   └── config.py
├── .python-version
├── Dockerfile
├── docker-compose.yml
├── decode_project.py
├── generate_project.py
├── Makefile
├── pyproject.toml
├── seed.csv                 # optional
└── README.md
```

`main.py` only starts the application. `routes.py` lists the HTTP endpoints,
and `services.py` contains the OpenSearch queries. The remaining files have
one small responsibility each.

## Requirements

### Docker workflow

- Docker Desktop or Docker Engine
- Docker Compose v2
- `make` and `curl` for the convenience commands

### Local Python workflow

- uv
- Docker for the local OpenSearch service

OpenSearch needs enough Docker memory. Allocate at least 2 GB to Docker Desktop.

## Fastest start: run everything in Docker

```bash
make run
```

Wait until the services are healthy, then open:

- Swagger: http://localhost:8000/docs
- OpenSearch Dashboards: http://localhost:5601
- API health: http://localhost:8000/health
- OpenSearch: http://localhost:9200

On the first startup, the API creates the physical index, adds the alias, applies the mapping, and seeds 10,000 documents.

The local OpenSearch container does not require a username or password:

```bash
curl http://localhost:9200
```

If an older search container or volume gets in the way, reset the local
demo data and start again:

```bash
make clean
make run
```

`make clean` deletes the local OpenSearch data. The API loads it again from
`seed.csv` when the project starts.

Disabling the security plugin is only for local development. Do not use this
Docker setting for an AWS or production domain.

The Docker image also uses uv. It resolves the dependencies from `pyproject.toml` and creates the project environment inside the image.

## Local development with uv

Start OpenSearch only:

```bash
make dev-os
```

This leaves the OpenSearch container running while FastAPI runs on your machine.

Create the local `.venv`, install locked dependencies, and start FastAPI with reload:

```bash
make dev
```

You can also run the commands directly:

```bash
uv sync
docker compose up -d opensearch
OPENSEARCH_HOST=localhost \
OPENSEARCH_PORT=9200 \
OPENSEARCH_USE_SSL=false \
OPENSEARCH_VERIFY_CERTS=false \
OPENSEARCH_AUTH_MODE=none \
uv run uvicorn app.main:app --reload
```

`uv sync` automatically creates `.venv` and `uv.lock` when they do not exist. `uv run` uses that environment and keeps it synchronized with the project configuration.

## Connect the API to AWS OpenSearch

Set the API container or process to IAM mode:

```bash
OPENSEARCH_HOST=search-your-domain.us-east-1.es.amazonaws.com
OPENSEARCH_PORT=443
OPENSEARCH_USE_SSL=true
OPENSEARCH_VERIFY_CERTS=true
OPENSEARCH_AUTH_MODE=iam
OPENSEARCH_SERVICE=es
OPENSEARCH_INDEX=ner_entity_occurrences-v1
OPENSEARCH_ALIAS=ner_entity_occurrences
AWS_REGION=us-east-1
IAM_ACCESS_ROLE=arn:aws:iam::123456789012:role/ner-opensearch-access
AUTO_SEED=false
```

These variables are also passed into the API container by
`docker-compose.yml`. You can put them in a local `.env` file before running
`make run`. Local Docker uses `OPENSEARCH_AUTH_MODE=none` when they are not set.

`OPENSEARCH_HOST` can also contain `https://`; the app removes the scheme. The
AWS identity running the API needs permission to call `sts:AssumeRole` for
`IAM_ACCESS_ROLE`. That assumed role also needs access to the OpenSearch domain
and index. Do not pass AWS access keys into the app.

The API creates a boto3 session by assuming the configured role, then passes
its credentials to `AWSV4SignerAuth`. For OpenSearch Serverless, set
`OPENSEARCH_SERVICE=aoss`.

## Dependency management

Add a package:

```bash
uv add package-name
```

Remove a package:

```bash
uv remove package-name
```

Refresh the lockfile:

```bash
make lock
```

Install or refresh the local environment:

```bash
make setup
```

Do not add a `requirements.txt`; dependencies belong in `pyproject.toml`. The first `uv sync` or `uv lock` command creates `uv.lock`.

## Common commands

```bash
make help
make setup
make dev-os
make dev
make run
make status
make logs
make smoke
make seed COUNT=50000
make seed-csv CSV=seed.csv
make seed1m
make stop
make clean
```

## Generate sample data

On an empty index, application startup checks for `seed.csv` in the project
root. If it is not there, startup uses fake data.

Manual seed API calls choose the source from the request:

- Provide `count` without `csvPath` to generate fake data.
- Provide `csvPath` to load a CSV file.
- Use `reset=true` to recreate the index first.
- Use `reset=false` to keep the current index data.

There is no fixed maximum for `count`. The records are generated and sent to
OpenSearch in batches, so a request can generate millions of records. The
time needed still depends on Docker memory, CPU, disk speed, and OpenSearch.

Generate one million fake records and recreate the index first:

```bash
curl -fsS -X POST \
  "http://localhost:8000/admin/seed?count=1000000&reset=true"
```

The same operation is available as:

```bash
make seed1m
```

Generate any amount without deleting current data:

```bash
make seed COUNT=2500000
```

Fake records get new IDs, so `reset=false` adds them after current records.

### Seed a CSV file

Place the CSV anywhere inside this project. Docker mounts the project as
read-only at `/project`, so a new CSV can be used without rebuilding the API.
Pass its path relative to the project root:

```bash
curl -fsS -X POST -G \
  --data-urlencode "csvPath=data/my-entities.csv" \
  --data-urlencode "reset=true" \
  "http://localhost:8000/admin/seed"
```

If `count` is left out, every CSV row is loaded. Add `count` to load only the
first part of a large file:

```bash
curl -fsS -X POST -G \
  --data-urlencode "csvPath=data/my-entities.csv" \
  --data-urlencode "count=1000000" \
  --data-urlencode "reset=false" \
  "http://localhost:8000/admin/seed"
```

The Make command loads all rows unless `LIMIT` is provided:

```bash
make seed-csv CSV=data/my-entities.csv RESET=true
make seed-csv CSV=data/my-entities.csv RESET=false LIMIT=1000000
```

When Docker is used, pass a relative project path such as
`data/my-entities.csv`, not an absolute path from the host computer.

Pandas reads large CSV files in chunks of 10,000 rows. It does not load the
whole file into memory before sending the rows to OpenSearch.

The CSV has the same fields as the fake OpenSearch documents:

```text
sentenceEntityId,applicationId,tspId,globalId,entityId,rawEntity,
normalizedText,entitySearchText,entityType,possibleSanction,
beginOffset,endOffset,score,source,documentType,createdAt,updatedAt
```

The column names are required, but values can be empty. For example, an empty
`documentType` is saved as `Written Statement`. Other empty values also get
simple defaults when possible.

This project includes a small `seed.csv` that can be used for testing.

With `reset=false`, a CSV row having the same `sentenceEntityId` as an existing
document replaces that document. Other current documents remain.

## Index and alias

The API uses:

```text
Physical index: ner_entity_occurrences-v1
Alias:          ner_entity_occurrences
```

The API writes and searches through the alias. A future mapping change can create `ner_entity_occurrences-v2`, backfill it, and switch the alias without changing API code.

## Main API endpoints

### System and setup

```text
GET  /health
GET  /stats
POST /admin/init
POST /admin/seed?count=10000&reset=false
POST /admin/seed?csvPath=seed.csv&reset=true
```

### Case Entities section

```text
GET /applications/{applicationId}/entities
```

This returns the entity list for the case, the count in that case, the number of other matching cases, and source locations.

Example application IDs are generated as `A000000001`, `A000000002`, and so on.

### Click an entity to see matching cases

```text
GET /applications/{applicationId}/entities/{entityId}/matching-cases
```

This endpoint returns matching cases in pages and includes a `nextToken` when more results exist.

### Match all case entities against other applications

```text
GET /applications/{applicationId}/matching-entities
```

This searches with all entities in the selected application and returns every
other matching application. Each result includes the shared entities, the
occurrence count in the searched application, and the occurrence count in the
matching application.

Results are paginated by matching application:

```text
GET /applications/A000000001/matching-entities?size=20
GET /applications/A000000001/matching-entities?size=20&nextToken=...
```

### Rank other cases by matching entity occurrences

```text
GET /applications/{applicationId}/similar-entity-cases
```

`basedOnSimilarEntities` is the number of other applications containing at
least one entity from the searched application. Each `otherCases` item reports
the total number of times all matching entities occur in that other case.

For example, if the searched application contains `Jack X`, `Haiti`, and
`Michael`, and another case contains `Jack X` 20 times, `Haiti` once, and
`Michael` 50 times, its `matchingEntityOccurrenceCount` is `71`.

Cases are ranked from the highest occurrence count to the lowest:

```json
{
  "applicationId": "A000000001",
  "basedOnSimilarEntities": 43,
  "otherCases": [
    {
      "applicationId": "A000000012",
      "matchingEntityOccurrenceCount": 71
    }
  ]
}
```

This endpoint is not paginated; `otherCases` contains all matching applications.

### Similar Cases tab

```text
GET /applications/{applicationId}/similar-cases
```

Cases are ranked by the number of distinct shared entities.

### View Details / Shared Entities modal

```text
GET /applications/{applicationId}/similar-cases/{otherApplicationId}/shared-entities
```

This returns the shared entities, type, occurrence counts in both cases, sanction flag, and document types where the entity was found.

### Fuzzy entity search

```text
GET /entities/fuzzy?text=mohamad
```

This can return close candidates such as `mohammed`, `mohammad`, or `muhammad`, with an OpenSearch relevance score.

### Fuzzy counts for each application entity

```text
GET /applications/{applicationId}/entities/fuzzy-summary?threshold=90
```

This returns the application entities like the normal entity endpoint, with
the verbatim and similar occurrence counts added to each one:

The first OpenSearch query gets every item for the application ID. Before the
fuzzy search, `entitySearchText` values are normalized and put in a set to
remove duplicates. The values are sent in small `bool.should` batches using
`AUTO:5,8`, which avoids OpenSearch's nested-clause limit. Candidate
aggregations use small pages and a lower-memory distinct count so large AWS
indexes do not trip the parent circuit breaker. Percentages and counts are
calculated after the fuzzy responses are returned.

```json
{
  "applicationId": "A000000001",
  "totalUniqueEntities": 2,
  "entities": [
    {
      "entityId": "E020",
      "countInCurrentCase": 2,
      "matchingOtherCaseCount": 44939,
      "verbatimMatchCount": 3000,
      "similarMatchCount": 200,
      "sourceLocations": []
    }
  ]
}
```

These two fuzzy counts are occurrence documents from other applications.
`matchingOtherCaseCount` remains the number of distinct matching applications.

### Fuzzy search one entity text for an application

```text
GET /applications/{applicationId}/entities/fuzzy-search?text=andrew%20smith&threshold=90
```

The application ID is only used as an exclusion. It does not need to exist in
OpenSearch. The API excludes that ID while finding fuzzy candidates and while
loading source locations. Accepted entity locations are loaded concurrently,
and each matching entity contains all its source locations:

```json
{
  "applicationId": "A000000001",
  "searchedText": "andrew smith",
  "thresholdPercentage": 90,
  "totalMatches": 1,
  "totalSourceLocations": 1,
  "matches": [
    {
      "entityId": "E020",
      "matchPercentage": 100.0,
      "matchType": "verbatim",
      "uniqueApplicationIdCount": 1,
      "totalCount": 1,
      "sourceLocations": [
        {
          "applicationId": "A000000010",
          "sentenceEntityId": 609436,
          "tspId": "TSP-A000000010-04",
          "globalId": "G-TSP-A000000010-04-0075",
          "entityId": "E020",
          "rawEntity": "Andrew Smith",
          "normalizedText": "andrew smith",
          "entitySearchText": "andrew smith",
          "entityType": "PERSON",
          "possibleSanction": false,
          "beginOffset": 125,
          "endOffset": 137,
          "score": 0.981,
          "documentType": "Interview"
        }
      ]
    }
  ]
}
```

## Suggested demo flow

1. Start the project with `make run`.
2. Open Swagger.
3. Call `GET /stats` and confirm 10,000 documents.
4. Call `GET /applications/A000000001/entities`.
5. Copy an `entityId` and call its matching-cases endpoint.
6. Call the similar-cases endpoint.
7. Copy another application ID and call shared-entities.
8. Try `/entities/fuzzy?text=mohamad`.
9. Try `/applications/A000000001/entities/fuzzy-summary`.
10. Try the application entity fuzzy-search endpoint with one entity text.

## Notes about counts

No pre-calculated `countInCase` is stored in each OpenSearch document.

- The case entity count is calculated from occurrence documents.
- The number of other matching cases is calculated from distinct `applicationId` values.
- Similar-case ranking is calculated from overlapping `entityId` values.

## Reset everything

```bash
make clean
make run
```

`make clean` removes the OpenSearch Docker volume, so the index and sample data are recreated on the next run.

## Recreate this project

Run the generator from any directory:

```bash
python generate_project.py
```

It creates a new `ner-similarities-opensearch-demo` folder with the same
application files, Docker setup, documentation, and CSV seed data.

To copy the complete current project folder, run:

```bash
python decode_project.py
```

This uses the folder containing `decode_project.py` as the source and creates a
sibling folder ending in `-decoded`. It copies everything, including nested and
hidden files. Running it again updates the existing decoded folder.

You can also pass both the source project path and exact output path:

```bash
python decode_project.py "/path/to/project" "/path/to/project-copy"
```

The output must be outside the source folder so the copy cannot include itself.
