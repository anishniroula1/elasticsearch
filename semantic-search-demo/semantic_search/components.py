from semantic_search.config import config
from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.service import SemanticSearchService


store = OpenSearchStore(config)
service = SemanticSearchService(store)
