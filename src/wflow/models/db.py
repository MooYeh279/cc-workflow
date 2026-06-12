import uuid

from sqlalchemy import Column, String, Integer, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, relationship

from wflow.common.time_utils import utc_now_iso


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Workflow(Base):
    __tablename__ = "workflow"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    config = Column(Text, nullable=False, default="{}")
    status = Column(String, default="active")
    created_at = Column(String, default=utc_now_iso)
    updated_at = Column(String, default=utc_now_iso, onupdate=utc_now_iso)

    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")
    cron_jobs = relationship("CronJob", back_populates="workflow", cascade="all, delete-orphan")


class WorkflowRun(Base):
    __tablename__ = "workflow_run"

    id = Column(String, primary_key=True, default=_uuid)
    workflow_id = Column(String, ForeignKey("workflow.id"), nullable=False)
    status = Column(String, default="pending")
    current_node_id = Column(String, nullable=True)
    context = Column(Text, default="{}")
    started_at = Column(String, default=utc_now_iso)
    finished_at = Column(String, nullable=True)

    workflow = relationship("Workflow", back_populates="runs")
    node_executions = relationship("NodeExecution", back_populates="run", cascade="all, delete-orphan")
    logs = relationship("RunLog", back_populates="run", cascade="all, delete-orphan")


class NodeExecution(Base):
    __tablename__ = "node_execution"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, ForeignKey("workflow_run.id"), nullable=False)
    node_id = Column(String, nullable=False)
    type = Column(String, nullable=False)
    session_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    retry_count = Column(Integer, default=0)
    input = Column(Text, default="{}")
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(String, default=utc_now_iso)
    finished_at = Column(String, nullable=True)

    run = relationship("WorkflowRun", back_populates="node_executions")


class Session(Base):
    __tablename__ = "session"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, ForeignKey("workflow_run.id"), nullable=False)
    node_id = Column(String, nullable=False)
    session_path = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(String, default=utc_now_iso)


class CronJob(Base):
    __tablename__ = "cron_job"

    id = Column(String, primary_key=True, default=_uuid)
    workflow_id = Column(String, ForeignKey("workflow.id"), nullable=False)
    cron_expr = Column(String, nullable=False)
    enabled = Column(Integer, default=1)
    inputs = Column(Text, default="{}")
    last_run_id = Column(String, ForeignKey("workflow_run.id"), nullable=True)
    next_fire_at = Column(String, nullable=True)
    created_at = Column(String, default=utc_now_iso)

    workflow = relationship("Workflow", back_populates="cron_jobs")


class RunLog(Base):
    __tablename__ = "run_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("workflow_run.id"), nullable=False)
    node_id = Column(String, nullable=True)
    level = Column(String, default="info")
    message = Column(Text, nullable=False)
    timestamp = Column(String, default=utc_now_iso)

    run = relationship("WorkflowRun", back_populates="logs")
