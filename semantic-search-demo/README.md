# Fast AWS OpenSearch Entity Semantic Search

This self-contained project targets an IAM-protected Amazon OpenSearch Service
domain running OpenSearch 3.7 and Amazon Titan Text Embeddings V2.

OpenSearch creates every embedding during seeding. The application never calls
Bedrock directly and the CSV never contains vectors.

## Measured outcome

Using the same application from the previous fuzzy-search test, the complete
summary covered **1,530 unique entities**:

| Search path | Observed time | Result coverage |
| --- | ---: | --- |
| Exact matching | Under 1 second | Identical normalized entity text |
| Previous RapidFuzz summary | About 20–30 seconds | Character and spelling similarity |
| Semantic vector summary | Under 5 seconds | Exact plus meaning-based similarity |

The semantic flow is approximately 4–6 times faster than the previous complete
fuzzy summary using the reported bounds. It also matches reordered names such
as `Government of United States` and `United States Government` at the tested
90% semantic threshold; the current Levenshtein scorer does not.

The recommendation is to proceed with semantic search, retain the exact path,
and retire bulk RapidFuzz matching from the main summary. See the
[final performance report](SEMANTIC_SEARCH_PERFORMANCE_REPORT.md) for the
side-by-side evidence, limitations, and final approval decision. The measured
semantic result used the final 512-dimensional configuration.

For the full ML Commons lifecycle through a Lasso proxy, including creation,
testing, connector updates, and cleanup, see
[OPENSEARCH_TITAN_LASSO_RUNBOOK.md](OPENSEARCH_TITAN_LASSO_RUNBOOK.md).
For the AWS console and direct-Bedrock alternatives, see
[MODEL_ID_SETUP.md](MODEL_ID_SETUP.md).

## Architecture

The data is split into two indexes, using these default names:

| Index | Purpose |
| --- | --- |
| `ner_entity_occurrences-v1` | Every entity occurrence and source location; no vectors |
| `ner_entity_semantic_catalog-v1` | One semantic document and vector per unique normalized `entitySearchText` |

This avoids putting the same 512-dimension vector on every repeated occurrence.
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
OPENSEARCH_INGEST_PIPELINE=my_bedrock_embedding_pipeline

OPENSEARCH_INDEX=ner_entity_occurrences-v1
OPENSEARCH_ALIAS=ner_entity_occurrences
OPENSEARCH_CATALOG_INDEX=ner_entity_semantic_catalog-v1
OPENSEARCH_CATALOG_ALIAS=ner_entity_semantic_catalog

INDEX_SHARDS=1
INDEX_REPLICAS=1
SEED_BATCH_SIZE=100
SEED_WORKERS=4
```

The model ID is the opaque OpenSearch model ID, not the Bedrock foundation
model ID `amazon.titan-embed-text-v2:0`.

The ingest pipeline must already exist in OpenSearch and contain a
`text_embedding` processor with this exact field map:

```json
"field_map": {
  "entitySearchText": "entitySearchTextVector"
}
```

The standard AWS credential chain is used. An AWS deployment should use its IAM
role. Set `IAM_ACCESS_ROLE` only when the application must assume a separate
OpenSearch access role. For an administration command, an existing CLI profile
can be selected:

```bash
AWS_PROFILE=my-profile make seed
```

## Seeding flow

```text
CSV
  -> validate all occurrence records
  -> normalize and deduplicate entitySearchText
  -> reset=true: recreate both indexes
  -> reset=false: preserve both indexes and find existing catalog keys
  -> send only required catalog documents through the text_embedding pipeline
  -> generate catalog embeddings in bounded parallel batches
  -> after each catalog batch succeeds, index its occurrence batch
  -> preserve the original CSV row order
  -> refresh both indexes once after the seed completes
```

`SEED_WORKERS` controls how many catalog batches can generate Titan embeddings
concurrently. Start with `4`; reduce it if the Bedrock connector is throttled, or
increase it carefully up to `16` if the domain and Bedrock quota have capacity.
`SEED_BATCH_SIZE` controls the documents in each batch.

Each catalog batch gets at most 10 total attempts. Transient proxy, connection,
HTTP 408/429, and HTTP 5xx failures wait five seconds before retrying. A
successful attempt resets the counter for the next batch. Permanent 4xx errors,
including invalid mappings or vector dimensions, stop immediately.

While a seed is running, `GET /stats` shows its durable progress. The
`occurrenceDocuments` count is the reliable contiguous CSV checkpoint because
an occurrence batch is written only after its required catalog documents have
succeeded. The catalog count can be ahead by at most the small worker window.
OpenSearch's normal refresh interval can make an in-progress count lag briefly;
both indexes are explicitly refreshed when seeding completes.

The occurrence mapping preserves the original fields and adds only the internal
`semanticKey` keyword. Its `entitySearchText` is normal text, not a semantic
field. The catalog uses an explicit text field and Titan V2 vector field:

```json
"entitySearchText": {"type": "text"},
"entitySearchTextVector": {
  "type": "knn_vector",
  "dimension": 512,
  "method": {
    "name": "hnsw",
    "space_type": "cosinesimil",
    "engine": "faiss"
  }
}
```

The catalog index sets `index.default_pipeline` to
`OPENSEARCH_INGEST_PIPELINE`. Cosine similarity is configured on the vector
field's HNSW method instead of the unsupported `index.knn.space_type` setting.
The service also validates that an existing catalog uses Faiss, so a Lucene
catalog cannot silently continue serving after this mapping change.

### Migrating an existing catalog to Faiss with 512 dimensions

The vector engine is fixed when a field is created, so do not try to update the
existing mapping in place. Create a new physical catalog index with the Faiss
mapping above, keeping the existing index available until validation and alias
cutover are complete.

Existing 1,024-dimension vectors cannot be copied into this 512-dimension
mapping. Titan must generate a new 512-value embedding from
`entitySearchText`. Do not set the destination pipeline to `_none` during this
dimension migration; the destination's `index.default_pipeline` must run.
Confirm that the old index can return the source text before starting:

```json
GET /ner_entity_semantic_catalog-v1/_search
{
  "size": 1,
  "_source": ["semanticKey", "normalizedText", "entitySearchText"]
}
```

If `entitySearchText` is absent, the old catalog cannot be re-embedded through
the Titan pipeline and must be rebuilt from the source CSV.

```json
POST /_reindex?wait_for_completion=false&slices=auto
{
  "source": {
    "index": "ner_entity_semantic_catalog-v1",
    "_source": ["semanticKey", "normalizedText", "entitySearchText"]
  },
  "dest": {
    "index": "ner_entity_semantic_catalog-v2",
    "op_type": "create"
  }
}
```

The request returns a task ID. Monitor it with `GET /_tasks/<task-id>`, then
compare both `_count` responses and test representative threshold searches.
Pause catalog writes during the final copy and cutover so documents indexed
after the reindex snapshot are not missed. Warm the completed Faiss index:

```text
GET /_plugins/_knn/warmup/ner_entity_semantic_catalog-v2?pretty
```

Atomically move the application alias after validation:

```json
POST /_aliases
{
  "actions": [
    {
      "remove": {
        "index": "ner_entity_semantic_catalog-v1",
        "alias": "ner_entity_semantic_catalog"
      }
    },
    {
      "add": {
        "index": "ner_entity_semantic_catalog-v2",
        "alias": "ner_entity_semantic_catalog",
        "is_write_index": true
      }
    }
  ]
}
```

Finally set `OPENSEARCH_CATALOG_INDEX=ner_entity_semantic_catalog-v2` in
`.env` and restart the API. The occurrence index and its alias do not change.
Keep the old catalog until the new index has been verified in production.

Run:

```bash
make setup
make test
make seed
```

The same seed operation is available in Swagger through:

```text
POST /admin/seed?reset=true&csvPath=data/seed.csv
POST /admin/seed?reset=false&csvPath=data/seed.csv
```

`reset` is required in Swagger. With `reset=true`, the operation validates the
complete CSV and then recreates both indexes before loading the data. With
`reset=false`, it preserves both indexes, reuses catalog embeddings that already
exist, embeds only new semantic texts, and upserts occurrences by
`sentenceEntityId`. In both modes, seeding starts at the first data row and
processes records in their original CSV order. `csvPath` may be absolute or
relative to the semantic project folder.

Use another CSV with:

```bash
make seed SEED_FILE=/absolute/path/entities.csv
```

`make seed` defaults to `SEED_RESET=true`. Preserve existing data with:

```bash
make seed SEED_FILE=/absolute/path/entities.csv SEED_RESET=false
```

## Fast semantic-summary flow

```text
applicationId
  -> load its unique entities from the occurrence index
  -> retrieve their vectors from catalog doc values as compact binary data
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
  "vectorSpaceType": "cosinesimil",
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

## Paginated application entities with outside match counts

```text
GET /applications/{applicationId}/entities/semantic-matches?threshold=90
```

This endpoint pages the application's unique entities in groups of 100. It
runs semantic matching only for those 100 source entities and returns every
entity on the page, including entities whose exact and similar match counts
are both zero. It does not create or use a third index.

The first request calculates `totalUniqueEntities` with an OpenSearch
cardinality aggregation using the maximum supported precision threshold. The
value is fast to calculate but, like every OpenSearch cardinality result, is
an estimate. It is carried in subsequent continuation tokens so later pages
do not repeat the count.

```json
{
  "applicationId": "A000042133",
  "thresholdPercentage": 90,
  "similarityMetric": "cosine",
  "vectorSpaceType": "cosinesimil",
  "queryEmbeddingSource": "semanticCatalog",
  "totalUniqueEntities": 237,
  "returnedEntities": 100,
  "pagination": {
    "page": 1,
    "pageSize": 100,
    "totalPages": 3,
    "hasPreviousPage": false,
    "hasNextPage": true
  },
  "nextToken": "<OPAQUE_TOKEN>",
  "entities": []
}
```

Pass the opaque token unchanged to fetch the next set:

```text
GET /applications/{applicationId}/entities/semantic-matches?threshold=90&nextToken=<NEXT_TOKEN>
```

The token wraps the `after_key` returned by the OpenSearch composite
aggregation. It is tied to the application and threshold, and the server uses
it to resume after the last source entity instead of reading earlier pages
again. Results are in composite `entityId` order, not global match-count order.

For each page the service retrieves up to 100 stored catalog vectors with one
`ids` search. The request disables `_source` and asks OpenSearch 3.7 for the
`semanticKey` and vector through `docvalue_fields`, with the vector in binary
format. The service decodes the little-endian float bytes and uses those
vectors for `_msearch`. This avoids
the vector reconstruction and large JSON-array parsing performed by the old
`mget` `_source` path and does not require reindexing.

The service then sends up to 100 threshold-based vector searches in one
`_msearch` and aggregates outside-application occurrence counts for the
resulting catalog keys. Titan is not called for these stored source entities.
For consistent results, do not seed or delete the indexes while paging.

## Semantic search for one text

```text
GET /applications/{applicationId}/entities/semantic-search?text=acme%20corporation&threshold=90
```

If the supplied text already exists in the catalog, the endpoint reuses its
stored vector. Otherwise, a `neural` query invokes Titan to embed the new query
and searches the explicit `entitySearchTextVector` field. It then loads
matching occurrences in one filtered query.

The response reports the path used as `queryEmbeddingSource`, either
`semanticCatalog` or `titan`.

An `exact` match has the same normalized semantic key. A `similar` match passes
the cosine threshold. For `threshold=90`, cosine similarity must be at least
`0.90`. The OpenSearch minimum score is `0.95` for `cosinesimil`. Responses
report `cosinesimil` as `vectorSpaceType`.

## Index statistics and previews

Get the exact document count for both indexes:

```text
GET /stats
```

Preview unfiltered documents from either index (the default count is 10):

```text
GET /index-documents?index=ner_entity_occurrences&count=10
GET /index-documents?index=ner_entity_semantic_catalog&count=10
```

Swagger displays `index` as a dropdown containing only the configured
occurrence and semantic-catalog aliases. `count` accepts 1 through 100. The
preview uses a plain `match_all` query and returns the full OpenSearch hits.

## API process

The business endpoints are the summary and one-text search. Administrative
endpoints are available at `POST /admin/init` and `POST /admin/seed`. System
endpoints are available at `GET /health`, `GET /stats`, and
`GET /index-documents`.

`POST /admin/init` creates missing indexes and aliases without deleting or
seeding existing data.

Delete both configured indexes, all their data, and both aliases:

```text
DELETE /admin/indexes?confirm=true
```

The confirmation parameter prevents an accidental deletion from Swagger. The
endpoint uses only the exact index and alias names from `.env`; it does not use
wildcards. After deletion, `GET /stats` reports zero documents. Use
`POST /admin/init` to recreate empty indexes or `POST /admin/seed?reset=true`
to recreate and populate them.

Start the application in its AWS runtime with:

```bash
make dev
```

Then open Swagger at [http://localhost:8000/docs](http://localhost:8000/docs).
The development process listens on port `8000` and reloads when Python files
change.

## References

- [Final semantic performance report](SEMANTIC_SEARCH_PERFORMANCE_REPORT.md)
- [OpenSearch Titan ingest-pipeline tutorial](https://docs.opensearch.org/latest/tutorials/vector-search/semantic-search/semantic-search-bedrock-titan/)
- [OpenSearch text embedding processor](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/)
- [OpenSearch k-NN query](https://docs.opensearch.org/latest/query-dsl/specialized/k-nn/index/)
- [OpenSearch Multi-Search API](https://docs.opensearch.org/latest/api-reference/search-apis/multi-search/)
- [OpenSearch vector spaces](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-spaces/)
- [AWS OpenSearch Bedrock integration](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cfn-template-bedrock.html)
