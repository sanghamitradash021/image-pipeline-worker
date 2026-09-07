from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.jobs import router as jobs_router
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Job Processing Service", lifespan=lifespan)
app.include_router(jobs_router)


@app.get("/health")
def health():
    return {"status": "ok"}
