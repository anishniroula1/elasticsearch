# Create and Find the OpenSearch Model ID for Titan V2

This guide explains how to connect Amazon OpenSearch Service to Amazon Bedrock
Titan Text Embeddings V2 and obtain the model ID required by this project.

## The two different IDs

These IDs are related, but they are not interchangeable.

### Bedrock foundation model ID

```text
amazon.titan-embed-text-v2:0
```

This identifies Titan Text Embeddings V2 inside Amazon Bedrock. It is placed in
the OpenSearch connector configuration.

### OpenSearch model ID

```text
xhR35JQBLopfJ2xsO9pr
```

This example is an opaque ID generated after a connector-backed model is
registered in a particular OpenSearch cluster. This is the value the project
needs:

```dotenv
OPENSEARCH_SEMANTIC_MODEL_ID=xhR35JQBLopfJ2xsO9pr
```

An OpenSearch model ID is scoped to the domain where the model was registered.
An ID from one AWS domain cannot be used by another domain.

## Prerequisites

Before creating the integration, confirm the following:

- The target domain runs OpenSearch 3.1 or later. The `semantic` field was
  introduced in OpenSearch 3.1.
- Fine-grained access control is enabled when using the AWS-provided
  CloudFormation integration.
- Titan Text Embeddings V2 is available in the selected AWS Region.
- The setup identity can use CloudFormation, Lambda, IAM, Amazon Bedrock, and
  the target OpenSearch domain.
- For a VPC domain, the setup process has network access to that VPC/domain.

Titan Text Embeddings V2 currently uses this Bedrock model ID and configuration:

```text
Model:      amazon.titan-embed-text-v2:0
Dimensions: 1024 by default; 512 and 256 are also supported
Normalize:  true
Type:       float
```

This project expects the OpenSearch model configuration to describe the same
dimension and vector space used by the connector.

## Option A: AWS Console Integration (Recommended)

Amazon OpenSearch Service provides an integration that creates the Bedrock
connector and model through CloudFormation.

### 1. Open the integration

1. Sign in to the AWS console in the same Region as the OpenSearch domain.
2. Open **Amazon OpenSearch Service**.
3. In the left navigation, choose **Integrations**.
4. Find **Integrate with Amazon Titan Text Embeddings model through Amazon
   Bedrock**.
5. Choose **Configure domain** for a VPC domain or **Configure public domain**
   for a public domain.

### 2. Configure the target

Follow the integration wizard and select the target OpenSearch domain. For a
VPC domain, provide the requested VPC and networking values. Review the
CloudFormation resources before creating the stack.

The integration creates supporting resources such as an IAM role and Lambda
function, then uses ML Commons to create the connector/model in the selected
domain.

Wait until the CloudFormation stack reaches:

```text
CREATE_COMPLETE
```

If the stack has a model-ID output, copy it. If no clear model-ID output is
shown, use the model search API in the next section.

### 3. Find the generated model ID

Open OpenSearch Dashboards for the same domain and use **Dev Tools**, or send an
authenticated request to the domain.

List registered models, excluding internal model chunks:

```json
POST /_plugins/_ml/models/_search
{
  "query": {
    "bool": {
      "must_not": {
        "exists": {
          "field": "chunk_number"
        }
      }
    }
  },
  "sort": [
    {
      "created_time": {
        "order": "desc"
      }
    }
  ],
  "size": 100
}
```

Look for the recent Bedrock/Titan text embedding model. The model ID is the
top-level `_id` of the matching hit:

```json
{
  "hits": {
    "hits": [
      {
        "_id": "xhR35JQBLopfJ2xsO9pr",
        "_source": {
          "name": "Bedrock embedding model",
          "function_name": "REMOTE"
        }
      }
    ]
  }
}
```

Do not use `connector_id`, `model_group_id`, or `task_id` as the semantic-field
`model_id`.

### 4. Verify the model

Retrieve the model directly:

```text
GET /_plugins/_ml/models/xhR35JQBLopfJ2xsO9pr
```

The model should be available for inference. A deployed model normally reports:

```json
{
  "model_state": "DEPLOYED"
}
```

If it is registered but not deployed, deploy it explicitly:

```text
POST /_plugins/_ml/models/xhR35JQBLopfJ2xsO9pr/_deploy
```

The deploy operation returns a `task_id`. Check it until `state` is
`COMPLETED`:

```text
GET /_plugins/_ml/tasks/<TASK_ID>
```

OpenSearch 2.13 and later can automatically deploy an externally hosted model
on its first prediction, but an explicit deployment check makes setup failures
easier to diagnose.

### 5. Test the semantic-field inference contract

```json
POST /_plugins/_ml/_predict/text_embedding/xhR35JQBLopfJ2xsO9pr
{
  "text_docs": ["hello world"],
  "return_number": true,
  "target_response": ["sentence_embedding"]
}
```

A successful response contains an inference result with a float embedding. For
the 1,024-dimension Titan V2 configuration, the output shape should be 1,024.
This exact test matters: a `semantic` field sends `text_docs`, while Titan
expects `inputText`. The connector preprocessor must translate between them.
Testing `/_plugins/_ml/models/<MODEL_ID>/_predict` with an already formed
`parameters.inputText` body can succeed even when semantic-field ingestion will
fail.

### 6. Configure this project

From `semantic-search-demo`, edit the included `.env`. If it is missing, copy
the template first:

```bash
cp .env.example .env
```

Set the OpenSearch model ID and AWS domain settings:

```dotenv
AWS_REGION=us-east-1
OPENSEARCH_HOST=search-my-domain.us-east-1.es.amazonaws.com
OPENSEARCH_SEMANTIC_MODEL_ID=xhR35JQBLopfJ2xsO9pr
```

Then run:

```bash
make seed
```

During `make seed`, the CSV contains only normal entity fields. The seeder
deduplicates `entitySearchText`, and OpenSearch generates one embedding per
unique text in the semantic catalog. Occurrence documents contain no vectors.

### 7. Verify the semantic field and generated vector

Check the index mapping:

```text
GET /ner_entity_semantic_catalog-v1/_mapping
```

It should contain both:

```text
entitySearchText
entitySearchText_semantic_info
```

`entitySearchText` is the configured `semantic` field. OpenSearch creates
`entitySearchText_semantic_info`, including the underlying embedding and model
metadata.

Inspect the generated embedding mapping and note its `space_type`. The project
supports `cosinesimil` directly and `l2` for normalized Titan V2 embeddings.
Titan V2 normalization defaults to `true`. The API converts the requested
cosine-similarity percentage to the correct OpenSearch score for either space.
Startup fails only for another vector space or a missing `space_type`.

Check the occurrence and semantic-catalog document counts:

```text
GET /ner_entity_occurrences/_count
GET /ner_entity_semantic_catalog/_count
```

The included sample CSV produces 26 occurrence documents but only four unique
semantic-catalog documents and four Titan embeddings.

## Option B: Manual Connector and Model Registration

Use this route only when the AWS console integration is unavailable or the
connector configuration must be controlled directly. The console integration
is less error-prone.

### 1. Create a connector execution role

Create an IAM role that Amazon OpenSearch Service can assume. Give it only the
permission needed to invoke Titan V2:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:<REGION>::foundation-model/amazon.titan-embed-text-v2:0"
    }
  ]
}
```

For an OpenSearch Service domain, its trust policy uses the OpenSearch service
principal. Restrict it to the account and domain to avoid a confused-deputy
risk:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "opensearchservice.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "<ACCOUNT_ID>"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:es:<REGION>:<ACCOUNT_ID>:domain/<DOMAIN_NAME>"
        }
      }
    }
  ]
}
```

The identity creating the connector also needs `iam:PassRole` for this exact
role and permission to call the relevant OpenSearch HTTP APIs. With
fine-grained access control, map that identity to the OpenSearch
`ml_full_access` backend role.

This application targets an Amazon OpenSearch Service managed domain, not
OpenSearch Serverless.

### 2. Create the Titan V2 connector

Send this request to the OpenSearch domain. For Amazon OpenSearch Service, the
request must be SigV4-signed by the IAM identity that has `iam:PassRole`; do
not send this request from Dashboards Dev Tools. Use `roleArn` and do not put
long-lived AWS access keys in source files.

```json
POST /_plugins/_ml/connectors/_create
{
  "name": "Amazon Bedrock Titan Text Embeddings V2",
  "description": "Titan V2 semantic embeddings",
  "version": 1,
  "protocol": "aws_sigv4",
  "parameters": {
    "region": "<REGION>",
    "service_name": "bedrock",
    "model": "amazon.titan-embed-text-v2:0",
    "dimensions": 1024,
    "normalize": true,
    "embeddingTypes": ["float"]
  },
  "credential": {
    "roleArn": "arn:aws:iam::<ACCOUNT_ID>:role/<CONNECTOR_ROLE_NAME>"
  },
  "actions": [
    {
      "action_type": "predict",
      "method": "POST",
      "url": "https://bedrock-runtime.${parameters.region}.amazonaws.com/model/${parameters.model}/invoke",
      "headers": {
        "content-type": "application/json",
        "x-amz-content-sha256": "required"
      },
      "request_body": "{ \"inputText\": \"${parameters.inputText}\", \"dimensions\": ${parameters.dimensions}, \"normalize\": ${parameters.normalize}, \"embeddingTypes\": ${parameters.embeddingTypes} }",
      "pre_process_function": "connector.pre_process.bedrock.embedding",
      "post_process_function": "connector.post_process.bedrock_v2.embedding.float"
    }
  ]
}
```

Save the returned `connector_id`.

### 3. Register the model with semantic-field configuration

The `model_config` is required for a remote model used by a `semantic` field.
Without it, index creation can fail with `Model config is null for the remote
model`.

```json
POST /_plugins/_ml/models/_register?deploy=true
{
  "name": "Bedrock Titan Text Embeddings V2",
  "function_name": "remote",
  "description": "Titan V2 1024-dimensional normalized embeddings",
  "connector_id": "<CONNECTOR_ID>",
  "model_config": {
    "model_type": "TEXT_EMBEDDING",
    "embedding_dimension": 1024,
    "framework_type": "SENTENCE_TRANSFORMERS",
    "additional_config": {
      "space_type": "cosinesimil"
    }
  }
}
```

`cosinesimil` is recommended for a new manual registration because it directly
matches the API contract. An existing working Titan V2 registration that
reports `l2` is also supported and does not need to be recreated, provided its
embeddings use Titan's default normalization.

Depending on the OpenSearch version, the response contains either `model_id`
directly or a `task_id`. If it returns a task, retrieve it:

```text
GET /_plugins/_ml/tasks/<TASK_ID>
```

When the task reaches `COMPLETED`, copy its `model_id`. That value becomes
`OPENSEARCH_SEMANTIC_MODEL_ID`.

### 4. Verify before running the project

Run these checks in order:

```text
GET  /_plugins/_ml/models/<MODEL_ID>
POST /_plugins/_ml/_predict/text_embedding/<MODEL_ID>
```

Use the `text_docs` request body shown in Option A. Only proceed to `make seed`
after that prediction succeeds.

## Troubleshooting

### `OPENSEARCH_SEMANTIC_MODEL_ID is required`

The `.env` file is absent or the value is blank. Copy `.env.example`, then set
the OpenSearch-generated model ID.

### `Failed to fetch model` or `Fail to find model`

Usually one of these is true:

- A connector ID, task ID, or model-group ID was used instead of the model ID.
- The model ID belongs to a different OpenSearch cluster.
- Model access control prevents the current backend role from seeing it.
- The model registration task did not complete.

Search the model registry again and use the matching hit's `_id`.

### `Model config is null for the remote model`

The model was registered manually without `model_config`. Register a new model
with `embedding_dimension: 1024` and the appropriate `space_type`, or use the
AWS console integration.

### `Some parameter placeholder not filled in payload: inputText`

The model can reach its connector, but the connector is not compatible with
the input contract used by neural and `semantic` field operations. OpenSearch
sends this shape:

```json
{
  "text_docs": ["hello world"]
}
```

Titan expects `inputText`, so the connector's `predict` action must contain:

```json
{
  "request_body": "{ \"inputText\": \"${parameters.inputText}\", \"dimensions\": ${parameters.dimensions}, \"normalize\": ${parameters.normalize}, \"embeddingTypes\": ${parameters.embeddingTypes} }",
  "pre_process_function": "connector.pre_process.bedrock.embedding",
  "post_process_function": "connector.post_process.bedrock_v2.embedding.float"
}
```

Inspect the model to find its connector, then inspect that connector:

```text
GET /_plugins/_ml/models/<MODEL_ID>
GET /_plugins/_ml/connectors/<CONNECTOR_ID>
```

If `pre_process_function` is absent or different, create a corrected connector
and register a new model using Option B. Put the new OpenSearch model ID in
`.env` as `OPENSEARCH_SEMANTIC_MODEL_ID`, restart the app, and seed again. Do
not work around this by hard-coding `inputText`: each catalog document needs a
different value supplied through `text_docs`.

### Model is `REGISTERED` or `UNDEPLOYED`

Deploy it and monitor the returned task:

```text
POST /_plugins/_ml/models/<MODEL_ID>/_deploy
GET  /_plugins/_ml/tasks/<TASK_ID>
```

### `403 Forbidden`

Check all relevant authorization layers:

- The setup identity can pass the connector role with `iam:PassRole`.
- The identity can call the OpenSearch domain APIs.
- Fine-grained access control maps the identity to `ml_full_access`.
- The connector role trust policy permits OpenSearch Service.
- The domain access policy allows the setup identity.

### Bedrock `AccessDeniedException`

Check that:

- The connector execution role allows `bedrock:InvokeModel`.
- The policy resource names `amazon.titan-embed-text-v2:0`.
- Titan V2 is available in the connector's Region.
- The connector Region, Bedrock endpoint Region, and IAM resource Region agree.

### Semantic field is unknown

The target domain is older than OpenSearch 3.1. Upgrade it or use the explicit
ingest-pipeline plus `knn_vector` implementation supported by the older version.

## Official references

- [AWS OpenSearch Bedrock integration](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cfn-template-bedrock.html)
- [AWS remote-inference CloudFormation setup](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cfn-template.html)
- [AWS OpenSearch connectors for AWS services](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ml-amazon-connector.html)
- [OpenSearch semantic field](https://docs.opensearch.org/latest/mappings/supported-field-types/semantic/)
- [OpenSearch text-embedding Predict API](https://docs.opensearch.org/latest/ml-commons-plugin/api/train-predict/predict/)
- [OpenSearch Search Model API](https://docs.opensearch.org/latest/ml-commons-plugin/api/model-apis/search-model/)
- [OpenSearch Get Model API](https://docs.opensearch.org/latest/ml-commons-plugin/api/model-apis/get-model/)
- [OpenSearch Register Model API](https://docs.opensearch.org/latest/ml-commons-plugin/api/model-apis/register-model/)
- [OpenSearch Titan connector blueprint](https://github.com/opensearch-project/ml-commons/blob/main/docs/remote_inference_blueprints/bedrock_connector_titan_embedding_blueprint.md)
- [Amazon Titan Text Embeddings V2](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)
