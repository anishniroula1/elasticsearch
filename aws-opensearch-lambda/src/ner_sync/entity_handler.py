import logging

from pydantic import ValidationError

from ner_sync.config import Config
from ner_sync.entity_event import EntityEvent
from ner_sync.entity_service import EntityService


logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Validate the database trigger event and apply it to OpenSearch."""

    try:
        # Lambda changes the JSON object from the DB trigger into a Python dict.
        entity_event = EntityEvent.model_validate(event)
    except ValidationError:
        # Do not log the full validation error because events may contain PII.
        logger.error(
            "Entity event validation failed requestId=%s",
            getattr(context, "aws_request_id", None),
        )
        # Raising lets Lambda retry and send the event to its failure destination.
        raise

    logger.info(
        "Processing %s for sentenceEntityId=%s",
        entity_event.eventType,
        entity_event.sentenceEntityId,
    )
    try:
        config = Config.get_config()
        service = EntityService(config)
        result = service.apply_entity_event(entity_event)
    except Exception:
        # Log useful IDs, but do not log the entity data because it may have PII.
        logger.exception(
            "Entity event failed eventType=%s sentenceEntityId=%s requestId=%s",
            entity_event.eventType,
            entity_event.sentenceEntityId,
            getattr(context, "aws_request_id", None),
        )
        # Lambda can now retry or send this event to its failure destination.
        raise

    result["requestId"] = getattr(context, "aws_request_id", None)
    return result
