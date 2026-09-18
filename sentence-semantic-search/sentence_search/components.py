from sentence_search.config import config
from sentence_search.deletion_service import SentenceDeletionService
from sentence_search.match_service import SentenceMatchService
from sentence_search.opensearch_store import OpenSearchStore
from sentence_search.postgres_store import PostgresStore
from sentence_search.seed_service import SeedService
from sentence_search.sentence_service import SentenceService

opensearch_store = OpenSearchStore(config)
postgres_store = PostgresStore(config)
sentence_service = SentenceService(
    config,
    opensearch_store,
    postgres_store,
)
seed_service = SeedService(
    config,
    opensearch_store,
    postgres_store,
)
match_service = SentenceMatchService(
    config,
    opensearch_store,
    postgres_store,
)
deletion_service = SentenceDeletionService(
    opensearch_store,
    postgres_store,
)
