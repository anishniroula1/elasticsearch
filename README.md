# NER Similarities with Elasticsearch — Local Demo

A small local project for testing NER entity matching and the proposed case UI flow.

The separate `aws-opensearch-lambda` folder contains the AWS Lambda version for
syncing database create, update, and delete events into Amazon OpenSearch Service.
See its own README for deployment and IAM setup.

The project uses:

- Elasticsearch
- FastAPI with Swagger/OpenAPI
- Pandas for chunked CSV reading
- uv for Python versions, dependencies, and the local virtual environment
- Docker Compose for the complete local stack
- Elasticsearch security with an automatically configured local administrator
- A versioned physical index: `ner_entity_occurrences-v1`
- A stable read/write alias: `ner_entity_occurrences`
- Automatic startup seed from the included CSV file
- Batch generation of thousands or millions of fake records

This is a local development demo. It uses simple default credentials that must not be copied to a production environment.

## What the demo supports

- One Elasticsearch document per `sentence_entities` occurrence
- Create, update, and delete event simulation
- Case entity list with occurrence count beside each entity
- Matching cases for a selected entity
- Similar cases ranked by overlapping entities
- Shared-entity details between two cases
- Source locations such as TSP ID, global ID, document type, offsets, and NER score
- Fuzzy entity search for spelling variations
- Swagger documentation for all endpoints

## Project structure

```text
ner-similarities-elasticsearch-demo/
├── aws-opensearch-lambda/   # AWS OpenSearch write Lambda
├── app/
│   ├── main.py
│   ├── models.py
│   ├── pagination.py
│   ├── routes.py
│   ├── search_client.py
│   ├── seed.py
│   ├── services.py
│   └── config.py
├── scripts/
│   └── setup-elasticsearch.sh
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
and `services.py` contains the Elasticsearch queries. The remaining files have
one small responsibility each.

## Requirements

### Docker workflow

- Docker Desktop or Docker Engine
- Docker Compose v2
- `make` and `curl` for the convenience commands

### Local Python workflow

- uv
- Docker for the local Elasticsearch service

Elasticsearch needs enough Docker memory. Allocate at least 2 GB to Docker Desktop.

## Fastest start: run everything in Docker

```bash
make run
```

Wait until the services are healthy, then open:

- Swagger: http://localhost:8000/docs
- Kibana: http://localhost:5601
- API health: http://localhost:8000/health
- Elasticsearch: http://localhost:9200

On the first startup, the API creates the physical index, adds the alias, applies the mapping, and seeds 10,000 documents.

Log in to Kibana with:

```text
Username: admin
Password: admin123
```

Direct Elasticsearch requests also require credentials:

```bash
curl -u admin:admin123 http://localhost:9200
```

If setup stops with HTTP 401 or 403, the Elasticsearch Docker volume probably
has a password from an older run. Reset the local demo data and start again:

```bash
make clean
make run
```

`make clean` deletes the local Elasticsearch data. The API loads it again from
`seed.csv` when the project starts.

The local usernames and passwords are written directly in
`docker-compose.yml` to keep this demo easy to run. They are not production
credentials and must not be reused outside local development.

The Docker image also uses uv. It resolves the dependencies from `pyproject.toml` and creates the project environment inside the image.

## Local development with uv

Start Elasticsearch only:

```bash
make dev-es
```

This also runs the one-time security setup container and then leaves
Elasticsearch running.

Create the local `.venv`, install locked dependencies, and start FastAPI with reload:

```bash
make dev
```

You can also run the commands directly:

```bash
uv sync
docker compose up -d setup
ELASTICSEARCH_URL=http://localhost:9200 \
ELASTICSEARCH_USERNAME=admin \
ELASTICSEARCH_PASSWORD=admin123 \
uv run uvicorn app.main:app --reload
```

`uv sync` automatically creates `.venv` and `uv.lock` when they do not exist. `uv run` uses that environment and keeps it synchronized with the project configuration.

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
make dev-es
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
Elasticsearch in batches, so a request can generate millions of records. The
time needed still depends on Docker memory, CPU, disk speed, and Elasticsearch.

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
whole file into memory before sending the rows to Elasticsearch.

The CSV has the same fields as the fake Elasticsearch documents:

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

### Simulate DB-trigger/Lambda events

```text
POST /events
```

Create example:

```json
{
  "eventType": "SENTENCE_ENTITY_CREATED",
  "sentenceEntityId": 200001,
  "applicationId": "A000000001",
  "tspId": "TSP-A000000001-01",
  "globalId": "G-TSP-A000000001-01-0001",
  "entityId": "E012",
  "rawEntity": "Mohammed",
  "normalizedText": "mohammed",
  "entityType": "PERSON",
  "possibleSanction": false,
  "beginOffset": 10,
  "endOffset": 18,
  "score": 0.98,
  "source": "aws_comprehend",
  "documentType": "Written Statement"
}
```

Use the same payload with `SENTENCE_ENTITY_UPDATED` to replace the document. Use this delete payload:

```json
{
  "eventType": "SENTENCE_ENTITY_DELETED",
  "sentenceEntityId": 200001
}
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

This can return close candidates such as `mohammed`, `mohammad`, or `muhammad`, with an Elasticsearch relevance score.

## Suggested demo flow

1. Start the project with `make run`.
2. Open Swagger.
3. Call `GET /stats` and confirm 10,000 documents.
4. Call `GET /applications/A000000001/entities`.
5. Copy an `entityId` and call its matching-cases endpoint.
6. Call the similar-cases endpoint.
7. Copy another application ID and call shared-entities.
8. Try `/entities/fuzzy?text=mohamad`.
9. Use `/events` to create, update, and delete one occurrence.

## Notes about counts

No pre-calculated `countInCase` is stored in each Elasticsearch document.

- The case entity count is calculated from occurrence documents.
- The number of other matching cases is calculated from distinct `applicationId` values.
- Similar-case ranking is calculated from overlapping `entityId` values.

## Reset everything

```bash
make clean
make run
```

`make clean` removes the Elasticsearch Docker volume, so the index and sample data are recreated on the next run.

## Recreate this project

Run the generator from any directory:

```bash
python generate_project.py
```

It creates a new `ner-similarities-elasticsearch-demo` folder with the same
application files, Docker setup, documentation, and CSV seed data.

To copy the complete current project folder, run:

```bash
python decode_project.py
```

This uses the folder containing `decode_project.py` as the source and creates a
sibling folder ending in `-decoded`. It copies everything, including nested and
hidden files. It stops if the output folder already exists.

You can also pass both the source project path and exact output path:

```bash
python decode_project.py "/path/to/project" "/path/to/project-copy"
```

The output must be outside the source folder so the copy cannot include itself.
