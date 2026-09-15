from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class EntityOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sentenceEntityId: int
    applicationId: str
    tspId: str
    globalId: str
    entityId: str
    rawEntity: str
    normalizedText: str
    entitySearchText: str
    entityType: str
    possibleSanction: bool = False
    beginOffset: int
    endOffset: int
    score: float
    source: str
    documentType: str = "Written Statement"
    createdAt: datetime
    updatedAt: datetime

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "EntityOccurrence":
        """Make sure the end position is not before the start position."""

        if self.endOffset < self.beginOffset:
            raise ValueError(
                "endOffset must be greater than or equal to beginOffset"
            )
        return self
