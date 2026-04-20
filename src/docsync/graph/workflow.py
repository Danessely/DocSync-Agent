from __future__ import annotations

import logging

from langsmith import traceable
from langgraph.graph import END, START, StateGraph

from ..config import Settings
from .nodes import WorkflowNodes
from .router import (
    route_after_analyze,
    route_after_generate,
    route_after_ingest,
    route_after_retrieve,
    route_after_validate,
)
from .state import PRSessionState

LOGGER = logging.getLogger(__name__)


class DocSyncWorkflow:
    def __init__(
        self,
        settings: Settings,
        github_client,
        llm_client,
        telegram_client=None,
        state_store=None,
    ) -> None:
        self._settings = settings
        self._nodes = WorkflowNodes(
            settings,
            github_client,
            llm_client,
            telegram_client=telegram_client,
            state_store=state_store,
        )
        self._graph = self._build_graph()
        self._state_store = state_store

    def _build_graph(self):
        graph = StateGraph(PRSessionState)
        graph.add_node("ingest", self._nodes.ingest)
        graph.add_node("load_pr", self._nodes.load_pr)
        graph.add_node("analyze_diff", self._nodes.analyze_diff)
        graph.add_node("retrieve_docs", self._nodes.retrieve_docs)
        graph.add_node("build_context", self._nodes.build_context)
        graph.add_node("generate", self._nodes.generate)
        graph.add_node("build_patch", self._nodes.build_patch)
        graph.add_node("validate", self._nodes.validate)
        graph.add_node("publish", self._nodes.publish)
        graph.add_node("clarify", self._nodes.clarify)
        graph.add_node("complete", self._nodes.complete)

        graph.add_edge(START, "ingest")
        graph.add_conditional_edges(
            "ingest",
            route_after_ingest,
            {"load_pr": "load_pr", "complete": "complete"},
        )
        graph.add_edge("load_pr", "analyze_diff")
        graph.add_conditional_edges(
            "analyze_diff",
            route_after_analyze,
            {"retrieve_docs": "retrieve_docs", "publish": "publish"},
        )
        graph.add_conditional_edges(
            "retrieve_docs",
            route_after_retrieve,
            {"build_context": "build_context", "publish": "publish"},
        )
        graph.add_edge("build_context", "generate")
        graph.add_conditional_edges(
            "generate",
            route_after_generate,
            {"build_patch": "build_patch", "publish": "publish", "clarify": "clarify"},
        )
        graph.add_edge("build_patch", "validate")
        graph.add_conditional_edges(
            "validate",
            route_after_validate,
            {"publish": "publish", "clarify": "clarify"},
        )
        graph.add_edge("publish", "complete")
        graph.add_edge("clarify", "complete")
        graph.add_edge("complete", END)
        return graph.compile()

    @traceable(run_type="chain", name="docsync_graph_invoke")
    def invoke(self, payload: dict) -> PRSessionState:
        LOGGER.info("workflow_invoke")
        return self._graph.invoke({"event_payload": payload})

    @traceable(run_type="chain", name="docsync_run")
    def run_once(self, payload: dict) -> PRSessionState:
        state: PRSessionState = {"event_payload": payload}
        state.update(self._nodes.ingest(state))
        if route_after_ingest(state) == "complete":
            state.update(self._nodes.complete(state))
            return state

        state.update(self._nodes.load_pr(state))
        state.update(self._nodes.analyze_diff(state))
        if route_after_analyze(state) == "publish":
            state.update(self._nodes.publish(state))
            state.update(self._nodes.complete(state))
            return state

        state.update(self._nodes.retrieve_docs(state))
        if route_after_retrieve(state) == "publish":
            state.update(self._nodes.publish(state))
            state.update(self._nodes.complete(state))
            return state

        state.update(self._nodes.build_context(state))
        state.update(self._nodes.generate(state))
        next_step = route_after_generate(state)
        if next_step == "clarify":
            state.update(self._nodes.clarify(state))
            state.update(self._nodes.complete(state))
            return state
        if next_step == "publish":
            state.update(self._nodes.publish(state))
            state.update(self._nodes.complete(state))
            return state

        state.update(self._nodes.build_patch(state))
        state.update(self._nodes.validate(state))
        if route_after_validate(state) == "clarify":
            state.update(self._nodes.clarify(state))
            state.update(self._nodes.complete(state))
            return state
        if route_after_validate(state) == "publish":
            state.update(self._nodes.publish(state))
        state.update(self._nodes.complete(state))
        return state

    @traceable(run_type="chain", name="docsync_resume_from_clarification")
    def resume_from_clarification(self, session_id: str, reply_text: str) -> PRSessionState:
        if self._state_store is None:
            raise RuntimeError("state_store_not_configured")

        pending = self._state_store.get_pending_clarification(session_id)
        if pending is None:
            raise KeyError(f"unknown_session:{session_id}")

        state = pending.state
        generation_input = state.get("generation_input")
        if generation_input is None:
            state.update(self._nodes.build_context(state))
            generation_input = state["generation_input"]

        state["generation_input"] = generation_input.model_copy(update={"human_clarification": reply_text})
        state.pop("clarification_result", None)
        state.pop("publish_result", None)
        state.pop("validation_report", None)
        state.pop("doc_patch", None)
        state.pop("outcome", None)
        state.pop("error_code", None)

        state.update(self._nodes.generate(state))
        next_step = route_after_generate(state)
        if next_step == "clarify":
            state.update(self._nodes.clarify(state))
            state.update(self._nodes.complete(state))
            return state
        if next_step == "publish":
            state.update(self._nodes.publish(state))
            self._state_store.clear_pending_clarification(session_id)
            state.update(self._nodes.complete(state))
            return state

        state.update(self._nodes.build_patch(state))
        state.update(self._nodes.validate(state))
        if route_after_validate(state) == "clarify":
            state.update(self._nodes.clarify(state))
            state.update(self._nodes.complete(state))
            return state
        if route_after_validate(state) == "publish":
            state.update(self._nodes.publish(state))
            self._state_store.clear_pending_clarification(session_id)
        state.update(self._nodes.complete(state))
        return state
