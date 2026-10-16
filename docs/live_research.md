# Phase 9: controlled real-adapter research preparation

For the Phase 10 Drafter HTTP-200 metadata rejection and new safe check flags,
see the [second Mesh validation investigation](second_mesh_validation.md).

For a single source-only diagnostic that uses no Mesh/Brave credentials, see
the [offline preflight and separately authorized execution commands](source_connectivity.md).

## Phase 10 source-failure destination diagnostics

Starting at `aa5f1b8`, source-only failure diagnostics now include
`diagnostics.source.source_hostname` in the terminal result and persisted
`outcome.json`. The hostname is normalized through the existing URL validator
and bounded to 253 characters. No source URL is retained in failure metadata:
paths, query names/values, fragments, user information and headers may contain
secrets, so a hostname-only policy is used. Credentials, bodies and exception
messages remain excluded. Mesh and Brave diagnostics do not gain this field.

For DNS failures, this is the requested hostname after URL validation, not proof
of successful resolution or a public DNS answer. Existing DNS/IP validation
still runs before connecting; unsafe-destination failures omit the hostname.
Invalid URLs blocked before the observer may have no source diagnostic at all.
Redirects are separately observed, so a failure on a permitted redirect records
that request's hostname rather than the original source's hostname. Timeout
cancellation preserves it when the terminal category is resolved to `timeout`.
This identifies a destination, not the failing IP, an exact URL or a root cause.
Do not put secrets in hostnames; treat destination metadata as private run data.

The field uses the existing exclusive, flushed/fsynced outcome persistence.
Audit event schemas and ordering are unchanged; no destination is added to
`audit.jsonl`. Files remain under ignored `reports/live/`. A crash before outcome
persistence can still leave only the partial audit, as documented below.
Existing completed-report citations/provenance keep their original URLs.

No request is added to collect diagnostics. Timeouts, zero HTTP retries, request
budgets, TLS, SSRF, source retrieval and verification contracts are unchanged.
The previous failed run's destination cannot be reconstructed by this change.
The dashboard at port 8000 need not be restarted: future live CLI processes load
the update independently. Browser tests use their own server at port 8765.

Offline verification: 259 Python tests passed, including six new source-only
diagnostic tests covering mocked DNS/TCP/TLS failures, unsafe destinations,
redaction, redirect destinations, persisted failures and cancellation. Six
browser scenarios, compilation, JavaScript syntax and whitespace checks passed.
The publication scan found no credential-pattern or private-artifact findings.
No live provider/source requests were made during implementation or testing.

The following sections preserve the earlier Phase 9 preparation record.

Started from verified clean HEAD `3dea62b` on `master`. The operator reports
successful published GitHub Offline CI; its workflow is unchanged. No live
Mesh, Brave, retrieval or other external requests were made in this phase.
No credentials were inspected, spend incurred, checker activated, commit,
push or deployment performed. Developed by Chabir Ahamad Abusali.

## Existing diagnostics: conclusions and limits

The retained [Brave timeout investigation](timeout_and_demo_continuity.md)
records one timed-out request with no HTTP status. Its ten seconds span worker
scheduling, DNS, TCP, certifi-verified TLS, sending, headers and body together.
The earlier successful TLS handshake does not prove responsiveness of a later
authenticated HTTP request or the same resolved address. No retained stage
result identifies the expired stage. DNS/queue delay, TCP/TLS delay, slow
sending/headers/body or combined latency remain hypotheses, not a root cause.
Invalid authentication cannot be inferred from timeout/null status. No timeout,
retry, destination, TLS or network policy was changed to work around it.

Mesh's operator-reported HTTP 200/text-schema success does not establish agent
JSON compatibility. The prior exact-OK issue was confined to connectivity and
was corrected in Phase 7B. Real agents still require strict JSON without fences,
unique keys and role-specific schemas: planner tasks, evidence-linked drafter
claims, and verifier assessments covering every evidence ID with exact grounded
quotes. No retained live structured output demonstrates those contracts. Offline
HTTP fixtures validate our code path, not provider behavior or semantic accuracy.

## Execution and persistence

`deepdive_ai.live` composes only existing real implementations:

`ResearchQuestion -> MeshPlannerAgent -> RetrievalResearcherAgent ->
BraveSearchDiscovery -> HTTPWebRetriever + BoundedContentExtractor ->
MeshDrafterAgent -> MeshVerifierAgent -> bounded research-again loop ->
VerifiedClaimsSynthesizerAgent -> Report`.

Planner/drafter/verifier LLM calls route through `MeshAPIClient` and `MeshJSON`.
Researcher fetches discovered public URLs, validates source digests and retains
extraction transformations; synthesizer deterministically copies accepted claims
and citations. There is no fixture import, alternate provider or live dashboard
route. Python test callers explicitly inject HTTP doubles; the CLI has no such
option. The existing offline dashboard and its exact attribution are unchanged.

Preflight validates syntax, policy acknowledgements and environment credential
format only. It creates no transport or output files and never authenticates a
key, resolves DNS, validates account prices or promises successful research.
Credentials are inherited from the invoking PowerShell process via `MESH_API_KEY`
and `BRAVE_SEARCH_API_KEY`; `.env` is not loaded automatically. Safety gates are
checked before credential configuration. No price or credential is hardcoded.

With explicit execution, a fresh UUID directory under the current checkout's
`reports/live/` is created exclusively. Files are never overwritten:

- `manifest.json`: real-adapter mode, selected model, fixed profile and the
  operator-attested rates, explicitly not verified prices.
- `audit.jsonl`: bounded structured `AuditEvent` records, flushed and fsynced
  before proceeding. No prompts, evidence, model bodies, headers or error text.
- `report.json`: only after complete successful orchestration; the validated
  report, source URLs, timestamps, hashes, extraction locators, final claims,
  final verification rationale and canonical citations. Treat it as private
  research data even if its sources are public. JSON is escaped and not executed.
- `outcome.json`: safe terminal status, run ID, attempt counts, available fixed
  HTTP failure categories/stages/statuses and conservative unknown-cost status.

Audit creation/flush failure prevents subsequent workflow work. Final storage
failure exits unsuccessfully rather than claiming durable results. Hard process
termination, disk failure or interruption can leave partial files: a report's
presence alone is not proof the final outcome persisted. There is no resume or
automatic resubmission. On deadline cancellation the outcome records failure;
the workflow's last audit event may precede it because cancellation interrupts
the agent. Prior-round claims/raw model responses are not archived; provenance
is accumulated and the final report contains the final verification ledger.

File creation uses exclusive opens and owner-only modes where supported; run
directories request mode 0700. Windows access uses inherited filesystem ACLs,
not Unix modes. Git ignores are not encryption, access control or retention
policy: secure the checkout's `reports` directory and review private artifacts
before sharing. No credentials are intentionally written. The caller must not
put secrets into questions or untrusted research content.

## Fixed first-run profile and spending boundary

| Limit | Runner policy |
| --- | --- |
| Question | Nonempty, <=160 characters / 20 words; no control characters |
| Model | Explicit <=120-character identifier; exact served-model match |
| Tasks / claims | At most one task and two claims per round |
| Research retries | One, hence at most two rounds |
| Mesh | At most seven client HTTP attempts total, no retries, 15 seconds each |
| Mesh output | 1,024 requested tokens each; valid reported usage required and <=1,024 |
| Mesh bytes | 24,000 request-body bytes; 32,768 response bytes per request |
| Brave | At most two GETs, one result per search, no retries, ten seconds each |
| Source retrieval | At most two fetch operations / four HTTP attempts including redirects; no retries, ten seconds each |
| Source redirects/bytes | At most one redirect per fetch, existing SSRF checks; 256,000 response bytes |
| Extraction | Existing bounded HTML and resource-isolated PDF implementation, PDF <=3 pages |
| Entire run | 120-second overall awaited deadline |

The Mesh maximum is one planner call plus two rounds of one drafter and up to
two verifier calls. The synthesizer adds no LLM request. Empty research may use
fewer calls. The profile bounds attempts per process/run, not all invocations.
Planner instructions/claims/rationales are limited to 160 characters in this
controlled profile; verbose JSON fails instead of being repaired or retried.
Combined discovery queries must also satisfy the existing 400-character / 50-word
limit; excess queries fail before Brave transmission. Evidence selection is
bounded to one chunk per document and can miss relevant later content.

Agent/HTTP budgets include research-again work. API failures, malformed output,
storage errors and security failures abort, with no fixture fallback. Missing
or excessive usage and model substitution fail the new runner's Mesh boundary.
Certificate/hostname verification, certifi, DNS pinning, public-address checks
and safe redirects remain in the existing shared transport. The established
connectivity harness and general-purpose adapter defaults are unchanged.

This is **not a hard dollar ceiling**. Upstream routing/fallback/thinking,
unknown input-token counts, final-request cap overrun and timeouts can still be
billable. At most 7,168 output tokens are requested across seven Mesh calls;
that is not a guaranteed billable-token total. Rates are required for private
budget review and recorded in the manifest, but no synthetic cost estimate,
balance audit or invoice verification is produced. The safe outcome remains
`unknown_may_be_billable` after any attempted request. Review actual account
prices, balance, auto-recharge, per-key restrictions and permitted overrun before
attesting spend controls. If that exposure is unacceptable, do not execute.

## Future PowerShell commands: do not execute during preparation

Use the existing terminal holding both configured credentials. Do not display
them or enter keys at these non-secret prompts. Use a small public question;
the question/model become process arguments. First resolve Brave readiness and
approve full-run spend separately: a failed first Brave search can still follow
a billable planner call. A preflight pass does not resolve that blocker.

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
$liveArgs = @(
    '--question', (Read-Host 'Approved public question (up to 160 characters and 20 words)')
    '--model', 'google/gemini-2.5-flash'
    '--accept-gateway-uncertainty'
    '--confirm-spend-controls'
    '--mesh-input-usd-per-million', (Read-Host 'Approved Mesh input USD per million tokens')
    '--mesh-output-usd-per-million', (Read-Host 'Approved Mesh output USD per million tokens')
    '--brave-usd-per-request', (Read-Host 'Approved Brave USD per request')
)
.\.venv\Scripts\python.exe -m deepdive_ai.live @liveArgs
if ($LASTEXITCODE -ne 0) { throw 'Preflight blocked; do not execute.' }
```

The command above is offline preflight. Only after SPA review, independently
confirmed provider readiness and **separate explicit live-run/spend approval**,
the proposed one-invocation command in that same terminal is:

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.live @liveArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Run failed, blocked or needs review; do not retry automatically.' }
```

Exit codes: 0 = ready offline preflight, or completed report whose current
verification status is `verified`; 1 = workflow/configuration/storage failure;
2 = blocked preflight/invalid arguments; 3 = completed `needs_review` report;
130 = interrupted. `verified` reflects existing claim/evidence checks, not an
independent benchmark or correctness guarantee. Inspect private audit/outcome
and source citations after any authorized attempt. Do not rerun on failure or
uncertainty without reviewing exposure and authorizing another invocation.

## Offline verification scope

The final sprint fixes the reviewed enforced-timeout diagnostic gap. When a
bounded client cancels its inner transport, the runner now snapshots only the
fixed stage/status before propagating cancellation. A bounded, cycle-safe walk
through typed exception context retrieves the client's timeout classification
through sanitizing agent boundaries. No exception messages or raw responses
are exposed. External task cancellation remains `interrupted` with
`request_cancelled` diagnostics; it is not falsely called a provider timeout.
No timeout, retry, request, spending, TLS or verification limit changed.

Three regression tests cover the enforced per-request deadline (rather than
only injecting a preclassified error), external cancellation and safe/cyclic
exception context. The historical validation below remains a Phase 9 record;
the sprint's current validation is in [submission readiness](submission_readiness.md).

Seventeen new deterministic tests inject authored Mesh chat-completion HTTP
responses, Brave result envelopes and source bytes into real adapters and five
agents. They cover success/persistence/provenance, contradictions, insufficient
evidence and retry exhaustion, seven-call bounds, empty discovery, malformed
agent JSON, invented citations/quotes, Mesh model/usage violations, safe timeout
diagnostics, actual deadline cancellation, configuration gates, storage failures,
exclusive artifact creation and CLI default preflight. DNS/socket guards block
external access; no model accuracy scores or live findings are claimed.

Existing CI automatically discovers the added tests. No workflow, provider,
production verification rule or dashboard file was modified.

Validation: **238 tests passed in 9.744 seconds**, zero failures/errors/skips
(221 existing plus 17 new). Python compilation, both JavaScript syntax checks,
`git diff --check`, explicit new-file whitespace checks and both documented
PowerShell command-block syntax checks passed. Browser checks passed for all
six fixture scenarios, streamed progress, source navigation, contradictions,
bounded retries, empty/error states, fresh Markdown download and exact attribution
at desktop/mobile sizes, with zero browser errors or external page requests.

HEAD remains `3dea62b` on `master`. Only README is modified; `live.py`,
`persistence.py`, this runbook and `tests/test_live.py` are new, all unstaged.
The successful CI workflow, offline dashboard, attribution, production agents,
provider adapters, TLS/SSRF rules and connectivity harness are unchanged.
No live run or provider request occurred. Stop for SPA review; no commit,
push, deployment or checker activation.
