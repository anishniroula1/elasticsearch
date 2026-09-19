import os

os.environ.setdefault("OPENSEARCH_HOST", "search.example.test")
os.environ.setdefault("OPENSEARCH_SEMANTIC_MODEL_ID", "test-model")
os.environ.setdefault("OPENSEARCH_IVF_MODEL_ID", "test-ivf-model")
os.environ.setdefault("OPENSEARCH_IVF_NPROBES", "64")
