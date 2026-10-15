"""Explicit composition only: no live services or agent runs start automatically."""

from dataclasses import replace
from datetime import datetime
from typing import Callable

from .agents.common import AgentBudget, AgentLimits, MeshJSON
from .agents.drafter import MeshDrafterAgent
from .agents.planner import MeshPlannerAgent
from .agents.researcher import RetrievalResearcherAgent
from .agents.synthesizer import VerifiedClaimsSynthesizerAgent
from .agents.verifier import MeshVerifierAgent
from .audit import AuditSink
from .discovery import SourceDiscovery
from .mesh import MeshClient
from .retrieval import WebRetriever
from .workflow import WorkflowCoordinator, WorkflowPolicy, utc_now


def build_research_workflow(
    *, mesh: MeshClient, model: str, discovery: SourceDiscovery, retriever: WebRetriever,
    audit: AuditSink, limits: AgentLimits = AgentLimits(),
    policy: WorkflowPolicy = WorkflowPolicy(), clock: Callable[[], datetime] = utc_now,
) -> WorkflowCoordinator:
    """Wire the five concrete agents to existing contracts with stricter summary checks.

    Budgets are lifetime limits of the returned workflow, shared across retries/runs.
    For per-run accounting construct a fresh workflow explicitly. Provider discovery
    is mandatory; no guessed URLs or default external services are substituted.
    """
    limits = replace(limits, max_tasks=min(limits.max_tasks, policy.max_tasks),
                     max_claims=min(limits.max_claims, policy.max_claims))
    budget = AgentBudget(limits)
    llm = MeshJSON(mesh, model, limits, budget)
    policy = replace(policy, max_tasks=limits.max_tasks, max_claims=limits.max_claims,
                     require_verified_summary=True)
    return WorkflowCoordinator(
        MeshPlannerAgent(llm), RetrievalResearcherAgent(discovery, retriever, limits, budget),
        MeshDrafterAgent(llm), MeshVerifierAgent(llm), VerifiedClaimsSynthesizerAgent(limits),
        audit, policy, clock)
