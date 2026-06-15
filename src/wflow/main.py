"""FastAPI application factory."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from wflow.models.db import Base


def _ensure_db_dir(db_url: str) -> None:
    """Create the parent directory for a SQLite database if it doesn't exist."""
    if "sqlite" in db_url and ":///" in db_url:
        # Extract path from URL like sqlite+aiosqlite:///./data/workflows.db
        path_str = db_url.split(":///", 1)[1]
        db_path = Path(path_str)
        db_path.parent.mkdir(parents=True, exist_ok=True)


def create_app(
    db_url: str | None = None,
) -> FastAPI:
    if db_url is None:
        db_url = os.environ.get(
            "WFLOW_DB_URL", "sqlite+aiosqlite:///./data/workflows.db"
        )
    _ensure_db_dir(db_url)
    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize logging
        from wflow.logging import setup_logging, get_server_logger
        setup_logging()
        logger = get_server_logger()
        logger.info("WFlow server starting...")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialized")

        # Wire up adapters and node type handlers
        from wflow.adapters.claude_cli import ClaudeCLI
        from wflow.adapters.opencode_cli import OpenCodeCLI
        from wflow.adapters.script_runner import ScriptRunner
        from wflow.engine.node_handler import (
            ClaudeHandler,
            OpenCodeHandler,
            ScriptHandler,
            HumanReviewHandler,
        )

        claude_cli = ClaudeCLI()
        opencode_cli = OpenCodeCLI()
        script_runner = ScriptRunner()

        handlers = {
            "claude": ClaudeHandler(claude_cli),
            "opencode": OpenCodeHandler(opencode_cli),
            "script": ScriptHandler(script_runner),
            "human_review": HumanReviewHandler(),
        }
        logger.info("Adapters initialized: ClaudeCLI, OpenCodeCLI, ScriptRunner")

        # Detect project config directory
        from wflow.common.workspace import detect_wflow_dir
        project_dir = os.environ.get("WFLOW_PROJECT_DIR", os.getcwd())
        wflow_dir = detect_wflow_dir(project_dir)
        if wflow_dir:
            logger.info(f"Project .wflow directory detected: {wflow_dir}")
        else:
            logger.info("No .wflow directory detected")

        # Wire up cron scheduler
        from wflow.engine.scheduler import WorkflowScheduler
        scheduler = WorkflowScheduler(SessionLocal)
        scheduler.set_adapters(handlers, wflow_dir)
        scheduler.start()
        await scheduler.restore_jobs(SessionLocal)
        logger.info("Cron scheduler started — restored enabled jobs")

        app.state.engine = engine
        app.state.SessionLocal = SessionLocal
        app.state.handlers = handlers
        app.state.wflow_dir = wflow_dir
        app.state.scheduler = scheduler

        yield

        logger.info("WFlow server shutting down...")
        scheduler.shutdown()
        await engine.dispose()

    app = FastAPI(title="WFlow", version="0.1.0", lifespan=lifespan)

    from wflow.api.cron import router as cron_router
    from wflow.api.runs import router as run_router
    from wflow.api.status import router as status_router
    from wflow.api.workflows import router as workflow_router

    app.include_router(status_router, prefix="/api/v1")
    app.include_router(workflow_router, prefix="/api/v1")
    app.include_router(run_router, prefix="/api/v1")
    app.include_router(cron_router, prefix="/api/v1")

    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount(
            "/", StaticFiles(directory=str(web_dir), html=True), name="web"
        )

    return app
