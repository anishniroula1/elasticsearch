from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from sentence_search.components import opensearch_store
from sentence_search.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare OpenSearch before accepting requests."""

    opensearch_store.wait_until_ready()
    opensearch_store.ensure_indices()
    yield


app = FastAPI(
    title="Semantic Sentence Matching",
    version="1.0.0",
    description=(
        "Sentence-level semantic matching with Amazon Titan and OpenSearch."
    ),
    lifespan=lifespan,
)
# Match pages contain repeated field names and sentence text. Compressing only
# responses above 1 KB reduces transfer time without touching small responses.
app.add_middleware(GZipMiddleware, minimum_size=1_000, compresslevel=5)
app.include_router(router)
