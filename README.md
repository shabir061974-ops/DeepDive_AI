# DeepDive AI

Agent Arena 2026, Track 01: DeepDive — Self-Verifying Research Analyst.

## Phase 3 status

This Python 3.12 project includes validated immutable domain models, five async
agent interfaces and concrete implementations, an injectable workflow coordinator,
audit events, a bounded Mesh HTTP client and a public-source HTTP retrieval adapter.
The five-agent workflow is callable through explicit dependency injection and has
been verified using offline fixtures. Live research has not been validated.

Phase 3 starts at commit `440eba4`, verified clean before implementation. Phase 1
and Phase 2 remain the architectural foundation. No live Mesh calls, web retrieval, paid API
usage, service activation, push or deployment occurred during implementation or
testing. Mesh protocol compatibility has not been verified against a live service
or official documentation. Offline success does not establish live readiness.

The existing greeting entrypoint and its two tests are preserved. `main.py`
still prints a greeting; it does not run research or start a server.

## Architecture

```text
ResearchQuestion
  -> PlannerAgent -> ResearchTask[]
  -> ResearcherAgent -> Source[] + Evidence[] + provenance
  -> DrafterAgent -> Claim[] referencing Evidence IDs
  -> VerifierAgent -> VerificationResult[]
       | insufficient evidence or no claims, retries remain
       +-> Researcher -> Drafter -> Verifier
  -> SynthesizerAgent -> Report with claims, citations and verification status
```

- `src/deepdive_ai/models.py`: questions, tasks, source provenance, evidence,
  claims, verification results and reports. Citations resolve through
  `Claim.evidence_ids -> Evidence.source_id -> Source.url`; evidence also records
  a source locator and originating task. Provenance includes a timezone-aware
  retrieval timestamp, retrieval method and SHA-256 of original content bytes.
- `src/deepdive_ai/agents/`: separate planner, researcher, drafter, verifier and
  synthesizer protocols plus their concrete implementations. Every LLM invocation
  uses `MeshClient`; source retrieval uses `WebRetriever` with mandatory separate
  discovery through `SourceDiscovery`.
- `src/deepdive_ai/agents/common.py`: strict JSON output parsing, input/output
  limits, shared external-call budgets, operation timeouts and sanitized failures.
- `src/deepdive_ai/discovery.py`: typed, injectable source-discovery interface.
  Discovery candidates are URL records, not evidence or fabricated provenance.
- `src/deepdive_ai/runtime.py`: `build_research_workflow(...)` composes the concrete
  agents with the existing coordinator. It creates no network requests and starts
  no services. A Mesh client, model, discovery provider, retriever and audit sink
  must be supplied explicitly.
- `src/deepdive_ai/reporting.py`: canonical JSON summary containing exact accepted
  claim text and citations resolved through actual evidence/source records.
- `src/deepdive_ai/workflow.py`: caller-supplied agents, audit sink, run ID and
  optional clock. The planner runs once; each research attempt drafts and verifies
  again. The researcher receives the full prior bundle and insufficient-evidence
  feedback, and returns a complete updated bundle preserving previous records.
- `src/deepdive_ai/audit.py`: structured metadata events for start, planning,
  research, drafting, verification, retry, completion and failure. Use opaque
  run/question IDs; events contain no research text or exception details.
- `src/deepdive_ai/mesh.py`: typed Mesh requests/responses, client protocol and
  `MeshAPIClient` transport adapter.
  `MeshSettings.from_environment()` reads `MESH_API_KEY`, rejects missing/blank
  values and excludes the key from its representation. All application LLM calls
  must use this Mesh boundary; there is no direct-provider or fallback client.
- `src/deepdive_ai/retrieval.py`: real search/fetch contracts, `HTTPWebRetriever`,
  original-byte digest validation and explicit byte-range evidence extraction.
- `src/deepdive_ai/http_transport.py`: shared bounded HTTP client, public URL/DNS
  policy, and IP-pinned socket transport with TLS certificate/hostname validation.
- `src/deepdive_ai/api.py`: optional FastAPI factory with a `/health` endpoint
  reporting `{"status": "ok", "phase": "foundation"}`. This is process health,
  not research readiness. No research HTTP endpoint is exposed. The Phase 1 health
  payload, greeting behavior and agent protocol signatures remain unchanged.
  Phase 1/2 validation and bounded retries remain intact. All 68 earlier tests
  continue to run unchanged. Disabled Phase 1 adapters remain available and disabled.

## Concrete research agents

1. `MeshPlannerAgent` asks Mesh for structured research queries, including checks
   for counter-evidence. Strict schema checks reject empty, duplicate or excessive
   tasks. IDs and question relationships are assigned locally, not by the model.
2. `RetrievalResearcherAgent` first asks the injected `SourceDiscovery` provider for
   candidates, then fetches each candidate through the Phase 2 retrieval adapter.
   It never treats direct URL fetching as search and never obtains URLs from an
   LLM. Provider snippets are never accepted as evidence. Retrieval errors abort
   safely; an empty result yields no invented sources or evidence.
3. `MeshDrafterAgent` produces atomic claims referencing only supplied evidence
   IDs. Missing, unknown or duplicate references, embedded source links, duplicate
   claims and excess counts fail validation. No evidence means no draft/model call.
4. `MeshVerifierAgent` independently asks Mesh to assess the semantics of each
   entire claim against every supplied excerpt, including uncited counter-evidence.
   Each excerpt needs exactly one relation: `supports`, `contradicts`, `irrelevant`
   or `insufficient`, with a semantic rationale. Support and contradiction require
   nonempty exact substring quotes. Citation presence and lexical overlap never
   suffice. The verifier preserves assessments and quotes in its structured
   rationale for review.
5. `VerifiedClaimsSynthesizerAgent` deterministically copies accepted claim text
   into a canonical JSON summary and resolves its supporting citations. It does
   not call an LLM, paraphrase, add conclusions or generate citation URLs.

Semantic classification has deterministic safety vetoes: a supported verdict
without cited supporting assessments becomes `insufficient_evidence`; explicit
counter-evidence without cited support becomes `unsupported`; conflicting support
and counter-evidence becomes `insufficient_evidence` for bounded follow-up. A model
label of `unsupported` without contradictory assessments is treated as uncertainty.
Missing citations yield insufficient evidence without calling Mesh. Unknown IDs,
fabricated quotes, missing assessments, malformed JSON or API failures abort the
run with a failure audit event, rather than manufacturing verification results.

Research evidence consists of exact UTF-8 byte slices, with locators, source IDs,
original content hashes and retrieval timestamps. Retries retain all prior source
and evidence records; changed source content becomes a new snapshot. Identical
URL/content snapshots retain their first timestamp. Extraction takes a bounded
prefix in consecutive byte chunks and preserves UTF-8 boundaries. It supports raw
UTF-8 plain text, CSV, JSON, HTML and XHTML. HTML is unrendered, untrusted source
markup; scripts are never executed. PDF and invalid/non-UTF-8 text abort safely.
This extractor does not claim to scan the entire source or discover all relevant
facts. Truncated content may omit relevant context or contradictions.

`Report.accepted_claims` is the final accepted-claims view. `Report.claims` and
`Report.verification` deliberately retain the full latest draft and its assessments
as an audit/review ledger, including rejected or uncertain claims. They are not the
final answer. `Report.summary` from the concrete synthesizer is JSON with only
`accepted_claims`, exact text and supporting citations (IDs, URL, locator, digest
and timestamp). If nothing is accepted it contains an empty list. Overall report
status remains `needs_review` if any latest-draft claim is unresolved or rejected,
even when other claims are accepted. A report with no claims cannot be `verified`.

`build_research_workflow` always enables `WorkflowPolicy.require_verified_summary`.
The coordinator compares the summary with the canonical rendering after checking
the original claim/evidence/verification records, rejecting altered wording,
citations or added conclusions. This is an additive stricter policy: directly
constructed legacy coordinators retain their prior default summary behavior so
existing interfaces and tests stay compatible. New operational workflows should
use the factory or explicitly enable that policy.

`AgentLimits` defaults bound tasks (8), claims (20), sources (32), evidence records
(64), total excerpt characters (48,000), question characters (8,000), individual
text/quote/rationale fields (2,000), prompt characters (120,000), model response
characters (32,000), summary characters (200,000), document bytes (2,000,000),
extraction (four chunks of up to 2,000 bytes per document), discovery results
(3 per task), model output tokens (4096), and each operation (30 seconds).
One shared budget permits at most 64 model calls, 32 discovery calls and 32 fetch
calls over the workflow instance's lifetime, including all retries and subsequent
runs. These high-level limits supplement Phase 2 HTTP attempt/retry/size limits;
the discovery provider must also implement its own bounded HTTP requests and
network protections. Any exhausted limit fails the run; data or counter-evidence
is not silently dropped to fit a context window. Use a new workflow instance for
per-run budgets, and choose limits suitable for the selected model's context and
output capacity. The factory clamps task/claim limits to the existing workflow
policy; it does not increase workflow retries. There is no model-output repair
loop or hidden retry on malformed JSON.

Composition example (definition only; no research or services start):

```python
from deepdive_ai.runtime import build_research_workflow

def compose(mesh_client, model_name, discovery_provider, http_retriever, audit_sink):
    return build_research_workflow(
        mesh=mesh_client, model=model_name, discovery=discovery_provider,
        retriever=http_retriever, audit=audit_sink,
    )
```

## Mesh adapter

`MeshAPIClient` uses `https://api.meshapi.ai/v1/chat/completions` with bearer
authentication and a non-streaming chat-completions JSON envelope: `model`, one
user `messages` entry, `max_tokens`, and `stream: false`. It expects an `id`, one
assistant message with string `content`, and `finish_reason: "stop"`. Truncated,
tool-call, duplicate-key, non-JSON and malformed responses fail closed. This
envelope is an explicit compatibility assumption, pending official Mesh protocol
confirmation. Model names are caller supplied; no model availability is assumed.

The configurable base URL is constrained to the approved Mesh HTTPS origin and
`/v1` path, with an optional trailing slash, so credentials cannot be redirected
to another provider. Mesh redirects are rejected. API keys with whitespace,
control characters or non-ASCII characters are rejected before header creation.
Keys, prompts and response bodies are omitted from adapter record representations.
No adapter logs payloads, keys, URLs or upstream error bodies. Never serialize
settings with `dataclasses.asdict()` for logs: that would include the key.

`RequestPolicy` configures per-attempt timeout (20 seconds), retries (2, maximum 5),
exponential backoff (0.5 seconds), maximum retry delay (5 seconds), serialized
request size (256,000 bytes), response size (2,000,000 bytes), lifetime HTTP
attempt budget (100) and concurrent awaited attempts (4). `MeshSettings` also caps
requested output tokens at 4096. These are limits, not tokenization or price
estimates. All retries and redirects consume the same instance's request budget.

HTTP 408, 429, 500, 502, 503 and 504 are retried within those bounds. Numeric
`Retry-After` values are capped; HTTP-date values use normal backoff. Mesh POST
transport errors/timeouts are not retried because completion may already have
occurred. Even retries of returned status errors can duplicate provider work;
set `max_retries=0` when this risk is unacceptable. Authentication failures,
malformed bodies and validation/security failures are not retried.

Construction makes no requests. Configuration example (also makes no requests):

```python
from deepdive_ai.http_transport import RequestPolicy
from deepdive_ai.mesh import MeshAPIClient, MeshSettings

policy = RequestPolicy(timeout_seconds=15, max_retries=1, max_requests=10)
settings = MeshSettings.from_environment(policy=policy, max_output_tokens=2048)
client = MeshAPIClient(settings)
```

## Public-source retrieval

`HTTPWebRetriever.fetch(url)` performs a GET only when explicitly called. It
accepts HTTP/HTTPS on standard ports, rejects credentials, malformed URLs,
localhost, local/private/link-local/loopback/multicast/reserved addresses and IPv6
transition forms that could embed unsafe IPv4 destinations. Every DNS result must
be public. The transport connects directly to one validated IP, with the original
Host header and TLS hostname; it never re-resolves between validation and connect.
Environment proxy settings, cookies and automatic redirects are not used.

Each redirect is validated again and consumes the request budget. HTTPS-to-HTTP
downgrades, loops, missing/ambiguous locations and more than five redirects are
rejected (configurable limit 0–10). Initial plain HTTP remains supported and does
not provide transport authenticity; prefer HTTPS research sources. DNS/transport
GET failures can be retried because GET is read only. One validated address is
tried per attempt; there is no automatic fallback to other DNS addresses.

Only `text/plain`, `text/html`, `text/csv`, `application/xhtml+xml`,
`application/json` and `application/pdf` are accepted. The transport requests
identity encoding and rejects compressed responses, invalid/ambiguous framing,
truncation, response headers over 65,536 characters, and bodies exceeding the
configured byte cap, including chunked or
lengthless bodies. Streaming enforces the cap before retaining the full response.
Socket deadlines and a deadline shutdown timer bound slow connections/reads.
The async caller also has a timeout; OS DNS workers cannot be forcibly cancelled,
but late DNS results are discarded before opening a connection. Cancellation can
leave a worker finishing until its deadline. Limits apply per exchange, including
retries; semaphore queue time is additional to the per-attempt timeout.

Successful documents retain the original requested URL, normalized redirect
chain, final source URL, retrieval timestamp, SHA-256 of original entity-body
bytes (after HTTP transfer framing), media type and retrieval method. Source IDs
derive from final URL plus content digest. The source title is the final URL,
not an invented page title. `evidence_from_bytes(id, task_id, start, end)` returns
only an exact, valid UTF-8 slice with a byte-range locator. It does not render HTML,
extract PDF text, execute scripts or infer findings. Sources remain untrusted.

The Phase 2 fetch adapter's `search()` still explicitly raises `RetrievalError`;
it is not a search engine. Phase 3 requires a separate `SourceDiscovery` provider
implementing `discover(SearchRequest) -> tuple[DiscoveredSource, ...]`. A provider
must return genuine search/index results and respect the requested limit. No live
search provider or provider credentials are bundled, guessed or activated.
Both clients accept an injected `HTTPTransport`; tests inject scripted responses
or fake sockets, clocks and delays. Injected transports are trusted implementations
and must uphold the transport contract; the default enforces DNS and socket policy.

## Validation and retry behavior

Default policy: one retry after the initial research attempt, at most 20 planned
tasks and 100 claims per draft. Retry configuration accepts 0–5 retries, so a run
performs at most six research attempts. An unsupported claim is retained for
review; only insufficient evidence or an empty draft triggers more research.
Retry exhaustion produces a `needs_review` report through the synthesizer.

Every claim must have exactly one verification result. Supported results require
nonempty evidence IDs linked to the claim and supplied source records. Unknown
IDs, duplicate IDs, mismatched tasks, missing verification, changed provenance
and altered synthesis records raise validation errors and fail the run. Empty
claims or non-supported results cannot produce a `verified` report. Agent and
audit errors propagate; the coordinator does not silently fabricate a report.

Structural checks plus the concrete semantic verifier assess supplied excerpts;
they do not prove source truth, source independence or completeness of research.
Semantic labels remain model judgments and can be wrong, even when quotes are
exact. Synthesis prevents paraphrase drift but cannot correct a mistaken semantic
assessment. No real findings, citations or answers
are bundled; test records are explicitly labeled offline fixtures on `.invalid`
URLs and never used by application entrypoints.

## Local setup

Use Python 3.12 (the supplied environment is Python 3.12.10).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe main.py
```

The core and offline tests require only the standard library. For the optional
FastAPI shell, install the declared backend extra:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[backend]'
```

The supplied environment has FastAPI installed, and the health-endpoint test runs.
On environments without FastAPI that optional test explicitly skips. The adapters
use Python 3.12's standard library (`http.client`, `ssl`, `socket`, `asyncio`), so
Phases 2 and 3 add no third-party runtime dependencies. Backend dependencies remain in
the existing optional extra. No dependency downloads were needed for this phase.
No credential is needed for tests. Supply `MESH_API_KEY` through the process
environment for future authorized Mesh use; never commit it. `.env` files are
ignored and are not automatically loaded.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src main.py tests
```

Tests use fixed timestamps and explicit in-memory agent doubles. They cover the
sequence, bounded retries and exhaustion, absent evidence, invalid references,
provenance preservation/digest checks, synthesis integrity, audit failures,
credential handling, disabled adapters and existing greeting behavior. Phase 2
adds success/error/malformed-response cases, bounded HTTP retries/budgets,
timeouts/concurrency, DNS pinning, TLS identity, redirect security, response-size
and framing checks, content hashing and exact evidence byte provenance. Network
calls are replaced by scripted transports or fake sockets, with guards against
accidental DNS/connections. Tests do not exercise live services or prove research
accuracy.

Phase 3 adds controlled fake-Mesh and Phase 2 retrieval fixtures for the five-agent
workflow, successful evidence support, unsupported and conflicting claims, uncited
counter-evidence, unknown citations, invented quotes, malformed output, sanitized
API errors, successful/exhausted retries, provenance preservation, summary tampering
and shared resource budgets. Fixture assertions are not real research findings.

## Remaining limitations

Official Mesh wire-format/model confirmation and explicitly authorized live
integration tests; a real discovery-provider implementation; rendered HTML/PDF
extraction and relevance selection beyond bounded byte prefixes; independent
semantic accuracy evaluation, source-quality assessment and completeness checking;
persistent evidence/audit storage; research endpoints, authentication and UI;
deployment. Prompts separate untrusted data and forbid embedded instructions, and
strict schemas/citation/quote checks constrain output, but prompt injection and
semantic misclassification remain model risks. No live source or model accuracy
claim is made. The workflow is not started by the greeting or health endpoint.

Phase 3 changes remain uncommitted pending SPA review. No Phase 4 work is included.
