import logging
import os

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth_api import router as auth_router
from app.api.billing import router as billing_router
from app.api.broker_portal import router as broker_router
from app.api.kb_admin import router as kb_router
from app.api.session_ws import router as session_router
from app.api.workbench import router as workbench_router
from app.config import settings
from app.db import init_db
from app.services import pdf_export, session_store, zone_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whiteboard-advisor")

app = FastAPI(title="WhiteboardAdvisor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


@app.on_event("startup")
async def _start_scheduler() -> None:
    """内建定时作业(到期扫描/每日简报);WB_DISABLE_SCHEDULER=1 关闭(测试)。"""
    import asyncio

    from app.services import scheduler

    if scheduler.enabled():
        asyncio.get_running_loop().create_task(scheduler.scheduler_loop())

app.include_router(session_router)
app.include_router(broker_router)
app.include_router(workbench_router)
app.include_router(kb_router)
app.include_router(auth_router)
app.include_router(billing_router)

# 桌面版/生产:前端构建产物挂在 /app(存在才挂,开发模式仍走 Vite 5173)
from app.paths import IS_FROZEN, resource_path

_FRONTEND_DIST = (
    resource_path("frontend_dist")
    if IS_FROZEN
    else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "frontend",
        "dist",
    )
)
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/app", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "llm": settings.has_llm,
        "speech": settings.has_speech,
    }


@app.get("/api/session/{session_id}/pdf")
async def session_pdf(session_id: str) -> Response:
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    pdf = pdf_export.render_session_pdf(session)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="plan-{session_id[:8]}.pdf"'},
    )


@app.post("/api/session/{session_id}/share")
async def create_share(session_id: str) -> dict:
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    token = session_store.ensure_share(session)
    return {"token": token, "expiresAt": session.share_expires_at.isoformat()}


@app.get("/api/share/{token}")
async def read_share(token: str) -> dict:
    session = session_store.resolve_share(token)
    if not session:
        raise HTTPException(status_code=410, detail="share link invalid or expired")
    # 脱敏:只返回白板与解说,不含任何联系方式
    return {
        "templateId": session.template_id,
        "language": session.language.value,
        "zones": zone_engine.zone_meta(session),
        "zoneData": {
            zid: {"data": z.data, "version": z.version}
            for zid, z in session.zones.items()
            if z.data
        },
        "dialogue": [
            {"role": e.role, "content": e.content}
            for e in session.dialogue_history
        ],
    }
