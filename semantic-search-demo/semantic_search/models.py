from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityOccurrence(BaseModel):
    """The original entity-occurrence record before its vector is generated."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sentenceEntityId: int = Field(ge=1)
    applicationId: str = Field(min_length=1, max_length=256)
    tspId: str = Field(min_length=1, max_length=256)
    globalId: str = Field(min_length=1, max_length=256)
    entityId: str = Field(min_length=1, max_length=256)
    rawEntity: str = Field(min_length=1, max_length=50_000)
    normalizedText: str = Field(min_length=1, max_length=50_000)
    entitySearchText: str = Field(min_length=1, max_length=50_000)
    entityType: str = Field(min_length=1, max_length=100)
    possibleSanction: bool = False
    beginOffset: int = Field(ge=0)
    endOffset: int = Field(ge=0)
    score: float = Field(ge=0, le=1)
    source: str = Field(min_length=1, max_length=256)
    documentType: str = Field(
        default="Written Statement",
        min_length=1,
        max_length=256,
    )
    createdAt: datetime
    updatedAt: datetime

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "EntityOccurrence":
        if self.endOffset < self.beginOffset:
            raise ValueError(
                "endOffset must be greater than or equal to beginOffset"
            )
        return self
