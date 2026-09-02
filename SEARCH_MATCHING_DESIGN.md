# Entity Matching: Exact, Fuzzy, and Vector Search

## 1. Purpose

This document explains the three main ways we can match NER entities in this
project:

1. Exact matching, with no fuzziness.
2. Fuzzy text matching for spelling differences.
3. Vector matching for semantic similarity.

The current project implements the first two. Vector search is a possible
future addition, but it should solve a specific problem instead of replacing
the existing matching logic.

The short recommendation is:

- Keep exact `entityId` matching for known entities.
- Keep fuzzy and Levenshtein matching for short names and spelling mistakes.
- Add vector search for long names, aliases written with different words,
  multilingual text, or sentence-level meaning.
- For important identity decisions, use vector search to find candidates and
  then verify them with lexical rules, entity type, identifiers, or human
  review.

## 2. NER and entity matching are different jobs

NER finds entity spans in text and assigns a type.

Example input:

```text
Alexander Hamilton met representatives from the International Rescue Committee.
```

Example NER output:

```json
[
  {
    "rawEntity": "Alexander Hamilton",
    "entityType": "PERSON"
  },
  {
    "rawEntity": "International Rescue Committee",
    "entityType": "ORGANIZATION"
  }
]
```

Entity matching happens after extraction. It answers questions such as:

- Is `Alexander Hamilten` the same candidate as `Alexander Hamilton`?
- Does another application contain the same `entityId`?
- Is `International Rescue Comm.` similar to
  `International Rescue Committee`?
- Does a sentence discuss the same person or organization even when it uses
  different wording?

A vector does not replace the NER model. The NER model still extracts the
text, type, offsets, and confidence. Exact, fuzzy, or vector search then helps
find related stored entities.

## 3. Current document model

Each OpenSearch document is one entity occurrence. An entity can therefore
appear in many documents and many applications.

| Field | Purpose | Matching behavior |
| --- | --- | --- |
| `applicationId` | Case/application containing the occurrence | Exact `keyword` |
| `entityId` | Known entity identifier | Exact `keyword` |
| `rawEntity` | Original extracted text | Full text |
| `normalizedText` | Cleaned form of the entity | Exact `keyword` with lowercase/ascii normalization |
| `entitySearchText` | Text used for fuzzy retrieval | Analyzed `text` |
| `entityType` | `PERSON`, `ORGANIZATION`, `LOCATION`, and so on | Exact filter |
| `sentenceEntityId` | Unique occurrence identifier | Exact grouping |
| offsets and document fields | Location of the entity | Returned as source information |

This occurrence-based model is good for answering where an entity appeared.
It is less efficient for vectors because the same entity text may be embedded
thousands of times. Section 10 describes a better vector layout.

## 4. Comparison of matching methods

| Method | Best for | Example | Main limitation |
| --- | --- | --- | --- |
| Exact ID | Confirmed identity | `E031` equals `E031` | Cannot find unknown aliases or typos |
| Exact normalized text | Same text after normalization | `AQAP` equals `aqap` | Different spelling does not match |
| Fuzzy edit distance | Typographical differences | `Hamilten` → `Hamilton` | Does not understand meaning |
| Vector/cosine | Similar meaning or context | long descriptions using different words | May connect semantically related but different entities |
| Hybrid | Production search needing both signals | lexical name plus semantic context | More setup, cost, and tuning |

## 5. Exact matching without fuzzy search

Exact matching is the safest and fastest option when the identifier is
trusted. The current non-fuzzy APIs use `term` and `terms` queries against
`keyword` fields.

### 5.1 Get entities for one application

The application query filters by the exact `applicationId`, then groups the
occurrences by `entityId`:

```json
POST ner_entity_occurrences/_search
{
  "size": 0,
  "query": {
    "term": {
      "applicationId": "A000042133"
    }
  },
  "aggs": {
    "entities": {
      "terms": {
        "field": "entityId",
        "size": 200
      },
      "aggs": {
        "sample": {
          "top_hits": {
            "size": 10
          }
        }
      }
    }
  }
}
```

This gives one bucket per entity. `doc_count` is the number of occurrences in
the current application.

### 5.2 Find the same entities in other applications

After collecting the current entity IDs, one `terms` query finds those exact
IDs in other applications:

```json
POST ner_entity_occurrences/_search
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "terms": {
            "entityId": ["E011", "E031", "E040"]
          }
        }
      ],
      "must_not": [
        {
          "term": {
            "applicationId": "A000042133"
          }
        }
      ]
    }
  },
  "aggs": {
    "entities": {
      "terms": {
        "field": "entityId",
        "size": 200
      },
      "aggs": {
        "cases": {
          "cardinality": {
            "field": "applicationId"
          }
        }
      }
    }
  }
}
```

Use exact matching when:

- `entityId` is assigned by a trusted entity-resolution process.
- False positives are more harmful than missed spelling variations.
- The user explicitly asks for the same known entity.
- Counts must be explainable and deterministic.

Do not use a `match` query when exact identity is required. A `match` query
analyzes text and can match individual tokens. A `term` query checks the exact
stored keyword value.

## 6. Fuzzy matching implemented in this project

The fuzzy implementation has two stages:

1. OpenSearch retrieves a small set of possible candidates.
2. RapidFuzz calculates the final normalized Levenshtein percentage.

This is deliberate. OpenSearch is good at quickly reducing millions of
documents to a candidate set. RapidFuzz gives the API a clear and consistent
percentage for the complete entity text.

### 6.1 OpenSearch candidate query

The current query is equivalent to:

```json
POST ner_entity_occurrences/_search
{
  "size": 0,
  "query": {
    "bool": {
      "must": [
        {
          "match": {
            "entitySearchText": {
              "query": "alexander hamilten",
              "fuzziness": "AUTO:5,8",
              "prefix_length": 1,
              "max_expansions": 25,
              "operator": "and"
            }
          }
        }
      ],
      "must_not": [
        {
          "term": {
            "applicationId": "A000042133"
          }
        }
      ]
    }
  }
}
```

The settings mean:

- `AUTO:5,8` chooses the allowed edit distance from the token length.
- `prefix_length: 1` requires the first character to remain unchanged.
- `max_expansions: 25` limits the number of expanded fuzzy terms.
- `operator: and` requires every analyzed word in a multi-word name.
- `must_not` prevents the current application from matching itself.

OpenSearch fuzziness is candidate retrieval. It is not the percentage shown
by the API.

### 6.2 Final RapidFuzz percentage

Both values are normalized before comparison:

- Convert to lowercase.
- Remove accents.
- Replace punctuation with spaces.
- Collapse repeated spaces.

The code then uses:

```python
from rapidfuzz.distance import Levenshtein

similarity = Levenshtein.normalized_similarity(left, right)
match_percentage = round(similarity * 100, 2)
```

Example:

```text
Input:     alexander hamilten
Candidate: alexander hamilton
Result:    94.44%
Type:      similar
```

A normalized score of `100` is labeled `verbatim`; anything accepted below
`100` is labeled `similar`.

Important detail: in the current code, `verbatim` means equal after
normalization. `AQAP` and `aqap` are therefore `100%`. If the business needs
true character-for-character verbatim matching, compare `rawEntity` before
normalization and expose a separate flag.

### 6.3 Fuzzy summary endpoint

```text
GET /applications/{applicationId}/entities/fuzzy-summary?threshold=90
```

The endpoint makes two HTTP calls to OpenSearch:

1. A normal search gets the current application's entities and source rows.
   Composite aggregation reads additional pages internally if the application
   has more than 1,000 unique entities.
2. An `_msearch` call contains:
   - One search for exact outside-case counts.
   - Query-string fuzzy searches containing batches of up to 50 cleaned,
     unique entity texts.

Duplicate search text is removed before batching. Each multiword name uses an
`AND` group, and the groups use `OR`:

```text
(alexander~ AND hamilten~) OR (united~ AND arab~ AND group~) OR (aqap~)
```

The batch query uses `AUTO:5,8`, `fuzzy_prefix_length: 2`, and
`fuzzy_max_expansions: 10`. A batch holds no more than 50 names or 80 words.
The word limit matters because fuzzy expansion happens per word, not per name.
It prevents long names from recreating the `too_many_nested_clauses` error.

Each batch uses a composite candidate aggregation. The 1,000 value is its page
size, not a result limit. If OpenSearch returns an `after_key`, the service
sends only unfinished batches in another `_msearch` call. The public API has no
pagination and still returns counts based on all candidate buckets.

`_msearch` reduces network round trips. OpenSearch still executes each batch
search inside the multi-search request.

### 6.4 Fuzzy text endpoint

```text
GET /applications/{applicationId}/entities/fuzzy-search?text=alexander%20hamilten&threshold=90
```

The normal path uses two searches:

1. Find candidate `entityId` values for the supplied text.
2. Find all occurrence locations for the accepted IDs with one `terms` query.

The second search groups by `sentenceEntityId` and maps each occurrence back
to its `entityId`. Composite aggregation paging is internal. If the result is
larger than the configured page, more calls are necessary to return every
location.

Example response shape:

```json
{
  "applicationId": "A000042133",
  "searchedText": "alexander hamilten",
  "thresholdPercentage": 90,
  "totalMatches": 2,
  "matches": [
    {
      "entityId": "E032",
      "matchPercentage": 100.0,
      "matchType": "verbatim",
      "uniqueApplicationIdCount": 7749,
      "totalCount": 7977,
      "sourceLocations": []
    },
    {
      "entityId": "E031",
      "matchPercentage": 94.44,
      "matchType": "similar",
      "uniqueApplicationIdCount": 7708,
      "totalCount": 7939,
      "sourceLocations": []
    }
  ]
}
```

### 6.5 Strengths and limits of lexical fuzzy matching

Strengths:

- Good for spelling mistakes, missing characters, and small variations.
- Easy to explain to a user.
- A `90%` threshold has a clear edit-distance meaning.
- No embedding model or inference service is needed.
- Usually the best choice for short person and location names.

Limits:

- It does not understand aliases with different words.
- Word order and added titles can lower the percentage.
- Transliteration can produce large lexical differences.
- Common short names may create false positives.
- Returning every occurrence can dominate response time and payload size even
  when candidate search is fast.

## 7. Do NER entities need vectors?

Usually not for every entity.

Short NER values carry little semantic information. An embedding model has
very little context for values such as `John`, `AQAP`, `Cuba`, or `ABC`. For
these values, exact identifiers, normalized text, edit distance, aliases, and
entity type are usually more reliable.

Vectors become more useful as the input contains more meaning:

| Input | Recommended first method | Why |
| --- | --- | --- |
| `Hamilton` vs `Hamilten` | Fuzzy | One spelling edit |
| `AQAP` vs `aqap` | Exact normalized text | Same normalized value |
| `Intl Rescue Committee` vs `International Rescue Committee` | Hybrid | Abbreviation plus lexical overlap |
| Long organization names with reordered words | Hybrid/vector | More context exists for the model |
| A full sentence describing an organization | Vector/hybrid | Meaning matters more than spelling |
| Cross-language or transliterated names | Multilingual vector plus rules | Character similarity may be low |

For sanctions, fraud, or identity decisions, semantic similarity alone should
not be treated as proof that two people are the same. A model may place two
different politicians, companies, or locations near each other because they
are discussed in similar contexts.

## 8. What a word embedding does

An embedding model converts text into a fixed-length array of numbers:

```text
"international rescue committee"
    -> [0.018, -0.104, 0.227, ..., 0.031]
```

Texts with similar learned meaning tend to point in similar directions in the
vector space. OpenSearch stores these arrays in a `knn_vector` field and uses
k-nearest-neighbor search to retrieve nearby vectors. Amazon OpenSearch
Service supports dense vector fields, approximate k-NN, cosine similarity,
Euclidean distance, and dot product. See the
[AWS vector search overview](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/vector-search.html)
and the
[OpenSearch vector index guide](https://docs.opensearch.org/latest/vector-search/creating-vector-index/).

Embeddings are model-specific. Index and query vectors must come from the
same model and model version, and the mapped dimension must match that model's
output dimension.

## 9. Cosine similarity

Cosine similarity measures the angle between vectors instead of comparing the
original characters:

```text
cosineSimilarity = dot(queryVector, storedVector)
                   / (length(queryVector) * length(storedVector))
```

For cosine distance, OpenSearch uses:

```text
distance = 1 - cosineSimilarity
OpenSearch _score = (2 - distance) / 2
```

Therefore, for a plain cosine k-NN result:

```text
cosineSimilarity = (2 * _score) - 1
```

The formulas and the zero-vector restriction are documented in
[OpenSearch vector spaces](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-spaces/).

Do not call `_score * 100` a match percentage. The score is transformed by
OpenSearch and can also be changed by a hybrid search pipeline. If the UI
needs a vector percentage, calculate cosine similarity from a plain cosine
query and label it clearly:

```python
cosine_similarity = (2 * opensearch_score) - 1
vector_percentage = round(max(0, cosine_similarity) * 100, 2)
```

This display percentage is not equivalent to a Levenshtein percentage. A
Levenshtein threshold of `90` cannot simply become a cosine threshold of
`0.90`. Vector thresholds must be selected from labeled examples.

## 10. Recommended vector data layout

### 10.1 Do not duplicate the same vector unnecessarily

The current occurrence index can contain thousands of rows with the same
`entitySearchText`. Adding a vector to every occurrence is simple, but it
duplicates storage, HNSW graph entries, and embedding work.

For reference, raw float vectors alone require approximately:

| Dimensions | Raw bytes per vector | Raw storage for 20 million vectors |
| --- | ---: | ---: |
| 384 | 1,536 bytes | about 30.7 GB |
| 768 | 3,072 bytes | about 61.4 GB |

Those numbers exclude document fields, index structures, HNSW graph data,
replicas, and operational headroom.

### 10.2 Preferred two-index model

Use two indexes:

```text
entity_catalog
  one row per unique normalized entity text and entity type
  contains the vector

entity_occurrences
  one row per application/document occurrence
  contains applicationId, offsets, document information, and entity key
```

Search flow:

1. Search `entity_catalog` using exact, fuzzy, vector, or hybrid search.
2. Collect accepted entity keys.
3. Search `entity_occurrences` with a `terms` query to return counts and
   locations.

The application can maintain catalog uniqueness even when occurrences arrive
one at a time. Create a deterministic catalog document ID from the normalized
text and entity type, for example:

```text
sha256("PERSON|alexander hamilton")
```

Indexing the same key again updates the same catalog document instead of
creating another vector. This is an application-level identity for the text
variant, not necessarily the final real-world identity of the person.

## 11. Vector setup option A: OpenSearch creates embeddings

This option uses an OpenSearch ML model and a `text_embedding` ingest
processor. On Amazon OpenSearch Service, the model may be local or connected
to a remote service such as Amazon Bedrock, depending on the domain version
and configuration. AWS documents managed semantic search and remote model
connectors in its
[semantic search guide](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/semantic-search.html).

The model must be registered/deployed first. The examples below use
`YOUR_MODEL_ID` and dimension `384`; replace both with the real model values.

### 11.1 Model and AWS prerequisites

Before creating the ingest pipeline:

1. Choose an embedding model and record its output dimension, version, token
   limit, supported languages, and expected request rate.
2. For a remote model such as Amazon Bedrock, create an OpenSearch ML
   connector with the required IAM role and `bedrock:InvokeModel` permission.
3. Register the model with ML Commons.
4. Deploy the model when the selected model type and OpenSearch version require
   deployment.
5. Run a prediction test and confirm that the returned vector has the expected
   dimension.
6. Give the application role permission to call the required OpenSearch index,
   pipeline, search-pipeline, and ML endpoints. Continue signing requests with
   the same AWS SigV4 client used by the application.

Relevant ML Commons endpoints include:

```text
POST /_plugins/_ml/connectors/_create
POST /_plugins/_ml/models/_register
GET  /_plugins/_ml/tasks/{task_id}
POST /_plugins/_ml/models/{model_id}/_deploy
GET  /_plugins/_ml/models/{model_id}
```

The exact connector request depends on the selected model provider. Use the
provider's current connector blueprint instead of copying credentials or an
endpoint from another model. See the official
[connector documentation](https://docs.opensearch.org/latest/ml-commons-plugin/remote-models/connectors/)
and
[Register Model API](https://docs.opensearch.org/latest/ml-commons-plugin/api/model-apis/register-model/).

### 11.2 Create the embedding ingest pipeline

```json
PUT /_ingest/pipeline/entity-embedding-pipeline
{
  "description": "Create an embedding for unique entity text",
  "processors": [
    {
      "text_embedding": {
        "model_id": "YOUR_MODEL_ID",
        "field_map": {
          "entitySearchText": "entityEmbedding"
        },
        "batch_size": 16,
        "skip_existing": true
      }
    }
  ]
}
```

`field_map` identifies the input text and output vector field. The model must
be deployed before ingestion. `skip_existing` can avoid another inference
call when the same document ID and unchanged text already contain a vector.
See the official
[`text_embedding` processor documentation](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/).

Test before backfilling:

```json
POST /_ingest/pipeline/entity-embedding-pipeline/_simulate
{
  "docs": [
    {
      "_index": "entity_catalog-v1",
      "_id": "example-1",
      "_source": {
        "entitySearchText": "international rescue committee"
      }
    }
  ]
}
```

### 11.3 Create the vector index

```json
PUT /entity_catalog-v1
{
  "settings": {
    "index.knn": true,
    "default_pipeline": "entity-embedding-pipeline"
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "entityKey": {
        "type": "keyword"
      },
      "entityId": {
        "type": "keyword"
      },
      "entityType": {
        "type": "keyword"
      },
      "entitySearchText": {
        "type": "text",
        "fields": {
          "exact": {
            "type": "keyword"
          }
        }
      },
      "entityEmbedding": {
        "type": "knn_vector",
        "dimension": 384,
        "method": {
          "name": "hnsw",
          "engine": "lucene",
          "space_type": "cosinesimil",
          "parameters": {
            "m": 16,
            "ef_construction": 100
          }
        }
      }
    }
  }
}
```

The index must have `index.knn: true`. The dimension must match the model.
HNSW provides approximate nearest-neighbor search. OpenSearch documents engine
and method tradeoffs in
[Methods and engines](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/).

For a smaller unique entity catalog with filters, Lucene HNSW is a sensible
starting point. For very large vector counts, test Faiss and available
compression or on-disk modes on the exact AWS OpenSearch version. Engine and
cosine support vary by version, so do not copy a production mapping without
checking the domain version.

### 11.4 Insert one entity

```json
PUT /entity_catalog-v1/_doc/PERSON%7Calexander-hamilton
{
  "entityKey": "PERSON|alexander hamilton",
  "entityId": "E031",
  "entityType": "PERSON",
  "entitySearchText": "alexander hamilton"
}
```

Because the index has a default pipeline, OpenSearch generates and stores
`entityEmbedding` automatically.

### 11.5 Bulk backfill

```text
POST /_bulk?pipeline=entity-embedding-pipeline
{ "index": { "_index": "entity_catalog-v1", "_id": "PERSON|alexander-hamilton" } }
{ "entityKey": "PERSON|alexander hamilton", "entityId": "E031", "entityType": "PERSON", "entitySearchText": "alexander hamilton" }
{ "index": { "_index": "entity_catalog-v1", "_id": "ORG|international-rescue-committee" } }
{ "entityKey": "ORG|international rescue committee", "entityId": "E033", "entityType": "ORGANIZATION", "entitySearchText": "international rescue committee" }
```

Bulk inference must be load-tested and throttled. It consumes model capacity
as well as OpenSearch indexing capacity. OpenSearch provides a batch-ingestion
example using the pipeline parameter in its
[batch ingestion documentation](https://docs.opensearch.org/latest/ml-commons-plugin/remote-models/batch-ingestion/).

### 11.6 Neural query using query text

```json
POST /entity_catalog-v1/_search
{
  "size": 100,
  "_source": {
    "excludes": ["entityEmbedding"]
  },
  "query": {
    "neural": {
      "entityEmbedding": {
        "query_text": "international humanitarian rescue organization",
        "model_id": "YOUR_MODEL_ID",
        "k": 100,
        "filter": {
          "term": {
            "entityType": "ORGANIZATION"
          }
        }
      }
    }
  }
}
```

The model converts `query_text` to a vector, and OpenSearch retrieves the `k`
nearest stored vectors. Filtering by `entityType` is important because a
person, organization, and location can use similar words but should not
normally be identity candidates. Efficient vector filter behavior depends on
the engine and method; see
[filtering vector search results](https://docs.opensearch.org/latest/vector-search/filter-search-knn/index/).

## 12. Vector setup option B: the application creates embeddings

The application can call an embedding service itself, then store the returned
array. In AWS this may be Amazon Bedrock, SageMaker, or another approved model
service.

Advantages:

- The embedding lifecycle is visible in application code.
- Embeddings can be cached before indexing.
- The same service can embed both index and query text.
- OpenSearch does not need an ML connector or inference pipeline.

Disadvantages:

- The application owns batching, retries, throttling, and model-version
  tracking.
- Every write may wait for inference unless a queue handles it asynchronously.

Index example with a pre-generated vector:

```json
PUT /entity_catalog-v1/_doc/example-1
{
  "entityKey": "ORGANIZATION|international rescue committee",
  "entityType": "ORGANIZATION",
  "entitySearchText": "international rescue committee",
  "embeddingModel": "model-name-and-version",
  "entityEmbedding": [0.018, -0.104, 0.227]
}
```

The example vector is shortened for readability. Its real length must equal
the mapped dimension.

Raw k-NN query:

```json
POST /entity_catalog-v1/_search
{
  "size": 100,
  "_source": {
    "excludes": ["entityEmbedding"]
  },
  "query": {
    "knn": {
      "entityEmbedding": {
        "vector": [0.021, -0.098, 0.219],
        "k": 100,
        "filter": {
          "term": {
            "entityType": "ORGANIZATION"
          }
        }
      }
    }
  }
}
```

## 13. Fuzzy matching with vectors and cosine similarity

Vector search itself is not character-level fuzzy search. It is better to
describe it as semantic candidate search.

A safe matching flow is:

1. Normalize the query entity.
2. Check exact `entityId` or exact normalized text.
3. Run lexical fuzzy search and calculate RapidFuzz percentage.
4. If exact/fuzzy confidence is not sufficient, create the query embedding.
5. Run cosine k-NN with an `entityType` filter and a reasonable `k`, such as
   100 or 200.
6. Re-rank vector candidates using business signals:
   - Exact aliases.
   - RapidFuzz percentage.
   - Entity type.
   - Date of birth or registration number when available.
   - Country, address, document context, or source reliability.
7. Return the evidence separately instead of hiding it in one score.

Example output:

```json
{
  "entityId": "E033",
  "matchType": "semantic",
  "lexicalPercentage": 63.16,
  "cosineSimilarity": 0.91,
  "vectorModel": "model-name-and-version",
  "evidence": [
    "same entity type",
    "high semantic similarity",
    "lexical threshold was not met"
  ]
}
```

Do not use `cosineSimilarity >= 0.90` only because the lexical API currently
uses `threshold=90`. Start with labeled matching and non-matching pairs, plot
their score distributions, and choose the threshold that meets the required
precision and recall.

## 14. Hybrid fuzzy and vector search

Hybrid search combines a lexical query and a vector query. OpenSearch uses a
search pipeline to normalize the two score ranges before combining them.
OpenSearch documents this process in its
[hybrid search guide](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/)
and
[normalization processor guide](https://docs.opensearch.org/latest/search-plugins/search-pipelines/normalization-processor/).

### 14.1 Create a search pipeline

This example gives lexical matching 70% of the combined weight and vector
matching 30%:

```json
PUT /_search/pipeline/entity-hybrid-pipeline
{
  "description": "Combine lexical and semantic entity matches",
  "phase_results_processors": [
    {
      "normalization-processor": {
        "normalization": {
          "technique": "min_max"
        },
        "combination": {
          "technique": "arithmetic_mean",
          "parameters": {
            "weights": [0.7, 0.3]
          }
        }
      }
    }
  ]
}
```

### 14.2 Run the hybrid query

```json
POST /entity_catalog-v1/_search?search_pipeline=entity-hybrid-pipeline
{
  "size": 100,
  "_source": {
    "excludes": ["entityEmbedding"]
  },
  "query": {
    "hybrid": {
      "filter": {
        "term": {
          "entityType": "ORGANIZATION"
        }
      },
      "queries": [
        {
          "match": {
            "entitySearchText": {
              "query": "international rescue commitee",
              "fuzziness": "AUTO:5,8",
              "operator": "and"
            }
          }
        },
        {
          "neural": {
            "entityEmbedding": {
              "query_text": "international rescue commitee",
              "model_id": "YOUR_MODEL_ID",
              "k": 100
            }
          }
        }
      ]
    }
  }
}
```

The combined `_score` is a ranking score, not a probability and not a direct
cosine percentage. If explainability is important, retain the lexical and
vector evidence separately in the API response or perform the final
combination in application code.

For short NER names, starting weights of `0.7` lexical and `0.3` vector are
more conservative than making vector similarity dominant. These are only
starting values and must be evaluated with real labeled data.

## 15. Long sentences and document context

Vectors are much more useful for sentences and passages than for isolated
short names.

Example:

```text
Sentence A: The organization provided legal support to displaced families.
Sentence B: The group helped refugees obtain representation and immigration advice.
```

Lexical fuzzy matching sees many different words. A sentence embedding can
capture that both passages discuss similar assistance.

Recommended sentence design:

- Store a separate `sentenceEmbedding` or `passageEmbedding`.
- Split long documents into meaningful chunks before embedding.
- Keep `sentenceEntityId`, `applicationId`, and detected entity IDs as
  metadata filters.
- Search sentence vectors for context, then use exact/fuzzy entity matching
  for identity.

Embedding models have token limits. OpenSearch notes that long text may be
truncated by the embedding model, so long documents should be divided into
smaller passages. See the
[`text_embedding` processor guidance](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/).

## 16. Suggested API response model

Do not force all matching methods into one ambiguous percentage. A clearer
response is:

```json
{
  "entityId": "E031",
  "matchType": "similar",
  "rawExactMatch": false,
  "normalizedExactMatch": false,
  "lexicalPercentage": 94.44,
  "cosineSimilarity": null,
  "hybridScore": null,
  "sourceLocations": []
}
```

For a vector result:

```json
{
  "entityId": "E033",
  "matchType": "semantic",
  "rawExactMatch": false,
  "normalizedExactMatch": false,
  "lexicalPercentage": 63.16,
  "cosineSimilarity": 0.91,
  "hybridScore": 0.84,
  "sourceLocations": []
}
```

This lets clients explain why a result appeared.

## 17. Production rollout plan

1. Build a labeled evaluation set.
   - Matching spelling variants.
   - Different people with similar names.
   - Acronyms and expanded organization names.
   - Transliteration and multilingual examples.
   - Long sentence examples.
2. Record the current exact/fuzzy baseline.
   - Precision, recall, p50, p95, and p99 latency.
   - Candidate count and response payload size.
3. Select and version an embedding model.
   - Use the same model for indexing and querying.
   - Confirm model dimension and token limit.
4. Build the unique entity catalog.
   - Backfill deterministic entity keys.
   - Keep occurrence documents in the existing index.
5. Create a new vector index.
   - Vector mappings cannot be safely retrofitted into every existing design.
   - Use a versioned index and alias for migration.
6. Backfill embeddings in controlled batches.
   - Monitor inference errors, indexing rejections, JVM pressure, vector
     memory, and latency.
7. Dual-write new entities.
   - Upsert the catalog by deterministic key.
   - Write every source occurrence to the occurrence index.
8. Run vector or hybrid search in shadow mode.
   - Do not change user-visible results initially.
   - Compare candidates with labeled outcomes.
9. Calibrate thresholds and `k`.
   - Optimize for the cost of false positives and false negatives.
10. Release as an additional signal.
    - Keep exact and lexical evidence visible.
    - Provide a fallback if model inference is unavailable.

## 18. Final recommendation for this project

For the current NER entity use case:

- Keep the current exact and fuzzy APIs.
- Use `entityId` exact matching whenever a trusted ID exists.
- Use OpenSearch fuzzy retrieval plus RapidFuzz for short names and spelling
  mistakes.
- Do not add a vector to every one of 20 million occurrence documents unless
  testing proves that duplication is necessary.
- Create a unique entity catalog if vector search is introduced.
- Pilot vectors first for long organization names, aliases with different
  words, multilingual/transliterated values, and sentence context.
- Use hybrid or staged matching for identity-sensitive workflows.
- Treat cosine and hybrid scores as model-specific ranking signals, not as
  guaranteed identity probabilities.

This keeps the simple, explainable matching path for narrow NER values while
adding semantic search only where it provides information that edit distance
cannot.

## 19. References

- [Amazon OpenSearch Service vector search](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/vector-search.html)
- [Amazon OpenSearch Service semantic search](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/semantic-search.html)
- [Creating a vector index](https://docs.opensearch.org/latest/vector-search/creating-vector-index/)
- [`knn_vector` field type](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-vector/)
- [Vector spaces and score formulas](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-spaces/)
- [Vector methods and engines](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/)
- [`text_embedding` ingest processor](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/)
- [Hybrid search](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/)
- [Normalization processor](https://docs.opensearch.org/latest/search-plugins/search-pipelines/normalization-processor/)
- [Filtering vector search](https://docs.opensearch.org/latest/vector-search/filter-search-knn/index/)
