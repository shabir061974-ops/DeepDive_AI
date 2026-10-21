import asyncio
import hashlib
import json
from typing import Protocol

from ..discovery import DiscoveredSource, SourceDiscovery
from ..http_transport import ResponseTooLarge, TransportError, UnsafeURL, validate_url
from ..extraction import BoundedContentExtractor, ContentExtractor, TextArtifact
from ..models import Evidence, ResearchBundle, ResearchQuestion, ResearchTask, VerificationResult, records
from ..retrieval import RetrievalError, RetrievedDocument, SearchRequest, WebRetriever
from .common import AgentBudget, AgentError, AgentLimitError, AgentLimits, text


# Failures confined to one candidate's fetch: access refused or any non-200 status,
# transport/TLS/DNS/timeout, unsafe redirects and oversized responses. The candidate
# contributes nothing and is not retried. Request-budget exhaustion and every check
# after a document is returned (integrity, provenance, extraction) stay fatal.
CANDIDATE_FETCH_FAILURES = (RetrievalError, TransportError, UnsafeURL, ResponseTooLarge)


class ResearcherAgent(Protocol):
    async def research(
        self, question: ResearchQuestion, tasks: tuple[ResearchTask, ...],
        previous: ResearchBundle, feedback: tuple[VerificationResult, ...],
    ) -> ResearchBundle:
        """Return a complete updated bundle with provenance; use feedback on retries.

        Preserve previous records and identifiers. Never manufacture findings.
        """
        ...


class RetrievalResearcherAgent:
    """Search via an injected provider, then fetch through the Phase 2 interface.

    Evidence is a bounded text prefix with original-byte or transformed-text locators.
    No provider is silently supplied and direct fetching is never called search.
    """

    def __init__(self, discovery: SourceDiscovery, retriever: WebRetriever,
                 limits: AgentLimits = AgentLimits(), budget: AgentBudget | None = None,
                 extractor: ContentExtractor | None = None):
        self.discovery = discovery
        self.retriever = retriever
        self.limits = limits
        self.budget = budget if budget is not None else AgentBudget(limits)
        self.extractor = extractor if extractor is not None else BoundedContentExtractor()

    async def _document_evidence(self, document: RetrievedDocument, task: ResearchTask) -> tuple[Evidence, ...]:
        if len(document.content) > self.limits.max_document_bytes:
            raise AgentLimitError("Retrieved document exceeds agent byte limit")
        if document.media_type not in {"text/html", "application/xhtml+xml", "application/pdf"}:
            return self._extract(document, task)
        try:
            artifacts = await asyncio.wait_for(self.extractor.extract(document), self.limits.operation_timeout_seconds)
        except Exception:
            raise AgentError("Source content extraction failed") from None
        if not isinstance(artifacts, tuple) or not artifacts or any(not isinstance(item, TextArtifact) for item in artifacts):
            raise AgentError("Content extractor returned invalid artifacts")
        evidence = []
        for artifact in artifacts:
            if artifact.original_content_sha256 != document.source.provenance.content_sha256:
                raise AgentError("Extraction artifact does not match original source")
            encoded = artifact.text.encode("utf-8")
            start = 0
            char_start = 0
            while start < len(encoded) and len(evidence) < self.limits.chunks_per_document:
                end = min(start + self.limits.chunk_bytes, len(encoded))
                while True:
                    try:
                        selected = encoded[start:end].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        end -= 1
                char_end = char_start + len(selected)
                if selected.strip():
                    digest = hashlib.sha256(encoded).hexdigest()
                    identity = json.dumps([document.source.id, task.id, artifact.method, artifact.page_number,
                                           digest, char_start, char_end, artifact.transformations])
                    evidence_id = "e-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    evidence.append(artifact.evidence(evidence_id, document.source.id, task.id, char_start, char_end))
                start, char_start = end, char_end
            if len(evidence) >= self.limits.chunks_per_document:
                break
        if not evidence:
            raise AgentError("Content extraction produced no usable evidence")
        return tuple(evidence)

    def _check_bundle(self, bundle: ResearchBundle) -> None:
        if (len(bundle.sources) > self.limits.max_sources or len(bundle.evidence) > self.limits.max_evidence
                or sum(len(item.excerpt) for item in bundle.evidence) > self.limits.max_evidence_chars):
            raise AgentLimitError("Research bundle exceeds agent limits")

    def _extract(self, document: RetrievedDocument, task: ResearchTask) -> tuple[Evidence, ...]:
        if len(document.content) > self.limits.max_document_bytes:
            raise AgentLimitError("Retrieved document exceeds agent byte limit")
        if document.media_type not in {"text/plain", "text/csv", "application/json"}:
            raise AgentError("Researcher requires a supported UTF-8 text document")
        # Validate the complete document, rather than silently replacing invalid bytes.
        try:
            document.content.decode("utf-8")
        except UnicodeDecodeError:
            raise AgentError("Research source is not valid UTF-8") from None
        evidence = []
        start = 0
        for _ in range(self.limits.chunks_per_document):
            if start >= len(document.content):
                break
            end = min(start + self.limits.chunk_bytes, len(document.content))
            # Trim at most three trailing bytes to avoid splitting a UTF-8 character.
            while True:
                try:
                    excerpt = document.content[start:end].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    end -= 1
            if excerpt.strip():
                identity = f"{document.source.id}\n{task.id}\n{start}:{end}"
                evidence_id = "e-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                evidence.append(document.evidence_from_bytes(evidence_id, task.id, start, end))
            start = end
        return tuple(evidence)

    async def research(
        self, question: ResearchQuestion, tasks: tuple[ResearchTask, ...],
        previous: ResearchBundle, feedback: tuple[VerificationResult, ...],
    ) -> ResearchBundle:
        text(question.text, self.limits.max_question_chars)
        records(tasks, ResearchTask)
        records(feedback, VerificationResult)
        if not tasks or len(tasks) > self.limits.max_tasks or any(task.question_id != question.id for task in tasks):
            raise AgentError("Research tasks are invalid or exceed agent limits")
        self._check_bundle(previous)
        task_ids = {task.id for task in tasks}
        if any(item.task_id not in task_ids for item in previous.evidence):
            raise AgentError("Previous evidence belongs to unknown tasks")
        sources = {source.id: source for source in previous.sources}
        evidence = {item.id: item for item in previous.evidence}
        seen: set[tuple[str, str]] = set()
        for task in tasks:
            instruction = text(task.instruction, self.limits.max_text_chars)
            # Actual model rationale is not sent to discovery; feedback codes signal targeted follow-up.
            query = json_query(question.text, instruction, bool(feedback))
            text(query, self.limits.max_question_chars + self.limits.max_text_chars + 200)
            self.budget.consume("discovery")
            try:
                candidates = await asyncio.wait_for(self.discovery.discover(SearchRequest(
                    query, self.limits.discovery_results_per_task)), self.limits.operation_timeout_seconds)
            except Exception:
                raise AgentError("Source discovery failed") from None
            if (not isinstance(candidates, tuple) or len(candidates) > self.limits.discovery_results_per_task
                    or any(not isinstance(item, DiscoveredSource) for item in candidates)):
                raise AgentError("Discovery returned invalid or excessive candidates")
            retrieved = 0
            last_failure: Exception | None = None
            for candidate in candidates:
                if retrieved >= self.limits.sources_per_task:
                    break
                url = validate_url(candidate.url)
                if (task.id, url) in seen:
                    continue
                seen.add((task.id, url))
                self.budget.consume("fetch")
                try:
                    document = await asyncio.wait_for(self.retriever.fetch(url), self.limits.operation_timeout_seconds)
                except CANDIDATE_FETCH_FAILURES as error:
                    last_failure = error
                    continue
                except Exception:
                    raise AgentError("Source retrieval failed") from None
                if not isinstance(document, RetrievedDocument):
                    raise AgentError("Retriever returned an invalid document")
                validate_url(document.source.url)
                # Even injected retrieval fixtures must preserve bytes and the requested URL.
                if document.requested_url is None or validate_url(document.requested_url) != url:
                    raise AgentError("Retriever did not preserve the requested URL")
                if hashlib.sha256(document.content).hexdigest() != document.source.provenance.content_sha256:
                    raise AgentError("Retrieved document provenance does not match its bytes")
                existing = sources.get(document.source.id)
                if existing is not None and (existing.url != document.source.url
                        or existing.provenance.content_sha256 != document.source.provenance.content_sha256):
                    raise AgentError("Source identifier collides with different provenance")
                # Identical URL/content snapshots retain their original timestamp and record.
                sources.setdefault(document.source.id, document.source)
                for item in await self._document_evidence(document, task):
                    if item.id in evidence and evidence[item.id] != item:
                        raise AgentError("Evidence identifier collides with different content")
                    evidence.setdefault(item.id, item)
                self._check_bundle(ResearchBundle(tuple(sources.values()), tuple(evidence.values())))
                retrieved += 1
            if last_failure is not None and not retrieved:
                # Same shape as `raise ... from None` inside the handler: the message is
                # suppressed, but typed transport diagnostics stay reachable via __context__.
                error = AgentError("Source retrieval failed")
                error.__context__ = last_failure
                error.__suppress_context__ = True
                raise error
        return ResearchBundle(tuple(sources.values()), tuple(evidence.values()))


def json_query(question: str, instruction: str, retry: bool) -> str:
    """Discovery queries are text, not instructions to fetch model-supplied URLs."""
    suffix = " Additional independent evidence and counter-evidence required." if retry else ""
    return f"{question}\nResearch task: {instruction}{suffix}"
