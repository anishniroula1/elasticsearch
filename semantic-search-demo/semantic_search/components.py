from semantic_search.config import config
from semantic_search.opensearch_store import OpenSearchStore
from semantic_search.paginated_summary_service import (
    PaginatedSemanticSummaryService,
)
from semantic_search.summary_service import SemanticSummaryService
from semantic_search.text_search_service import SemanticTextSearchService

store = OpenSearchStore(config)
summary_service = SemanticSummaryService(store)
text_search_service = SemanticTextSearchService(store)
paginated_summary_service = PaginatedSemanticSummaryService(store)
