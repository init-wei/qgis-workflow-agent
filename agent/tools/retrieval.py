#!/usr/bin/env python3
"""API documentation retrieval contracts for controlled execution."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agent.tools.tool_contracts import ToolCallPlan, ToolObservation


class ApiDocRecord(BaseModel):
    """A normalized API documentation record used as execution evidence."""

    api_name: str = Field(description="API name or symbol")
    library: str = Field(description="Source library such as GDAL or PyQGIS")
    content: str = Field(description="Documentation content")
    source: str = Field(default="", description="Retriever or storage source")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional document metadata"
    )


class RetrievalContext(BaseModel):
    """Step-level API retrieval evidence and query context."""

    step_id: int = Field(description="TaskPlan step ID")
    tool_name: str = Field(description="MCP tool name")
    query: str = Field(description="Retriever query text")
    query_source: str = Field(description="Query source such as initial or error")
    candidate_apis: list[str] = Field(
        default_factory=list, description="Candidate API names from planning"
    )
    records: list[ApiDocRecord] = Field(
        default_factory=list, description="Retrieved API documentation records"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional retrieval metadata"
    )


class ApiDocRetriever(Protocol):
    """Protocol for retrieving API documentation for a context."""

    async def retrieve(self, context: RetrievalContext) -> RetrievalContext:
        """Return a context populated with records."""


class RetrievalQueryBuilder:
    """Build initial and error-driven API documentation queries."""

    def build_initial_context(self, call: ToolCallPlan) -> RetrievalContext:
        candidate_apis = self._candidate_apis(call)
        argument_names = self._argument_names(call.arguments)
        query_parts = [call.tool_name, *candidate_apis, *argument_names]
        return RetrievalContext(
            step_id=call.step_id,
            tool_name=call.tool_name,
            query=" ".join(part for part in query_parts if part),
            query_source="initial",
            candidate_apis=candidate_apis,
            metadata={"argument_names": argument_names},
        )

    def build_error_context(
        self, call: ToolCallPlan, observation: ToolObservation
    ) -> RetrievalContext:
        candidate_apis = self._candidate_apis(call)
        argument_names = self._argument_names(call.arguments)
        error_type = observation.error_type or "unknown_error"
        message = observation.message or ""
        query_parts = [
            call.tool_name,
            error_type,
            message,
            *candidate_apis,
            *argument_names,
        ]
        return RetrievalContext(
            step_id=call.step_id,
            tool_name=call.tool_name,
            query=" ".join(part for part in query_parts if part),
            query_source="error",
            candidate_apis=candidate_apis,
            metadata={
                "argument_names": argument_names,
                "error_type": error_type,
                "message": message,
            },
        )

    def _candidate_apis(self, call: ToolCallPlan) -> list[str]:
        candidates = []
        for key in ("candidate_gdal_apis", "candidate_pyqgis_apis"):
            values = call.metadata.get(key, [])
            if isinstance(values, list):
                candidates.extend(str(value) for value in values if value)
        return list(dict.fromkeys(candidates))

    def _argument_names(self, value: Any) -> list[str]:
        names: list[str] = []

        def visit(node: Any, key: str | None = None) -> None:
            if key is not None:
                names.append(key)
            if isinstance(node, dict):
                for nested_key, nested_value in node.items():
                    visit(nested_value, str(nested_key))
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(value)
        return list(dict.fromkeys(names))


class DefaultApiDocRetriever:
    """Default retriever that reuses the existing API-RAG prototype."""

    async def retrieve(self, context: RetrievalContext) -> RetrievalContext:
        gdal_apis = [
            api_name
            for api_name in context.candidate_apis
            if api_name.startswith("osgeo.") or api_name.startswith("gdal.")
        ]
        pyqgis_apis = [
            api_name for api_name in context.candidate_apis if api_name not in gdal_apis
        ]

        records: list[ApiDocRecord] = []
        if gdal_apis:
            from agent.tools.rag import search_gdal_docs

            gdal_docs = await asyncio.to_thread(search_gdal_docs, gdal_apis)
            records.extend(
                self._record_from_mapping(doc, default_library="GDAL")
                for doc in gdal_docs
            )
        if pyqgis_apis:
            from agent.tools.rag import search_pyqgis_docs_async

            records.extend(
                self._record_from_mapping(doc, default_library="PyQGIS")
                for doc in await search_pyqgis_docs_async(pyqgis_apis)
            )

        return context.model_copy(update={"records": records})

    def _record_from_mapping(
        self, doc: dict[str, Any], default_library: str
    ) -> ApiDocRecord:
        api_name = str(doc.get("api_name", ""))
        library = str(doc.get("library") or doc.get("doc_type") or default_library)
        content_parts = [
            str(doc.get("description") or doc.get("content") or ""),
            str(doc.get("params") or ""),
            str(doc.get("example_code") or ""),
        ]
        metadata = {
            key: value
            for key, value in doc.items()
            if key not in {"api_name", "library", "doc_type", "description", "content"}
        }
        return ApiDocRecord(
            api_name=api_name,
            library=library,
            content="\n\n".join(part for part in content_parts if part),
            source="api_rag",
            metadata=metadata,
        )
