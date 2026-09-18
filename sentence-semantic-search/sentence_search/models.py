from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, model_validator

from sentence_search.text import sentence_key


def utc_now() -> datetime:
    """Return the current UTC time."""

    return datetime.now(UTC)


class SentenceOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    applicationId: str
    tspId: str
    sectionName: str
    globalId: str
    sentIdLocal: int
    sentenceContent: str
    isTracer: bool = False
    isFormLanguage: bool = False
    sentenceKey: str | None = None
    sourceType: str
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    analysisGroup: str

    @model_validator(mode="after")
    def prepare_record(self):
        """Validate the sentence and fill values controlled by the API."""

        if not self.sentenceContent:
            raise ValueError("sentenceContent cannot be empty")
        if self.sentIdLocal < 0:
            raise ValueError("sentIdLocal cannot be negative")

        generated_key = sentence_key(self.sentenceContent)
        if self.sentenceKey and self.sentenceKey != generated_key:
            raise ValueError(
                "sentenceKey does not match sentenceContent; omit it and "
                "the application will generate it"
            )
        self.sentenceKey = generated_key
        current_time = utc_now()
        self.createdAt = self.createdAt or current_time
        self.updatedAt = self.updatedAt or current_time
        return self


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    csvPath: str = "data/seed.csv"
    reset: bool = False
