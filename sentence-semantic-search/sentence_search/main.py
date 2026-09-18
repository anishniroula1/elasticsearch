from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from sentence_search.components import opensearch_store, postgres_store
from sentence_search.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare OpenSearch and PostgreSQL before accepting requests."""

    postgres_store.wait_until_ready()
    postgres_store.init_schema()
    opensearch_store.wait_until_ready()
    opensearch_store.ensure_indices()
    yield
    postgres_store.close()


app = FastAPI(
    title="Semantic Sentence Matching",
    version="1.0.0",
    description=(
        "Sentence-level semantic matching with Amazon Titan, OpenSearch, "
        "and prepared PostgreSQL matching-ID lists."
    ),
    lifespan=lifespan,
)
# Match pages contain repeated field names and sentence text. Compressing only
# responses above 1 KB reduces transfer time without touching small responses.
app.add_middleware(GZipMiddleware, minimum_size=1_000, compresslevel=5)
app.include_router(router)
