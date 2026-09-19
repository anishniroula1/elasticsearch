from sentence_search.config import config
from sentence_search.deletion_service import SentenceDeletionService
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.seed_service import SeedService
from sentence_search.sentence_key_search_service import SentenceKeySearchService
from sentence_search.sentence_service import SentenceService
from sentence_search.sentence_summary_service import SentenceSummaryService

opensearch_store = OpenSearchStore(config)
sentence_summary_service = SentenceSummaryService(opensearch_store)
sentence_service = SentenceService(opensearch_store)
seed_service = SeedService(
    config,
    opensearch_store,
)
deletion_service = SentenceDeletionService(opensearch_store)
sentence_key_search_service = SentenceKeySearchService(opensearch_store)
