import os

os.environ.setdefault("OPENSEARCH_HOST", "search.example.test")
os.environ.setdefault("OPENSEARCH_SEMANTIC_MODEL_ID", "test-model")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://sentence:sentence@localhost:5434/sentence_matches",
)
