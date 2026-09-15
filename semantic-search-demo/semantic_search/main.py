from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from semantic_search.components import store
from semantic_search.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Wait for OpenSearch and prepare both indexes when the API starts."""

    store.wait_until_ready()
    store.ensure_indices()
    yield


app = FastAPI(
    title="NER Entity Semantic Search",
    version="1.0.0",
    description=(
        "Entity semantic matching using a deduplicated OpenSearch vector "
        "catalog backed by Amazon Titan Text Embeddings V2."
    ),
    lifespan=lifespan,
)
# Summary and search responses can contain hundreds of repeated field names.
# Compressing responses over 1 KB reduces transfer time without affecting the
# smaller health and administration responses.
app.add_middleware(GZipMiddleware, minimum_size=1_000, compresslevel=5)
app.include_router(router)
