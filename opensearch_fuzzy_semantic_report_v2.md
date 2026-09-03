# OpenSearch Fuzzy and Semantic Search Report

## Summary

I tested the entity search flow with **about 7 million sentence-entity records** in AWS OpenSearch.

The main recommendation is simple:

- **Entity names:** keep fuzzy search on demand. Load the entity list first, then fuzzy-search only when the user clicks an entity.
- **Sentences and descriptions:** use embeddings/vector search because meaning matters more than spelling.

Trying to calculate fuzzy results for every entity during page load is not worth the cost.

## Test Results

| Test | Approx. Result |
|---|---:|
| Records indexed | 7 million |
| Application `2823690` | 3,548 entities |
| Application `2822082` | 1,530 unique entities |
| Load entity list | ~1 second |
| Fuzzy summary for ~200 entities | ~3 seconds |
| Fuzzy summary for 1,530 entities at >=90% | ~20-30 seconds |
| Fuzzy search for one entity | ~3 seconds |

Regular OpenSearch lookups stay fast at this scale. The slow part is running fuzzy matching for hundreds or thousands of unique entities at once.

## Recommended Fuzzy Search Approach

For the application page:

```text
Application
  -> load unique entities
  -> show exact counts immediately
  -> user clicks an entity
  -> OpenSearch finds fuzzy candidates
  -> local Levenshtein calculates final similarity
  -> return matching entities/cases
```

OpenSearch `_score` should not be treated as a percentage. I would use OpenSearch to retrieve likely candidates and use normalized **Levenshtein distance** for the final entity similarity percentage.

For short values such as names and organizations, this is more predictable than embeddings because there is not enough context for semantic meaning.

I would start with a **90% fuzzy threshold**, then test 85-90% against real examples.

## Entity Confidence

Some extracted entities are noisy. For example, `Prime Minister` may be classified as a `PERSON`, but the AWS Comprehend confidence can be relatively low.

I recommend testing an AWS Comprehend confidence cutoff around **80-90%** for the main entity workflow. This should reduce noisy entities and also reduce unnecessary fuzzy-search work.

This threshold should be validated with real data so valid entities are not removed accidentally.

# Semantic / Vector Search for Sentences

For **sentence matching**, I recommend semantic vector search.

Example:

```text
The customer moved money through offshore companies.

Funds were transferred through businesses registered overseas.
```

These sentences are semantically similar even though the words are different. Levenshtein is not a good fit here, but embeddings are.

The flow is:

```text
Sentence text
   -> embedding model
   -> vector (list of numbers)
   -> store vector in OpenSearch

User search sentence
   -> same embedding model
   -> query vector
   -> nearest vector search
   -> semantically similar sentences
```

## Important Design Point for the 7 Million Records

If the same sentence is repeated across several entity-occurrence records, **do not store the same sentence embedding on every entity record**.

Prefer something like:

```text
entity-occurrence-index
  -> entityId
  -> sentenceId
  -> applicationId
  -> entity fields

sentence-search-index
  -> sentenceId
  -> applicationId
  -> sentenceText
  -> sentence embedding
```

That keeps entity fuzzy search and sentence semantic search separate and avoids generating/storing duplicate vectors.

For a record that already represents exactly one unique sentence, one embedding per record is fine.

# Recommended Semantic Search Setup

For AWS, I would start with **Amazon Bedrock Titan Text Embeddings V2** (or another supported text embedding model) connected to Amazon OpenSearch Service.

The easiest setup is through the **OpenSearch Service Integrations / CloudFormation integration** for Bedrock. After setup, OpenSearch gives you a `model_id`. That model ID is what the index uses to create query and document embeddings.

The exact implementation depends on the OpenSearch version.

## Option A - OpenSearch 3.1+ `semantic` Field (Recommended if Available)

OpenSearch 3.1 introduced the `semantic` field. This is the cleanest setup because OpenSearch automatically creates and manages the underlying vector field.

### 1. Create the index mapping

```json
PUT /sentence-search
{
  "settings": {
    "index.knn": true
  },
  "mappings": {
    "properties": {
      "sentenceId": {
        "type": "keyword"
      },
      "applicationId": {
        "type": "keyword"
      },
      "sentenceText": {
        "type": "semantic",
        "model_id": "<BEDROCK_MODEL_ID>"
      }
    }
  }
}
```

No separate `text_embedding` ingest pipeline is required for this option. OpenSearch generates the embedding when the document is indexed.

### 2. Index a sentence

```json
POST /sentence-search/_doc/1
{
  "sentenceId": "sentence-1001",
  "applicationId": "2822082",
  "sentenceText": "The customer transferred money through offshore companies."
}
```

Internally OpenSearch creates the embedding for `sentenceText` and stores the vector metadata.

### 3. Search by meaning

```json
GET /sentence-search/_search
{
  "size": 20,
  "_source": {
    "excludes": [
      "sentenceText_semantic_info"
    ]
  },
  "query": {
    "neural": {
      "sentenceText": {
        "query_text": "money moved through overseas businesses",
        "k": 50
      }
    }
  }
}
```

OpenSearch performs these steps automatically:

```text
query text
  -> embedding model
  -> query vector
  -> vector similarity search
  -> top matching sentences
```

Because the model is already attached to the `semantic` field, the query does not need to send `model_id` again.

### 4. Exclude the current application

This is useful for our use case when we want similar sentences from other cases:

```json
GET /sentence-search/_search
{
  "size": 20,
  "query": {
    "neural": {
      "sentenceText": {
        "query_text": "money moved through overseas businesses",
        "k": 100,
        "filter": {
          "bool": {
            "must_not": [
              {
                "term": {
                  "applicationId": "2822082"
                }
              }
            ]
          }
        }
      }
    }
  }
}
```

This gives us semantically similar sentences while ignoring the current application.

# Option B - Explicit Embedding Pipeline + `knn_vector`

Use this if the OpenSearch domain does not support the `semantic` field yet, or if we want more control over the vector mapping.

Assume the Bedrock embedding model returns **1024 dimensions**. The vector mapping must use the same dimension as the selected model.

## 1. Create the embedding ingest pipeline

```json
PUT /_ingest/pipeline/sentence-embedding-pipeline
{
  "description": "Generate sentence embeddings",
  "processors": [
    {
      "text_embedding": {
        "model_id": "<BEDROCK_MODEL_ID>",
        "field_map": {
          "sentenceText": "sentenceEmbedding"
        }
      }
    }
  ]
}
```

## 2. Create the vector index

```json
PUT /sentence-search
{
  "settings": {
    "index.knn": true,
    "default_pipeline": "sentence-embedding-pipeline"
  },
  "mappings": {
    "properties": {
      "sentenceId": {
        "type": "keyword"
      },
      "applicationId": {
        "type": "keyword"
      },
      "sentenceText": {
        "type": "text"
      },
      "sentenceEmbedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "engine": "lucene",
          "space_type": "cosinesimil"
        }
      }
    }
  }
}
```

## 3. Index normal text

```json
POST /sentence-search/_doc/1
{
  "sentenceId": "sentence-1001",
  "applicationId": "2822082",
  "sentenceText": "The customer transferred money through offshore companies."
}
```

Because `sentence-embedding-pipeline` is the default pipeline, OpenSearch automatically sends `sentenceText` to the embedding model and stores the result in `sentenceEmbedding`.

Conceptually, the stored document becomes:

```json
{
  "sentenceId": "sentence-1001",
  "applicationId": "2822082",
  "sentenceText": "The customer transferred money through offshore companies.",
  "sentenceEmbedding": [0.013, -0.052, 0.021, "..."]
}
```

The application does not need to create the vector itself.

## 4. Search the vector field

```json
GET /sentence-search/_search
{
  "size": 20,
  "_source": [
    "sentenceId",
    "applicationId",
    "sentenceText"
  ],
  "query": {
    "neural": {
      "sentenceEmbedding": {
        "query_text": "money moved through overseas businesses",
        "model_id": "<BEDROCK_MODEL_ID>",
        "k": 50
      }
    }
  }
}
```

Here OpenSearch converts the query text to a vector using the same model and compares it against `sentenceEmbedding`.

# Hybrid Search - Keyword + Semantic

For sentence search, I would eventually use **hybrid search** instead of semantic-only search.

Why?

Semantic search is good for meaning, while normal `match`/BM25 search is good when exact words, names, IDs, or phrases matter.

Example:

```text
User query:
"Prime Minister transferred funds to an offshore company"

Keyword search
  -> strongly rewards "Prime Minister" and "offshore"

Semantic search
  -> finds sentences describing the same activity using different words

Hybrid search
  -> combines both
```

### 1. Create a hybrid search pipeline

```json
PUT /_search/pipeline/sentence-hybrid-pipeline
{
  "description": "Combine keyword and semantic sentence search",
  "phase_results_processors": [
    {
      "normalization-processor": {
        "normalization": {
          "technique": "min_max"
        },
        "combination": {
          "technique": "arithmetic_mean",
          "parameters": {
            "weights": [
              0.3,
              0.7
            ]
          }
        }
      }
    }
  ]
}
```

In this example:

- 30% = keyword/BM25 score
- 70% = semantic/vector score

Those weights should be tuned using real search examples.

### 2. Run the hybrid query

For a `semantic` field:

```json
GET /sentence-search/_search?search_pipeline=sentence-hybrid-pipeline
{
  "size": 20,
  "query": {
    "hybrid": {
      "queries": [
        {
          "match": {
            "sentenceText": "money moved through overseas businesses"
          }
        },
        {
          "neural": {
            "sentenceText": {
              "query_text": "money moved through overseas businesses",
              "k": 50
            }
          }
        }
      ]
    }
  }
}
```

This is the approach I would recommend for a future **sentence search API**.

# What About Similarity Thresholds?

For entity fuzzy matching, a Levenshtein result like **90%** is easy to understand.

Vector-search `_score` is different. A semantic score should **not automatically be treated as a percentage**. The score depends on the model, vector space, search method, and OpenSearch scoring behavior.

For semantic sentence search I would initially:

1. Request the top 20-50 matches.
2. Review real good/bad matches.
3. Build a small test set of expected sentence matches.
4. Choose a `min_score` only after seeing how the selected model scores our data.

Do not assume that semantic `_score = 0.90` means "90% similar".

# Sentence vs. Long Description

For a single sentence, I would **not chunk it**. The sentence is already a good semantic unit.

For long descriptions or full documents, chunk the text first:

```text
Long document
  -> split into passages/sentences
  -> embedding per chunk
  -> vector search finds the best chunk
  -> return the original document + matching passage
```

OpenSearch supports text chunking before embedding, and the newer `semantic` field can also manage chunking for long-form text.

# Final Recommended Architecture

```text
                    OpenSearch
                        |
          +-------------+-------------+
          |                           |
    Entity Search                 Sentence Search
          |                           |
 exact entityId lookup            keyword/BM25
          |                           +
 user selects entity              embeddings/vector
          |                           |
 fuzzy candidates                hybrid semantic search
          |
 local Levenshtein
          |
  >= 85-90% match
```

For our current use case, I would implement it in this order:

1. **Keep application entity loading as-is** because it is fast.
2. **Run fuzzy entity matching only when an entity is selected.**
3. Use **Levenshtein for names/organizations**, not embeddings.
4. Filter or flag low-confidence Comprehend entities, starting around **80-90% confidence** and validating against real data.
5. Create a **separate sentence semantic-search index** if sentence text is duplicated across entity records.
6. Add one embedding per unique sentence.
7. Start with semantic search and then move to **hybrid keyword + semantic search** after collecting real query examples.

## Conclusion

The 7-million-record test shows that OpenSearch itself is fast for normal indexed lookups. The expensive part is trying to calculate a fuzzy summary for every entity at once.

For entities, the best design is **load first, fuzzy-search on demand, then calculate the final score with Levenshtein**.

For sentences and longer descriptions, embeddings are a much better fit. Store one vector per unique sentence, let OpenSearch/Bedrock generate embeddings during ingestion, and use `neural` or hybrid queries to find text with the same meaning even when the wording is different.

## References

- OpenSearch semantic search: https://docs.opensearch.org/latest/vector-search/ai-search/semantic-search/
- OpenSearch semantic field: https://docs.opensearch.org/latest/mappings/supported-field-types/semantic/
- OpenSearch neural query: https://docs.opensearch.org/latest/query-dsl/specialized/neural/
- OpenSearch vector index: https://docs.opensearch.org/latest/vector-search/creating-vector-index/
- OpenSearch hybrid search: https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/
- AWS OpenSearch ML connectors: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ml-create.html
- AWS / Bedrock semantic search example: https://docs.opensearch.org/latest/tutorials/vector-search/semantic-search/semantic-search-bedrock-titan/
- AWS DocumentDB + OpenSearch example: https://aws.amazon.com/blogs/database/perform-fuzzy-full-text-search-and-semantic-search-on-amazon-documentdb-using-amazon-opensearch-service/
