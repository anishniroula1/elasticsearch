from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import config
from app.routes import router
from app.search_client import client, ensure_index, wait_until_ready
from app.seed import seed_documents


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Connect with OpenSearch and seed data when app starts."""

    wait_until_ready()
    ensure_index()

    if config.auto_seed and client.count(index=config.index_alias)["count"] == 0:
        seed_documents(config.seed_count)

    yield


app = FastAPI(
    title="NER Similarities with OpenSearch",
    version="1.0.0",
    description=(
        "Local demo for NER entity occurrences, entity counts, matching cases, "
        "shared entities, and fuzzy entity search."
    ),
    lifespan=lifespan,
)
app.include_router(router)
