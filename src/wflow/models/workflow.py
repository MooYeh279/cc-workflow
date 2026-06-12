from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class ToolConfig(BaseModel):
    allowed: list[str] = Field(default_factory=list)
    disallowed: list[str] = Field(default_factory=list)


class RetryConfig(BaseModel):
    max_retries: int = 3
    on_error: list[str] = Field(default_factory=lambda: ["timeout", "parse_error"])


class AgentNode(BaseModel):
    id: str
    name: str
    type: str = "agent"
    prompt: str
    tools: ToolConfig = Field(default_factory=ToolConfig)
    model: str = "sonnet"
    output_schema: dict[str, Any] = Field(alias="output")
    retry: RetryConfig = Field(default_factory=RetryConfig)

    class Config:
        populate_by_name = True


class ScriptNode(BaseModel):
    id: str
    name: str
    type: str = "script"
    command: str
    timeout_seconds: int = 300
    output_schema: dict[str, Any] = Field(alias="output")
    retry: RetryConfig = Field(default_factory=RetryConfig)

    class Config:
        populate_by_name = True
        extra = "allow"


class Edge(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: Optional[str] = None
    condition: Optional[str] = None

    class Config:
        populate_by_name = True


class WorkflowConfig(BaseModel):
    max_retries: int = 3
    retry_delay_seconds: int = 30
    timeout_seconds: int = 1800


class WorkflowSpec(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    config: WorkflowConfig = Field(default_factory=WorkflowConfig)
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("nodes")
    @classmethod
    def validate_unique_ids(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [n["id"] for n in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate node IDs found: {ids}")
        return v

    @field_validator("edges")
    @classmethod
    def validate_edge_refs(cls, v: list[dict[str, Any]], info) -> list[dict[str, Any]]:
        node_ids = {n["id"] for n in info.data.get("nodes", [])}
        for edge in v:
            if edge["from"] not in node_ids:
                raise ValueError(f"Edge '{edge['id']}' references unknown 'from' node: {edge['from']}")
            if edge.get("to") is not None and edge["to"] not in node_ids:
                raise ValueError(f"Edge '{edge['id']}' references unknown 'to' node: {edge['to']}")
        return v

    def get_node(self, node_id: str) -> dict[str, Any]:
        for n in self.nodes:
            if n["id"] == node_id:
                return n
        raise KeyError(f"Node not found: {node_id}")

    def get_outgoing_edges(self, node_id: str) -> list[dict[str, Any]]:
        return [e for e in self.edges if e["from"] == node_id]
