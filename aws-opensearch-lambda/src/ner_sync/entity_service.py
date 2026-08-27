from opensearchpy.exceptions import NotFoundError

from ner_sync.entity_event import DELETE_EVENT
from ner_sync.open_search_client import OpenSearchClient


class EntityService:
    """Create, replace or delete entity occurrences in OpenSearch."""

    def __init__(self, config):
        self.config = config
        self.client = OpenSearchClient(config).create_client()

    def apply_entity_event(self, entity_event):
        """Apply one database entity event to OpenSearch."""

        document_id = str(entity_event.sentenceEntityId)

        if entity_event.eventType == DELETE_EVENT:
            try:
                self.client.delete(
                    index=self.config.index_name,
                    id=document_id,
                )
                action = "deleted"
            except NotFoundError:
                # A repeated delete is successful because the document is gone.
                action = "already_deleted"

            return {
                "action": action,
                "sentenceEntityId": entity_event.sentenceEntityId,
            }

        response = self.client.index(
            index=self.config.index_name,
            id=document_id,
            body=entity_event.to_document(),
        )

        return {
            "action": response.get("result", "upserted"),
            "eventType": entity_event.eventType,
            "sentenceEntityId": entity_event.sentenceEntityId,
        }
