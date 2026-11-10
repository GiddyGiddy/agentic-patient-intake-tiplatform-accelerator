import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import router
from app.api.federation import router as federation_router
from app.api.reference_auth import router as reference_auth_router
from app.core import db
from app.core.kim_outbox import retry_pending
from app.core.session import store
from config import settings

log = logging.getLogger(__name__)


def _housekeeping() -> None:
    try:
        store.purge_expired()
        retry_pending()
    except Exception:
        log.exception("housekeeping failed")


async def _worker() -> None:
    while True:
        await asyncio.sleep(settings.kim_retry_interval_s)
        await asyncio.to_thread(_housekeeping)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(db.init_db)
    task = asyncio.create_task(_worker())
    yield
    task.cancel()


app = FastAPI(title="Agentic Patient Intake GematikPlatform", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)
# The LangGraph workflow (app.agents.graph.intake_graph) is invoked in app/api/endpoints.py: POST /api/chat
app.include_router(router, prefix="/api")
app.include_router(federation_router)
app.include_router(reference_auth_router)


@app.get("/health")
def health(response: Response):
    ok = db.is_healthy()
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", "database": ok}
