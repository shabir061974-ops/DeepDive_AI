# DeepDive AI

Agent Arena 2026, Track 01: DeepDive — Self-Verifying Research Analyst.

## Phase 5A status

Phase 5B preparation at checkpoint `6e44a0e` adds an explicit controlled
connectivity harness, defaulting to offline preflight. See the [PowerShell
commands, limits, cost inputs and stop conditions](docs/controlled_connectivity.md).
The operator's first authorized Brave attempt failed before a complete transport
response was returned; Mesh was not attempted. See the [offline failure
investigation](docs/brave_failure_investigation.md). No additional live requests
were made during that investigation. The candidate Mesh model remains
`google/gemini-2.5-flash`; the five-agent workflow is unchanged.
The blocker review from `af7926d` makes version checks optional and treats gateway
headers as diagnostics. Client request bounds remain strict; live mode requires
approved account prices plus explicit gateway-risk and spending acknowledgements.
No local hard dollar ceiling or single upstream attempt is claimed.

The secure CA-bundle update from `6c8e416` uses pinned `certifi==2026.7.22`
for the shared HTTPS transport. Certificate and hostname verification remain
enabled. The operator reported that certifi resolved a local issuer-chain TLS
failure in a separate unauthenticated handshake diagnostic. This integration
was tested offline only; authenticated provider compatibility remains unverified.

The offline provider readiness audit starts at clean checkpoint `39cc3a0`.
See [provider requirements, compatibility gaps and the bounded future live-test
plan](docs/provider_readiness.md). Both required credentials were absent from
the auditing process environment. Live testing remains blocked pending private
configuration, approved model/pricing, routing-policy confirmation and SPA review.
No provider API requests or paid usage occurred during this audit.

This Python 3.12 project includes validated immutable domain models, five async
agent interfaces and concrete implementations, an injectable workflow coordinator,
audit events, a bounded Mesh HTTP client and a public-source HTTP retrieval adapter.
The five-agent workflow now includes an explicit Brave Web Search adapter and
bounded static HTML/PDF extraction, verified using mocked HTTP and local fixtures.
Live search, Mesh compatibility and live research have not been validated.

Phase 4 starts at commit `5e2d54e`, verified clean before implementation. Phase 1
and Phase 2 remain the architectural foundation. No live Mesh calls, web retrieval, paid API
usage, service activation, push or deployment occurred during implementation or
testing. Phase 5A compares the adapters with available official documentation;
exact Mesh wire-format/model compatibility remains unverified. Offline success
does not establish live readiness.

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
- `src/deepdive_ai/search.py`: explicit Brave Web Search adapter using the existing
  protected HTTP transport, bounded requests, strict results and URL deduplication.
- `src/deepdive_ai/extraction.py`: bounded readable HTML extraction, PDF worker
  orchestration and transformed-text artifacts.
- `src/deepdive_ai/pdf_worker.py`, `process_limits.py`: isolated PDF parsing with
  page/stream/structure/text limits and OS memory/CPU limits.
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
  Phase 1/2 validation and bounded retries remain intact. All 104 earlier tests
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

Plain UTF-8 text/CSV/JSON evidence retains exact original-byte locators. HTML/PDF
evidence uses explicit transformed-text locators and extraction provenance, never
original-byte offsets. Source IDs, original content hashes and timestamps remain
unchanged by extraction. Retries retain all prior source
and evidence records; changed source content becomes a new snapshot. Identical
URL/content snapshots retain their first timestamp. Evidence selection takes a
bounded prefix in UTF-8-safe chunks after extraction. HTML/PDF handling is described
below; scripts are never executed, and unsupported or ambiguous encodings fail.
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

## Brave Web Search provider

`BraveSearchDiscovery` implements the existing `SourceDiscovery` interface. It
uses a GET to `https://api.search.brave.com/res/v1/web/search`, a
`X-Subscription-Token` header, and `q`, `count`, `search_lang`, `country`,
`spellcheck=false` and `result_filter=web` parameters. It expects a JSON
`type: "search"` response with
optional `web.results` URL records. Search snippets/titles never become evidence.
Missing web results are empty discovery; malformed records or unsafe URLs fail the
whole request. URLs are normalized and duplicates removed without changing their
query strings. Fetched redirect aliases also retain a single source record for
identical final URL/content snapshots.

An account/subscription with Web Search API access and a valid process environment
variable `BRAVE_SEARCH_API_KEY` are required for future authorized live use.
`BraveSearchSettings.from_environment()` rejects missing or invalid credentials.
Country/language and `RequestPolicy` are configured in Python. The credential is
excluded from representations and worker environments; it is never placed in a
query string. Authenticated search redirects are rejected, including redirects to
the same host. The endpoint is constrained to the approved HTTPS origin/path.
Public DNS/IP validation, IP pinning, TLS verification, proxy exclusion, timeout,
size and retry protections use the unchanged Phase 2 transport. Discovery URLs
undergo syntax/IP checks; public DNS checks occur before each actual source fetch.

Default search limits: 10-second attempt timeout, one retry, 256,000 response bytes,
32 lifetime HTTP attempts, two concurrent awaited requests. Queries must fit 400
characters and 50 whitespace-delimited words; overlong queries are rejected rather
than silently truncated. SearchRequest accepts 1–20 results. Provider responses
exceeding the requested count fail. English (`en`) and country `US` are defaults;
country codes require two uppercase ASCII letters (`ALL` is no longer accepted).
Other language/country codes must be accepted by the provider. No pagination,
automatic provider fallback, query rewriting or hard requests-per-second scheduler
is implemented. Agent-generated task queries must also fit these provider limits.

No account-specific free tier, credit allowance or price is assumed. The audit
records the advertised public price and unresolved account-specific cost gates.
Plan availability, quotas, attribution/storage rights and costs require review in
the [Brave API dashboard](https://api-dashboard.search.brave.com/) and
[provider plan information](https://brave.com/search/api/) before activation.
Each HTTP retry may count as another billable request. Budgets bound request
counts, not currency spend; set `max_retries=0` for strict single-attempt behavior.
This declared API contract is tested with mocked responses only, not a live
provider or live account. No subscription was created or service activated.

## HTML/PDF extraction and provenance

`BoundedContentExtractor` is the researcher's default extractor for HTML, XHTML and
PDF. It can be explicitly injected through `build_research_workflow(extractor=...)`.
The other four agents and verification requirements are unchanged. Original HTTP
entity-body bytes remain the input to source hashing; extracted text never replaces
the source digest.

HTML parsing uses the standard library without fetching resources or executing
scripts. It removes script/style/head, navigation, headers/footers, sidebars,
forms, hidden elements and common cookie/advert/social boilerplate; prefers static
`main`/`article` content; decodes entities; and normalizes block whitespace. Limits
cover input bytes (2,000,000), retained text characters (100,000), parser nodes
(50,000) and nesting (128). Malformed nesting, unclosed nonoptional elements,
external/unsafe declarations, no parsed elements or no meaningful text fail closed.
Common optional HTML closing tags are accepted. UTF-8 is the default; HTTP charset,
BOM and early meta charset declarations support a strict allowlist of UTF-8,
Windows-1252, ISO-8859-1 and BOM-marked UTF-16. Conflicting, unsupported or invalid
encodings are rejected, without replacement decoding. Charset metadata is retained
by the HTTP adapter; ambiguous duplicate charset parameters are rejected.

HTML removal is heuristic, not a browser layout engine: complex CSS, dynamic/JS
pages, unusual boilerplate, code, tables and meaningful content inside removed
elements can be missed or flattened. Limits fail rather than truncate the parsed
artifact. CPU work is bounded by byte/node/depth limits; the hard worker wall-clock
and OS memory/CPU limits below apply to PDF, not in-process HTML parsing.

PDF parsing uses the pinned `pypdf==6.20.1` in a separate isolated Python process.
Defaults: 2,000,000 input bytes, 20 pages, 20,000 text characters per page,
100,000 total text characters, 8,000,000 decoded content-stream bytes, 50,000 graph
nodes, depth 128, 256 MiB memory, five CPU seconds and a ten-second wall timeout.
One worker runs at a time per extractor. On Windows, a Job Object caps combined
job/process memory and CPU, allows the venv launcher/interpreter pair, and kills
the entire job on timeout/cancellation/cleanup. POSIX workers apply address-space,
CPU and core-dump limits before parsing. If required resource limits cannot be
established, extraction fails. Worker stdin/stdout are bounded by input/output
policies; diagnostics are suppressed, source bytes are not written to temporary
files, credentials are not inherited, and worker socket access is disabled.

Encrypted PDFs (including empty-password encryption), malformed signatures/trailers,
invalid structures, active actions/JavaScript, embedded files, external streams,
oversized/decompression-heavy documents and documents with no extractable text are
rejected. Conservative active-action rejection also excludes some PDFs containing
ordinary link annotations. No OCR, rendering, passwords or external fonts/resources
are fetched. Image-only scans cannot yield evidence. PDF reading order, column,
table and glyph reconstruction remain parser limitations. Process resource
isolation is not a complete OS security sandbox against parser vulnerabilities.

`Evidence.extraction` records the original byte digest, extracted artifact digest,
excerpt digest, versioned extraction method, transformations, extracted Unicode
character span and optional 1-based PDF page. HTML has one text artifact; PDF has
one per text-bearing page. Locators explicitly say `extracted text chars`, never
original HTTP bytes. The model validates excerpt span length/digest and binds the
original digest to the evidence's actual source record. Final supporting citations
retain this metadata. Original-byte locators remain available only for unchanged
plain UTF-8 evidence. Reproducing a full extraction artifact requires the original
document plus recorded method/transformations; artifacts are not persistently
archived in this phase. Bounded evidence selection can still omit later text or
pages and must not be interpreted as complete-document verification.

Configuration example (definition only; constructing clients makes no requests):

```python
from deepdive_ai.extraction import BoundedContentExtractor, ExtractionSettings
from deepdive_ai.http_transport import RequestPolicy
from deepdive_ai.retrieval import HTTPWebRetriever
from deepdive_ai.search import BraveSearchDiscovery, BraveSearchSettings
from deepdive_ai.runtime import build_research_workflow

def compose_web_research(mesh_client, model_name, audit_sink):
    discovery = BraveSearchDiscovery(BraveSearchSettings.from_environment(
        policy=RequestPolicy(timeout_seconds=10, max_retries=0, max_requests=10)))
    extractor = BoundedContentExtractor(ExtractionSettings(max_pages=10))
    return build_research_workflow(
        mesh=mesh_client, model=model_name, discovery=discovery,
        retriever=HTTPWebRetriever(), extractor=extractor, audit=audit_sink,
    )
```

Credentials are process environment variables, `MESH_API_KEY` and
`BRAVE_SEARCH_API_KEY`. `.env` files are ignored and not automatically loaded.
Never log settings with `dataclasses.asdict()`, which would include credentials.
Calling a workflow later would perform external operations; this example does not
invoke it. No default provider is silently activated at import or server startup.

## Mesh adapter

`MeshAPIClient` uses `https://api.meshapi.ai/v1/chat/completions` with bearer
authentication and a non-streaming chat-completions JSON envelope: `model`, one
user `messages` entry, `max_tokens`, and `stream: false`. It expects an `id`, one
assistant message with string `content`, and `finish_reason: "stop"`. Truncated,
tool-call, duplicate-key, non-JSON and malformed responses fail closed. This
basic endpoint/auth/message envelope matches the official quickstart. The exact
token parameter and strict response subset remain compatibility assumptions;
see the Phase 5A audit. Model names are caller supplied; no model availability is
assumed.

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

policy = RequestPolicy(timeout_seconds=15, max_retries=0, max_requests=1, max_concurrency=1)
settings = MeshSettings.from_environment(policy=policy, max_output_tokens=2048)
client = MeshAPIClient(settings)
```

This bounds client attempts only. Mesh gateway retries/fallback require a
separately verified per-key policy; the live-test plan specifies the gate.

## Public-source retrieval

HTTPS connections use `ssl.create_default_context(cafile=certifi.where())`.
Python's secure defaults retain `CERT_REQUIRED` and hostname checking; the
original hostname supplies TLS SNI while connections remain pinned to a validated
public IP. Brave, Mesh and HTTPS source retrieval share this transport. Missing
or invalid CA bundles fail closed, without retrying with an unverified context.
No custom CA path, trust-store environment override or insecure TLS option is
introduced. Injected transports remain the caller's explicit responsibility.

The CA bundle is a pinned project dependency for reproducible installation.
Review and update the pin deliberately as trust roots change. Enterprise/private
roots available only in the Windows trust store may not be in certifi; such
failures require a separately reviewed trust policy, never disabled verification.
No live provider request was made to validate this change.

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
it is not a search engine. Phase 4 supplies the separate Brave implementation of
`SourceDiscovery`; credentials and activation remain explicit. No credentials are
bundled or guessed.
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

The core now requires the pinned PDF dependency, installed by `pip install -e .`.
For the optional FastAPI shell, install the declared backend extra:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[backend]'
```

The supplied environment has FastAPI installed, and the health-endpoint test runs.
On environments without FastAPI that optional test explicitly skips. HTTP, search
and HTML parsing use the Python 3.12 standard library. Phase 4 adds the pinned
`pypdf==6.20.1` dependency, installed into this repository's `.venv` for verification.
Backend dependencies remain in the existing optional extra. No live search,
retrieval or Mesh calls were used to install or test the implementation.
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

Phase 4 adds mocked Brave results and authored local HTML/PDF fixtures for provider
errors/limits/duplication, charset handling, cleaned text, transformed span/hash
integrity, actual resource-limited PDF extraction, encrypted/active/malformed PDF
rejection, resource exhaustion, unsafe redirects and end-to-end researcher
integration. The original 104 tests remain unchanged.

## Remaining limitations

Official Mesh wire-format/model confirmation and explicitly authorized live
integration tests; additional discovery providers and search quality evaluation;
browser-rendered HTML, OCR and better PDF layout/relevance selection; independent
semantic accuracy evaluation, source-quality assessment and completeness checking;
persistent evidence/audit storage; research endpoints, authentication and UI;
deployment. Prompts separate untrusted data and forbid embedded instructions, and
strict schemas/citation/quote checks constrain output, but prompt injection and
semantic misclassification remain model risks. No live source or model accuracy
claim is made. The workflow is not started by the greeting or health endpoint.

Phase 4 is checkpointed at `39cc3a0`. Phase 5A audit/configuration changes and seven
new offline tests remain uncommitted pending SPA review. No live integration or
later-phase application work is included.
