from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CREATE_EVENT = "SENTENCE_ENTITY_CREATED"
UPDATE_EVENT = "SENTENCE_ENTITY_UPDATED"
DELETE_EVENT = "SENTENCE_ENTITY_DELETED"


class EntityEvent(BaseModel):
    """Validate create, update and delete data sent to Lambda."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    eventType: Literal[
        "SENTENCE_ENTITY_CREATED",
        "SENTENCE_ENTITY_UPDATED",
        "SENTENCE_ENTITY_DELETED",
    ]
    schemaVersion: int = Field(default=1, ge=1)
    eventId: str = None
    occurredAt: datetime = None
    sentenceEntityId: int = Field(gt=0)
    applicationId: str = None
    tspId: str = None
    globalId: str = None
    entityId: str = None
    rawEntity: str = None
    normalizedText: str = None
    entitySearchText: str = None
    entityType: str = None
    possibleSanction: bool = None
    beginOffset: int = Field(default=None, ge=0)
    endOffset: int = Field(default=None, ge=0)
    score: float = Field(default=None, ge=0, le=1)
    source: str = "aws_comprehend"
    documentType: str = "Written Statement"
    createdAt: datetime = None
    updatedAt: datetime = None

    @model_validator(mode="after")
    def validate_event_fields(self):
        """Delete needs an ID, create and update need the full entity."""

        if self.eventType == DELETE_EVENT:
            return self

        required_fields = [
            "applicationId",
            "tspId",
            "globalId",
            "entityId",
            "rawEntity",
            "normalizedText",
            "entityType",
            "possibleSanction",
            "beginOffset",
            "endOffset",
            "score",
        ]
        missing_fields = [
            field_name
            for field_name in required_fields
            if getattr(self, field_name) is None
            or getattr(self, field_name) == ""
        ]
        if missing_fields:
            missing = ", ".join(missing_fields)
            raise ValueError(f"Missing required fields: {missing}")

        if self.endOffset < self.beginOffset:
            raise ValueError("endOffset cannot be less than beginOffset")

        return self

    def to_document(self):
        """Change a create or update event into an OpenSearch document."""

        timestamp = datetime.now(timezone.utc).isoformat()
        document = self.model_dump(
            mode="json",
            exclude={
                "eventType",
                "schemaVersion",
                "eventId",
                "occurredAt",
            },
            exclude_none=True,
        )
        document["entitySearchText"] = (
            self.entitySearchText or self.normalizedText
        )
        document["createdAt"] = (
            self.createdAt.isoformat() if self.createdAt else timestamp
        )
        document["updatedAt"] = (
            self.updatedAt.isoformat() if self.updatedAt else timestamp
        )
        return document
