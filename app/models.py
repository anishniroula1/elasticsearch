from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityEvent(BaseModel):
    """Data we expect for create, update and delete event."""

    model_config = ConfigDict(extra="forbid")

    eventType: Literal[
        "SENTENCE_ENTITY_CREATED",
        "SENTENCE_ENTITY_UPDATED",
        "SENTENCE_ENTITY_DELETED",
    ]
    sentenceEntityId: int
    applicationId: str = None
    tspId: str = None
    globalId: str = None
    entityId: str = None
    rawEntity: str = None
    normalizedText: str = None
    entityType: str = None
    possibleSanction: bool = None
    beginOffset: int = None
    endOffset: int = None
    score: float = Field(default=None, ge=0, le=1)
    source: str = "aws_comprehend"
    documentType: str = "Written Statement"
    createdAt: datetime = None
    updatedAt: datetime = None

    @model_validator(mode="after")
    def require_document_fields(self):
        """For create and update make sure all entity fields are passed."""

        if self.eventType == "SENTENCE_ENTITY_DELETED":
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
        missing = [
            field_name
            for field_name in required_fields
            if getattr(self, field_name) is None
        ]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
        return self
