# OpenSearch 3.7, Lasso, and Titan V2 Runbook

This runbook creates the complete ML Commons path used by this project:

```text
OpenSearch document
  -> text_embedding ingest pipeline
  -> deployed ML Commons model
  -> standalone Lasso connector
  -> Amazon Bedrock Titan Text Embeddings V2
  -> 512-float embedding
  -> entitySearchTextVector
```

Amazon OpenSearch Service already supplies ML Commons, Neural Search, and k-NN.
There is no custom plugin ZIP to install. The work below configures those
features in one OpenSearch domain.

Run the requests in OpenSearch Dashboards **Dev Tools**, or with an equivalent
SigV4-signed client. The caller needs the OpenSearch `ml_full_access` role and
permission to manage the target index and ingest pipeline.

## Values to record

Replace angle-bracket values throughout this guide and keep the returned IDs:

| Value | Meaning |
| --- | --- |
| `<BACKEND_ROLE>` | OpenSearch backend role allowed to use the resources |
| `<LASSO_HOST>` | Lasso hostname only, without `https://` |
| `<LASSO_TITAN_PATH>` | Lasso route that invokes Titan V2 |
| `<LASSO_TOKEN>` | Secret expected by Lasso; never commit it |
| `<MODEL_GROUP_ID>` | Returned when the model group is registered |
| `<CONNECTOR_ID>` | Returned when the connector is created |
| `<MODEL_ID>` | Returned when the remote model is registered |
| `<TASK_ID>` | Temporary ID returned by an asynchronous ML operation |

The Bedrock foundation model ID is
`amazon.titan-embed-text-v2:0`. It is not the OpenSearch `<MODEL_ID>`.

## 1. Enable model access control

Enable model access control before creating the model group:

```json
PUT /_cluster/settings
{
  "persistent": {
    "plugins.ml_commons.model_access_control_enabled": true
  }
}
```

Verify the effective value:

```text
GET /_cluster/settings?include_defaults=true&flat_settings=true
```

Model access control applies through model groups. This guide uses a
`restricted` group so only its owner and matching backend roles can use it.

### Trust the Lasso endpoint

ML Commons only calls connector URLs allowed by
`plugins.ml_commons.trusted_connector_endpoints_regex`.

First read the current setting:

```text
GET /_cluster/settings?flat_settings=true
```

Then update the list, preserving every expression already needed by other
connectors and adding the narrowest expression that matches Lasso:

```json
PUT /_cluster/settings
{
  "persistent": {
    "plugins.ml_commons.trusted_connector_endpoints_regex": [
      "^https://<LASSO_HOST>/<ESCAPED-LASSO-PATH>/.*$"
    ]
  }
}
```

This array replaces the previous array; do not accidentally remove other
trusted endpoints. Escape dots as `\\.` and other Java-regex characters. Avoid
a permissive expression such as `^https://.*$`.

If the Lasso hostname resolves to a private address, network access and the
managed-service private-endpoint setting must also be configured. A trusted
regex permits a URL; it does not create a network route to it.

Connector access control is separate from model access control. Enable it only
if the domain uses backend-role restrictions for standalone connectors:

```json
PUT /_cluster/settings
{
  "persistent": {
    "plugins.ml_commons.connector_access_control_enabled": true
  }
}
```

## 2. Register a model group

Model groups hold model versions and enforce access control:

```json
POST /_plugins/_ml/model_groups/_register
{
  "name": "entity-titan-v2-lasso",
  "description": "Titan V2 entity embeddings through Lasso",
  "access_mode": "restricted",
  "backend_roles": ["<BACKEND_ROLE>"]
}
```

Save the returned `model_group_id` as `<MODEL_GROUP_ID>`.

- `restricted` is the normal production choice.
- `private` permits only the owner.
- `public` permits every OpenSearch user and is not recommended for this
  connector.

## 3. Create the Lasso connector

Lasso gateways are organization-specific. The example below assumes:

- OpenSearch calls Lasso over HTTPS using `protocol: "http"`.
- Lasso accepts `Authorization: Bearer <token>`.
- The selected Lasso route accepts the native Titan V2 request body and returns
  the native Titan V2 response.

Change only the URL, authorization header, and any proxy-required static
headers to match the approved Lasso contract. If Lasso uses `x-api-key`, for
example, replace the `Authorization` header and rename the credential.

```json
POST /_plugins/_ml/connectors/_create
{
  "name": "Lasso to Amazon Titan Text Embeddings V2",
  "description": "Entity embeddings through the Lasso proxy",
  "version": 1,
  "protocol": "http",
  "parameters": {
    "model": "amazon.titan-embed-text-v2:0",
    "dimensions": 512,
    "normalize": true,
    "embeddingTypes": ["float"]
  },
  "credential": {
    "lasso_token": "<LASSO_TOKEN>"
  },
  "actions": [
    {
      "action_type": "predict",
      "method": "POST",
      "url": "https://<LASSO_HOST>/<LASSO_TITAN_PATH>",
      "headers": {
        "content-type": "application/json",
        "Authorization": "Bearer ${credential.lasso_token}"
      },
      "request_body": "{ \"inputText\": \"${parameters.inputText}\", \"dimensions\": ${parameters.dimensions}, \"normalize\": ${parameters.normalize}, \"embeddingTypes\": ${parameters.embeddingTypes} }",
      "pre_process_function": "connector.pre_process.bedrock.embedding",
      "post_process_function": "connector.post_process.bedrock_v2.embedding.float"
    }
  ]
}
```

Save the returned `connector_id` as `<CONNECTOR_ID>`, then inspect the stored
definition:

```text
GET /_plugins/_ml/connectors/<CONNECTOR_ID>
```

The two processing functions are important:

- `connector.pre_process.bedrock.embedding` converts ML Commons `text_docs`
  input into the `${parameters.inputText}` value required by Titan.
- `connector.post_process.bedrock_v2.embedding.float` extracts Titan V2's
  float embedding and returns the shape expected by `text_embedding` and
  `neural`.

Do not replace `${parameters.inputText}` with fixed text. The preprocessor fills
it separately for each indexed document or query.

OpenSearch encrypts the connector credential in its system index, but the
secret can still be exposed in Dev Tools history, shell history, or deployment
logs. Prefer the organization's approved secret-injection process.

## 4. Register the remote model

Register one OpenSearch model version against the standalone connector. Keep
deployment separate so its result can be checked explicitly:

```json
POST /_plugins/_ml/models/_register
{
  "name": "Amazon Titan Text Embeddings V2 through Lasso",
  "function_name": "remote",
  "description": "Titan V2 normalized 512-dimensional float embeddings",
  "model_group_id": "<MODEL_GROUP_ID>",
  "connector_id": "<CONNECTOR_ID>",
  "model_config": {
    "model_type": "TEXT_EMBEDDING",
    "embedding_dimension": 512,
    "framework_type": "SENTENCE_TRANSFORMERS",
    "additional_config": {
      "space_type": "cosinesimil"
    }
  }
}
```

The response can contain `model_id` immediately, or it can return a `task_id`.
For a task, poll until `state` is `COMPLETED`:

```text
GET /_plugins/_ml/tasks/<TASK_ID>
```

Save the resulting `model_id` as `<MODEL_ID>`. Never substitute a connector,
group, or task ID where a model ID is required.

### What `model_config` controls

| Field | Value here | Purpose |
| --- | --- | --- |
| `model_type` | `TEXT_EMBEDDING` | Identifies this as a dense text-embedding model |
| `embedding_dimension` | `512` | Declares the exact output vector length |
| `framework_type` | `SENTENCE_TRANSFORMERS` | Supplies the ML Commons text-embedding interface metadata |
| `additional_config.space_type` | `cosinesimil` | Makes the model's similarity contract cosine |

The dimension must agree in four places: the Titan request, Titan response,
OpenSearch `model_config`, and `knn_vector` mapping. A dimension change requires
a new compatible model configuration and a newly created vector index.

Titan V2 supports 1,024, 512, and 256 dimensions. This project uses 512 to
reduce vector memory, storage, and distance-computation work. Validate search
quality and recalibrate semantic thresholds on representative labeled pairs.

`normalize: true` produces unit-length embeddings and is appropriate for this
project's cosine similarity search. `embeddingTypes: ["float"]` selects the
float vector extracted by the postprocessor.

## 5. Deploy and inspect the model

Deploy the model:

```text
POST /_plugins/_ml/models/<MODEL_ID>/_deploy
```

Poll the returned task:

```text
GET /_plugins/_ml/tasks/<TASK_ID>
```

Then inspect the model:

```text
GET /_plugins/_ml/models/<MODEL_ID>
```

Continue only when the task is `COMPLETED` and the model state is `DEPLOYED`.
Although recent OpenSearch versions can auto-deploy a remote model on first
prediction, explicit deployment exposes connector and permission problems
before ingestion starts.

## 6. Test prediction in two layers

### Test A: direct model/connector call

This checks the Lasso route and Titan-shaped request:

```json
POST /_plugins/_ml/models/<MODEL_ID>/_predict
{
  "parameters": {
    "inputText": "Ethiopian Airlines"
  }
}
```

This test should reach Lasso and return an inference result. By itself, it does
not prove that an ingest pipeline will work.

### Test B: ML Commons text-embedding contract

This is the important test because it uses the same input contract as the
`text_embedding` processor and a `neural` query:

```json
POST /_plugins/_ml/_predict/text_embedding/<MODEL_ID>
{
  "text_docs": ["Ethiopian Airlines"],
  "return_number": true,
  "target_response": ["sentence_embedding"]
}
```

The result should contain one float embedding with 512 values. If the direct
test works but this test fails, the connector's preprocessor, postprocessor, or
model configuration is wrong.

## 7. Create and test the ingest pipeline

The pipeline converts `entitySearchText` into `entitySearchTextVector` during
indexing:

```json
PUT /_ingest/pipeline/my_bedrock_embedding_pipeline
{
  "description": "Titan V2 entity embeddings through Lasso",
  "processors": [
    {
      "text_embedding": {
        "model_id": "<MODEL_ID>",
        "field_map": {
          "entitySearchText": "entitySearchTextVector"
        }
      }
    }
  ]
}
```

Creating a pipeline does not validate that the model exists or is deployed.
Always simulate it before a bulk seed:

```json
POST /_ingest/pipeline/my_bedrock_embedding_pipeline/_simulate
{
  "docs": [
    {
      "_source": {
        "entitySearchText": "Ethiopian Airlines"
      }
    }
  ]
}
```

The simulated `_source` should include an `entitySearchTextVector` array of
length 512. Start with the processor's default single-item behavior; only add
processor `batch_size` after confirming that the Lasso/Titan path supports the
resulting load and request contract.

## 8. Create the vector index and alias

Create the catalog index with the pipeline as its default:

```json
PUT /ner_entity_semantic_catalog-v1
{
  "settings": {
    "index.knn": true,
    "index.default_pipeline": "my_bedrock_embedding_pipeline",
    "number_of_shards": 1,
    "number_of_replicas": 1
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "semanticKey": {
        "type": "keyword"
      },
      "normalizedText": {
        "type": "keyword"
      },
      "entitySearchText": {
        "type": "text"
      },
      "entitySearchTextVector": {
        "type": "knn_vector",
        "dimension": 512,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "faiss"
        }
      }
    }
  }
}
```

Add its read/write alias:

```json
POST /_aliases
{
  "actions": [
    {
      "add": {
        "index": "ner_entity_semantic_catalog-v1",
        "alias": "ner_entity_semantic_catalog"
      }
    }
  ]
}
```

`space_type` belongs inside the vector field's `method`; do not use the invalid
index setting `index.knn.space_type`. Field names are case-sensitive. With
`dynamic: "strict"`, `entitySearchtextVector` and
`entitySearchTextVector` are different names and the former is rejected.

## 9. Prove ingestion and semantic search

Index one document without supplying a vector. The default pipeline creates it:

```json
PUT /ner_entity_semantic_catalog/_doc/test-1?refresh=true
{
  "semanticKey": "test-1",
  "normalizedText": "ethiopian airlines",
  "entitySearchText": "Ethiopian Airlines"
}
```

Confirm the document exists without printing the large vector:

```json
GET /ner_entity_semantic_catalog/_doc/test-1?_source_excludes=entitySearchTextVector
```

Run a semantic query:

```json
POST /ner_entity_semantic_catalog/_search
{
  "size": 10,
  "_source": [
    "semanticKey",
    "normalizedText",
    "entitySearchText"
  ],
  "query": {
    "neural": {
      "entitySearchTextVector": {
        "query_text": "Ethiopian air carrier",
        "model_id": "<MODEL_ID>",
        "k": 10
      }
    }
  }
}
```

After these checks pass, configure the application:

```dotenv
OPENSEARCH_SEMANTIC_MODEL_ID=<MODEL_ID>
OPENSEARCH_INGEST_PIPELINE=my_bedrock_embedding_pipeline
```

## Updating the connector safely

Undeploy every model that uses a standalone connector before changing that
connector. While the model is undeployed, pipeline ingestion and text-based
neural queries using it are unavailable, so perform this during a maintenance
window.

1. Record the model and connector definitions:

   ```text
   GET /_plugins/_ml/models/<MODEL_ID>
   GET /_plugins/_ml/connectors/<CONNECTOR_ID>
   ```

2. Undeploy the model:

   ```text
   POST /_plugins/_ml/models/<MODEL_ID>/_undeploy
   ```

3. Update the connector. A small metadata example is:

   ```json
   PUT /_plugins/_ml/connectors/<CONNECTOR_ID>
   {
     "description": "Updated Lasso connector description"
   }
   ```

   For a URL, header, request body, or processing-function change, send the
   complete replacement `actions` array. The Update Connector API can update or
   add fields but cannot delete an existing field.

4. Redeploy and poll its task:

   ```text
   POST /_plugins/_ml/models/<MODEL_ID>/_deploy
   GET  /_plugins/_ml/tasks/<TASK_ID>
   ```

5. Repeat both prediction tests and the pipeline simulation before resuming
   ingestion.

Use the same undeploy/redeploy cycle after changing trusted endpoint or private
network settings, because deployment can cache connector execution state.

If the connector changes to a model with a different dimension or embedding
meaning, create a new model version and new vector index, re-embed the catalog,
validate relevance, and switch the alias. Existing vectors cannot be mixed with
vectors from a different model configuration.

## Delete everything created by this runbook

Use exact IDs and names. Do not use wildcards. Stop application writes first,
then delete in dependency order.

1. Delete the test/catalog index. Deleting the index also removes its alias
   binding:

   ```text
   DELETE /ner_entity_semantic_catalog-v1
   ```

2. Delete the ingest pipeline so it no longer references the model:

   ```text
   DELETE /_ingest/pipeline/my_bedrock_embedding_pipeline
   ```

3. Undeploy and delete the model:

   ```text
   POST   /_plugins/_ml/models/<MODEL_ID>/_undeploy
   DELETE /_plugins/_ml/models/<MODEL_ID>
   ```

4. Delete the standalone connector:

   ```text
   DELETE /_plugins/_ml/connectors/<CONNECTOR_ID>
   ```

5. Delete the model group if it still exists:

   ```text
   DELETE /_plugins/_ml/model_groups/<MODEL_GROUP_ID>
   ```

   OpenSearch can automatically delete a model group when its last model is
   deleted. A `not_found` result here is therefore harmless.

6. Remove the Lasso trusted-endpoint regex by reading the shared array and
   writing it back without only that expression. Do not erase expressions used
   by other connectors.

7. On a dedicated test domain only, restore the access-control setting to its
   default:

   ```json
   PUT /_cluster/settings
   {
     "persistent": {
       "plugins.ml_commons.model_access_control_enabled": null
     }
   }
   ```

   Do not disable this shared setting on a domain where other model groups
   depend on it.

## Fast troubleshooting

| Error or symptom | Meaning and fix |
| --- | --- |
| Connector URL is not trusted | The full Lasso URL does not match `trusted_connector_endpoints_regex`; correct the narrow regex and redeploy the model |
| `Some parameter placeholder not filled ... inputText` | `${parameters.inputText}` is missing, misspelled, or not populated by `connector.pre_process.bedrock.embedding` |
| Direct prediction returns a nested raw `response` | Add or correct `connector.post_process.bedrock_v2.embedding.float`, redeploy, and rerun the text-embedding test |
| `Vector dimension mismatch` | Connector/model/mapping dimensions differ; use 512 everywhere and recreate the index whose immutable mapping is wrong |
| Strict mapping rejects `entitySearchtextVector` | Fix the capitalization to `entitySearchTextVector` in both the pipeline and mapping |
| Model space type is not `cosinesimil` | Register the model with `model_config.additional_config.space_type` set to `cosinesimil` |
| Lasso returns HTTP 5xx | Lasso or its upstream Bedrock call failed; use bounded retries with backoff, but do not retry mapping or other permanent 4xx errors |
| HTTP 401/403 | Check the Lasso credential, model-group backend role, OpenSearch `ml_full_access`, and domain access policy |

## Official references

- [ML Commons model access control](https://docs.opensearch.org/latest/ml-commons-plugin/model-access-control/)
- [Connecting to externally hosted models](https://docs.opensearch.org/latest/ml-commons-plugin/remote-models/)
- [Connector blueprints and processing functions](https://docs.opensearch.org/latest/ml-commons-plugin/remote-models/blueprints/)
- [Register Model Group API](https://docs.opensearch.org/latest/ml-commons-plugin/api/model-group-apis/register-model-group/)
- [Create and update connector APIs](https://docs.opensearch.org/latest/ml-commons-plugin/api/connector-apis/)
- [Register, deploy, undeploy, and delete model APIs](https://docs.opensearch.org/latest/ml-commons-plugin/api/model-apis/)
- [Predict API](https://docs.opensearch.org/latest/ml-commons-plugin/api/train-predict/predict/)
- [Text embedding ingest processor](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/)
- [k-NN vector mapping](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-vector/)
- [Neural query](https://docs.opensearch.org/latest/query-dsl/specialized/neural/)
- [Titan Text Embeddings V2 parameters](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-titan-embed-text.html)
