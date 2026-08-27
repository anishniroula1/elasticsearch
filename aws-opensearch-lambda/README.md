# NER Database Event Lambda for Amazon OpenSearch Service

This is a separate Lambda project for sending NER database changes to Amazon OpenSearch Service.

The database trigger sends one JSON object to Lambda. Lambda validates the object and creates, replaces, or deletes one OpenSearch document. This project only handles that database event flow.

## Project structure

```text
aws-opensearch-lambda/
├── opensearch/
│   └── index-definition.json
├── sample-events/
│   ├── create.json
│   ├── delete.json
│   └── update.json
├── src/
│   ├── ner_sync/
│   │   ├── config.py
│   │   ├── entity_handler.py
│   │   ├── entity_service.py
│   │   ├── entity_event.py
│   │   └── open_search_client.py
│   └── requirements.txt
├── tests/
├── Makefile
├── pyproject.toml
└── template.yaml
```

## Event flow

```text
Database row change
        ↓
Database trigger creates a JSON object
        ↓
Lambda validates the object with Pydantic
        ↓
Lambda creates, replaces, or deletes the OpenSearch document
```

The handler expects the event fields directly at the top level. It does not unwrap an API Gateway body, a queue record, or another event wrapper.

The handler reads `Config` once and passes it into `EntityService`. The service passes the same config into `OpenSearchClient`, so the settings are not loaded again in the lower classes.

## Supported events

```text
SENTENCE_ENTITY_CREATED
SENTENCE_ENTITY_UPDATED
SENTENCE_ENTITY_DELETED
```

Create example:

```json
{
  "eventType": "SENTENCE_ENTITY_CREATED",
  "schemaVersion": 1,
  "eventId": "50b6171d-0945-4e3d-a90a-d8aa60916330",
  "occurredAt": "2026-08-25T12:30:00Z",
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

An update has the same entity fields with `eventType` set to `SENTENCE_ENTITY_UPDATED`.

Delete only needs the document ID:

```json
{
  "eventType": "SENTENCE_ENTITY_DELETED",
  "schemaVersion": 1,
  "eventId": "c0c99e4c-15ce-469b-806e-5a96edfc51b1",
  "occurredAt": "2026-08-25T12:35:00Z",
  "sentenceEntityId": 200001
}
```

`schemaVersion`, `eventId`, and `occurredAt` are optional event metadata. They are checked but are not saved in the OpenSearch document.

## Model validation

The `EntityEvent` Pydantic model checks the database event before OpenSearch is called:

- delete needs a positive `sentenceEntityId`
- create and update need the complete entity data
- `score` must be from 0 to 1
- offsets cannot be negative
- `endOffset` cannot be less than `beginOffset`
- required text fields cannot be blank
- fields that are not part of the model are rejected

An invalid event throws a validation error. This lets the database trigger or Lambda retry policy handle the failure instead of writing incomplete data.

OpenSearch and STS errors are logged with the event type, entity ID, and Lambda request ID. The handler throws the error again so Lambda can retry it or send it to the configured failure destination. The entity data is not written to the log because it may contain PII.

## OpenSearch document behavior

| Event | OpenSearch action |
|---|---|
| Created | Index the document using `sentenceEntityId` as its document ID |
| Updated | Replace the document with the same `sentenceEntityId` |
| Deleted | Delete the document with that `sentenceEntityId` |

Using the same document ID makes create and update retries safe. A repeated delete returns `already_deleted` if the document is already gone.

## OpenSearch connection

Lambda uses STS to assume the configured OpenSearch access role. It creates a boto3 session with those temporary credentials and passes the session credentials to `AWSV4SignerAuth`. No OpenSearch username or password is stored here.

Environment variables:

| Name | Example | Notes |
|---|---|---|
| `OPENSEARCH_HOST` | `search-ner-prod-abc.us-east-1.es.amazonaws.com` | OpenSearch hostname |
| `OPENSEARCH_INDEX` | `ner_entity_occurrences` | Index name or write alias |
| `OPENSEARCH_SERVICE` | `es` | Managed OpenSearch Service uses `es` |
| `AWS_REGION` | `us-east-1` | Lambda sets this automatically |
| `IAM_ACCESS_ROLE` | `arn:aws:iam::123456789012:role/ner-opensearch-access` | Role assumed through STS |

The Lambda execution role needs `sts:AssumeRole` permission for the access role. The access role trust policy must trust the Lambda execution role. The access role also needs permission to write to the OpenSearch domain.

The OpenSearch domain policy must allow the access role. If fine-grained access control is enabled, map the access role to an OpenSearch role with write access to this index.

If the domain is private, add `VpcConfig` to the Lambda function and use subnets and security groups that can reach the domain over HTTPS.

## Create the index first

`opensearch/index-definition.json` has the mapping used by this project. Apply it before sending database events. It creates a physical index called `ner_entity_occurrences-v1` and the `ner_entity_occurrences` write alias.

The example uses four primary shards and two replicas. Two replicas need at least three data nodes. Confirm these values with production load tests.

## Local tests

Requirements:

- Python 3.12
- uv
- AWS SAM CLI for build and deployment

Run the tests:

```bash
cd aws-opensearch-lambda
make test
```

Build and validate the SAM application:

```bash
make build
make validate
```

The tests are kept in one `tests/test_entity_lambda.py` file. Most scenarios start from `lambda_handler` and use small fake STS, boto3 session, and OpenSearch clients. They do not connect to AWS.
Pytest checks line and branch coverage and fails when total coverage is below 100%.

## Deploy with AWS SAM

```bash
sam build
sam deploy --guided
```

SAM asks for the OpenSearch host, index name, and IAM access role ARN. The stack creates the Lambda function and gives its execution role permission to assume the access role.

The database trigger is not created here because its database type and trigger method belong to the database project. Configure it to send the event object shown above to the Lambda function ARN from the stack output.

Test the handler directly after deployment:

```bash
aws lambda invoke \
  --function-name YOUR_ENTITY_SYNC_FUNCTION \
  --cli-binary-format raw-in-base64-out \
  --payload file://sample-events/create.json \
  response.json
```

## Production checklist

- Create the index and alias before enabling database events.
- Let the Lambda execution role assume the IAM access role.
- Give the IAM access role permission to write to OpenSearch.
- Allow the IAM access role in the OpenSearch domain policy.
- Configure VPC networking when the OpenSearch domain is private.
- Add a failure destination or dead-letter queue if the database trigger supports one.
- Add CloudWatch alarms for errors, throttles, and duration.
- Keep the event contract and `schemaVersion` the same between the database and Lambda projects.

## AWS reference

- [Amazon OpenSearch Service signed Python client](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-clients.html)
- [Boto3 STS assume_role](https://docs.aws.amazon.com/boto3/latest/reference/services/sts/client/assume_role.html)
