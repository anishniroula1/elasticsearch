# Move the sentence catalog from HNSW to IVF

This guide keeps the current HNSW catalog available while a new IVF catalog is
built and tested. Do not delete the HNSW index until IVF gives acceptable
counts and response times.

The two model IDs are different:

- OPENSEARCH_SEMANTIC_MODEL_ID is the deployed Titan model used by the ingest
  pipeline to create a 512-number embedding.
- OPENSEARCH_IVF_MODEL_ID is the trained OpenSearch k-NN model that divides
  those embeddings into IVF buckets.

## 1. Train the IVF model from the current catalog

The existing catalog can be the training source because it already contains
unique Titan vectors. The catalog must have representative sentence data.

Check the vector count:

```http
GET /sentence_semantic_catalog-v1/_count
```

Start training. This example reads at most 500,000 existing 512-dimension
vectors. It creates 4,096 buckets and saves 64 as the default number of buckets
searched by each query.

```http
POST /_plugins/_knn/models/sentence-ivf-512-v1/_train
{
  "training_index": "sentence_semantic_catalog-v1",
  "training_field": "sentenceContentVector",
  "dimension": 512,
  "max_training_vector_count": 500000,
  "search_size": 10000,
  "description": "Faiss IVF model for Titan 512 sentence vectors",
  "space_type": "cosinesimil",
  "method": {
    "name": "ivf",
    "engine": "faiss",
    "parameters": {
      "nlist": 4096,
      "nprobes": 64
    }
  }
}
```

Training runs in the background. Check it until state is created:

```http
GET /_plugins/_knn/models/sentence-ivf-512-v1?filter_path=model_id,state,error,dimension,space_type
```

Expected values:

```text
state: created
dimension: 512
space_type: cosinesimil
error: empty
```

This k-NN model does not use the ML Commons deploy or undeploy APIs.

## 2. Configure this API

Put the trained model ID and new physical catalog name in .env:

```text
OPENSEARCH_IVF_MODEL_ID=sentence-ivf-512-v1
OPENSEARCH_IVF_NPROBES=64
OPENSEARCH_CATALOG_INDEX=sentence_semantic_catalog-ivf-v1
OPENSEARCH_CATALOG_ALIAS=sentence_semantic_catalog
```

OPENSEARCH_IVF_NPROBES is a search setting. Change it and restart the API to
compare 32, 64, 128, or 256 without retraining the model.

## 3. Create the empty IVF catalog

Start the API or call:

```http
POST /admin/init
```

The new vector mapping is created from the trained model:

```json
"sentenceContentVector": {
  "type": "knn_vector",
  "model_id": "sentence-ivf-512-v1"
}
```

If the sentence_semantic_catalog alias still points to HNSW, initialization
does not move it. Search continues using HNSW until the alias-switch step.

Check the new mapping:

```http
GET /sentence_semantic_catalog-ivf-v1/_mapping
```

## 4. Copy the existing catalog vectors

Pause catalog seeding before copying. A document added to HNSW after reindexing
but before the alias switch would be missing from IVF.

First confirm that the old document source contains sentenceContentVector. If
it does not, do not use reindex; seed the new catalog through Titan instead.

```http
GET /sentence_semantic_catalog-v1/_search
{
  "size": 1,
  "_source": ["sentenceKey", "sentenceContentVector"],
  "query": {"match_all": {}}
}
```

Start an asynchronous reindex. pipeline=_none prevents the new index's default
Titan pipeline from generating and charging for the same vectors again.

```http
POST /_reindex?wait_for_completion=false
{
  "source": {
    "index": "sentence_semantic_catalog-v1"
  },
  "dest": {
    "index": "sentence_semantic_catalog-ivf-v1",
    "pipeline": "_none"
  }
}
```

Save the returned task ID and check it:

```http
GET /_tasks/<TASK_ID>
```

Wait for completed: true and make sure failures is empty.

## 5. Validate before switching

Counts must match:

```http
GET /sentence_semantic_catalog-v1/_count
GET /sentence_semantic_catalog-ivf-v1/_count
```

The missing-vector count must be zero:

```http
GET /sentence_semantic_catalog-ivf-v1/_count
{
  "query": {
    "bool": {
      "must_not": [
        {"exists": {"field": "sentenceContentVector"}}
      ]
    }
  }
}
```

Run the same sentence vectors directly against both physical indexes. Compare
the number of 90% matches and the response time while testing different
nprobes values. The API uses min_score=0.95 for a 90% cosine threshold.

## 6. Switch the catalog alias

When IVF is ready, move the alias in one atomic request:

```http
POST /_aliases
{
  "actions": [
    {
      "remove": {
        "index": "sentence_semantic_catalog-v1",
        "alias": "sentence_semantic_catalog"
      }
    },
    {
      "add": {
        "index": "sentence_semantic_catalog-ivf-v1",
        "alias": "sentence_semantic_catalog",
        "is_write_index": true
      }
    }
  ]
}
```

New sentence text will still go through the Titan ingest pipeline. Its vector
will then be added to the IVF catalog using the trained bucket model.

## 7. Roll back if needed

Keep the HNSW index during testing. To roll back, reverse the alias:

```http
POST /_aliases
{
  "actions": [
    {
      "remove": {
        "index": "sentence_semantic_catalog-ivf-v1",
        "alias": "sentence_semantic_catalog"
      }
    },
    {
      "add": {
        "index": "sentence_semantic_catalog-v1",
        "alias": "sentence_semantic_catalog",
        "is_write_index": true
      }
    }
  ]
}
```

Delete the old HNSW index only after IVF has passed the match-count, latency,
and ingestion tests.

Official references:

- https://docs.opensearch.org/latest/vector-search/vector-search-techniques/approximate-knn/
- https://docs.opensearch.org/latest/vector-search/api/knn/
- https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/
