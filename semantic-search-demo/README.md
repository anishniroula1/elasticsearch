# Fast AWS OpenSearch Entity Semantic Search

This self-contained project targets an IAM-protected Amazon OpenSearch Service
domain running OpenSearch 3.1 or later and Amazon Titan Text Embeddings V2.

OpenSearch creates every embedding during seeding. The application never calls
Bedrock directly and the CSV never contains vectors.

For connector and model setup, see
[MODEL_ID_SETUP.md](MODEL_ID_SETUP.md).

## Architecture

The data is split into two indexes, using these default names:

| Index | Purpose |
| --- | --- |
| `ner_entity_occurrences-v1` | Every entity occurrence and source location; no vectors |
| `ner_entity_semantic_catalog-v1` | One semantic document and vector per unique normalized `entitySearchText` |

This avoids putting the same 1,024-dimension vector on every repeated occurrence.
If seven million occurrences contain 100,000 unique entity texts, only 100,000
catalog embeddings and vector graph entries are created.

The AWS-only client behavior remains fixed:

- HTTPS with certificate verification
- IAM SigV4 for Amazon OpenSearch Service (`es`)
- cosine similarity thresholds

Connection, index, shard, replica, and batch values are read from `.env`.

## Configuration

The project includes `.env` and `.env.example`. Update `.env` for the target
AWS environment:

```dotenv
AWS_REGION=us-east-1
IAM_ACCESS_ROLE=
OPENSEARCH_HOST=search-your-domain.us-east-1.es.amazonaws.com
OPENSEARCH_PORT=443
OPENSEARCH_SERVICE=es
OPENSEARCH_SEMANTIC_MODEL_ID=n17yX5cBsaYnPfyOzmQU

OPENSEARCH_INDEX=ner_entity_occurrences-v1
OPENSEARCH_ALIAS=ner_entity_occurrences
OPENSEARCH_CATALOG_INDEX=ner_entity_semantic_catalog-v1
OPENSEARCH_CATALOG_ALIAS=ner_entity_semantic_catalog

INDEX_SHARDS=1
INDEX_REPLICAS=1
SEED_BATCH_SIZE=100
```

The model ID is the opaque OpenSearch model ID, not the Bedrock foundation
model ID `amazon.titan-embed-text-v2:0`.

The standard AWS credential chain is used. An AWS deployment should use its IAM
role. Set `IAM_ACCESS_ROLE` only when the application must assume a separate
OpenSearch access role. For an administration command, an existing CLI profile
can be selected:

```bash
AWS_PROFILE=my-profile make seed
```

## Fresh seeding flow

```text
CSV
  -> validate all occurrence records
  -> normalize and deduplicate entitySearchText
  -> recreate occurrence and catalog indexes
  -> index unique texts into the semantic catalog
  -> OpenSearch creates one Titan embedding per unique text
  -> index ordinary occurrence records with semanticKey references
```

The occurrence mapping preserves the original fields and adds only the internal
`semanticKey` keyword. Its `entitySearchText` is normal text, not a semantic
field. Only the catalog mapping contains:

```json
"entitySearchText": {
  "type": "semantic",
  "model_id": "<OPENSEARCH_SEMANTIC_MODEL_ID>"
}
```

Run:

```bash
make setup
make test
make seed
```

The same fresh seed operation is available in Swagger through:

```text
POST /admin/seed?csvPath=data/seed.csv
```

`csvPath` may be absolute or relative to the semantic project folder. This is
an administrative, destructive operation: it validates the complete CSV and
then recreates both indexes before loading the data and generating embeddings.

Use another CSV with:

```bash
make seed SEED_FILE=/absolute/path/entities.csv
```

Seeding recreates both indexes, so all vectors are generated fresh.

## Fast semantic-summary flow

```text
applicationId
  -> load its unique entities from the occurrence index
  -> fetch their already-generated vectors from the catalog
  -> raw vector _msearch against the small catalog (no Titan calls)
  -> one occurrence aggregation for exact/similar counts
```

Every request performs vector searches but no query embedding inference.

```text
GET /applications/{applicationId}/entities/semantic-summary?threshold=90
```

Example:

```json
{
  "applicationId": "A000042133",
  "thresholdPercentage": 90,
  "similarityMetric": "cosine",
  "queryEmbeddingSource": "semanticCatalog",
  "totalUniqueEntities": 1,
  "entities": [
    {
      "entityId": "E101",
      "rawEntity": "Acme Corporation",
      "normalizedText": "acme corporation",
      "entitySearchText": "acme corporation",
      "entityType": "ORGANIZATION",
      "possibleSanction": false,
      "countInCurrentCase": 2,
      "exactMatchCount": 5,
      "similarMatchCount": 3
    }
  ]
}
```

Counts are matching occurrence documents outside the supplied application.
Source locations are omitted from the summary.

## Semantic search for one text

```text
GET /applications/{applicationId}/entities/semantic-search?text=acme%20corporation&threshold=90
```

If the supplied text already exists in the catalog, the endpoint reuses its
stored vector. Otherwise, the catalog `semantic` query invokes Titan to embed
the new query. It then loads matching occurrences in one filtered query.

The response reports the path used as `queryEmbeddingSource`, either
`semanticCatalog` or `titan`.

An `exact` match has the same normalized semantic key. A `similar` match passes
the cosine threshold. For `threshold=90`, cosine similarity must be at least
`0.90`, which is OpenSearch `cosinesimil` score `0.95`.

## API process

The business endpoints are the summary and one-text search. Administrative
endpoints are available at `POST /admin/init` and `POST /admin/seed`. The system
health endpoint is available at `GET /health`.

`POST /admin/init` creates missing indexes and aliases without deleting or
seeding existing data.

Start the application in its AWS runtime with:

```bash
uv run --locked uvicorn semantic_search.main:app --host 0.0.0.0 --port 8000
```

## References

- [OpenSearch semantic field](https://docs.opensearch.org/latest/mappings/supported-field-types/semantic/)
- [OpenSearch k-NN query](https://docs.opensearch.org/latest/query-dsl/specialized/k-nn/index/)
- [OpenSearch Multi-Search API](https://docs.opensearch.org/latest/api-reference/search-apis/multi-search/)
- [OpenSearch vector spaces](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-spaces/)
- [AWS OpenSearch Bedrock integration](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cfn-template-bedrock.html)
