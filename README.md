# DeepDive AI

Agent Arena 2026, Track 01: DeepDive — Self-Verifying Research Analyst.

## Project attribution

Developed by Chabir Ahamad Abusali

Copyright © 2026 Chabir Ahamad Abusali. All rights reserved.

## Current status

Latest verified code checkpoint: `28339c11289c88be3bc448ab0578a3819dde9b06` on
`master`. This table supersedes the historical phase records below.

| Area | Evidence at this checkpoint | Status |
| --- | --- | --- |
| Offline workflow | Real coordinator and five agents with scripted Mesh responses and local retrieval fixtures | Verified offline |
| Dashboard | Six fixed scenarios, citations, verification ledger, audit events, Markdown export and desktop/mobile checks | Verified offline only; the dashboard remains a labeled fixture demo and is **not** live research |
| Automated verification | 483 offline Python tests passed at `28339c1`; six browser scenarios passed in CI | Verified |
| GitHub Offline CI | [Successful run 39663310116](https://github.com/shabir61974-ops/DeepDive_AI/actions/runs/39663310116) for `28339c1` (483 tests); latest checked: [successful run 39816805642](https://github.com/shabir61974-ops/DeepDive_AI/actions/runs/39816805642) for `1e39c18` (**495 tests**, six browser scenarios) | Verified |
| Genuine live research (GitHub) | [Live Research Demo run 39768843859](https://github.com/shabir61974-ops/DeepDive_AI/actions/runs/39768843859), October 22, 2026, commit `28339c1`: the run and its job finished `success`. Operator-reported from the run's Summary page: all five agents completed, 2 claims supported with citations to MDN's HTTP 304 documentation, Mesh 4 / Brave 1 / source 1 requests | Passed once; see [GitHub live run evidence](docs/submission_readiness.md#github-live-run-evidence) |
| Earlier live research (local) | One completed local run on October 20, 2026 at `ee50eb2` (`56943dceac174beebd53e9b10573344c`, HTTP 405/501 question) | Passed once; see [live run evidence](docs/submission_readiness.md#live-run-evidence) |
| Live cost | Each run is bounded at about USD 0.04 (GitHub profile) by configured caps and operator-approved rates | Estimate only; actual provider billing **not verified** |
| Live reliability and accuracy | Nine GitHub live-demo runs on October 21, 2026 (all before `28339c1`), five earlier local attempts and an older `f1728f28…` attempt failed closed before these successes | **Not established**; two successful runs are not a reliability or accuracy measure |
| Official Agent Arena checker | Activated at commit `0b3c253` as [`agent-arena-2026-checks.yml`](.github/workflows/agent-arena-2026-checks.yml), an unchanged copy of the organizer's workflow. [First run 39809709272](https://github.com/shabir61974-ops/DeepDive_AI/actions/runs/39809709272): 3 of 4 critical checks passed; the requirements check failed because no LLM client was declared. The optional [Mesh SDK diagnostic](#optional-mesh-sdk-diagnostic) was added in response. [Run 39816805767](https://github.com/shabir61974-ops/DeepDive_AI/actions/runs/39816805767) on `1e39c18`: **all 4 critical checks passed** (code compiles, requirements list a web framework and an LLM client, Mesh API referenced, Mesh key valid). The organizer did **not** record either result, because the entry had not yet been submitted. The entry has since been submitted and the organizer is awaiting a recorded check | Checks pass on GitHub Actions; organizer recording and acceptance **not yet confirmed**. Offline CI does not establish acceptance |
| Demo video | [YouTube: youtu.be/84Fua4MaO-0](https://youtu.be/84Fua4MaO-0) (published by the repository owner; not re-reviewed in this update) | Optional for the organizer; offline dashboard and live-run segments are labeled separately |

The completed runs passed every existing safeguard unchanged: strict model
identity and `X-Mesh-Routing-Fallback: false` routing checks, rejection of
unverified cache hits, strict agent JSON and schemas, evidence-ID and exact-quote
verification, TLS, SSRF and public-IP pinning. Claims were judged supported
by the live Mesh Verifier and checked programmatically as exact excerpt quotes;
they were not independently reviewed by a person. Earlier blockers (slow server
DNS, Mesh cache hits, fenced agent JSON, Planner task count, claim length and a
DoH timeout) and their fixes are recorded in the [live run evidence](docs/submission_readiness.md#live-run-evidence).
The failed runs remain as historical evidence and are not hidden.
See also the [submission checklist and video script](docs/submission_readiness.md),
[live runner](docs/live_research.md) and [identity/routing policy](docs/mesh_model_identity.md).

## Fresh-checkout quickstart

Use Windows PowerShell 5.1, Git and Python 3.12. From a parent directory where
`DeepDive_AI` does **not** already exist:

```powershell
git clone https://github.com/shabir061974-ops/DeepDive_AI.git
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
Set-Location -LiteralPath '.\DeepDive_AI'
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
.\.venv\Scripts\python.exe -m pip install -e '.[backend]'
if ($LASTEXITCODE -ne 0) { throw 'Installation failed.' }
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Offline tests failed.' }
.\.venv\Scripts\python.exe -m uvicorn deepdive_ai.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://127.0.0.1:8000/. No API keys or Node build are needed for the demo.
Installation downloads dependencies; tests and fixtures use no live providers.
These commands use supported entry points; a fresh install was not rerun during
this documentation-only update. Reuse an already running dashboard instead of
starting another instance or stopping it. Keep the server on loopback.
`main.py` is only a greeting. See [setup details](#local-setup) and
[browser/test requirements](#verification).

## Six offline scenarios

Every scenario is labeled **OFFLINE FIXTURE DEMO — NO LIVE RESEARCH**. Authored
fixture bytes and scripted assessments exercise actual backend validation;
they do not evaluate a live model's research accuracy.

| Scenario | Actual fixture behavior | Outcome |
| --- | --- | --- |
| Supported evidence | One supported claim and its citation | Verified report |
| Direct contradiction | Counterevidence rejects the claim; no research retry | Needs review; no accepted claim |
| Conflicting sources | One research-again loop adds counterevidence; contradiction veto prevents acceptance | Needs review; insufficient evidence |
| Evidence gap resolved | One research-again loop adds support and preserves earlier provenance | Verified report |
| No evidence available | One research-again loop still finds no evidence; claim/assessment model calls are skipped | Needs review; no claims |
| Malformed assessment | Verifier's scripted JSON violates its contract | Safe failure and audit events; no final report/export |

Fixture citations use authored `.invalid` identifiers. The live runner never
substitutes fixtures when providers fail. Events and counts come from execution;
they are not benchmark scores or invented timings.

## Reproducible offline self-evaluation

Evaluation **v1.0** adds six fictional research questions separately from the
dashboard scenarios. The local measured run passed **6/6 cases**, using the
existing five agents, Mesh response parser, Brave discovery adapter, HTTP
retriever and coordinator with injected HTTP doubles. Real DNS, sockets, default
HTTP transports and private credential loading are blocked during evaluation.
All documents are labeled **AUTHORED OFFLINE EVALUATION — NOT LIVE RESEARCH**;
all source identifiers use `.invalid` URLs.

These results measure **deterministic offline correctness against scripted
assessments**, not live semantic accuracy, independent source truth, search
quality, research completeness or organizer-checker acceptance. The scripted
verifier deliberately proposes positive verdicts; actual production vetoes must
reject contradictory or insufficient assessments. This cannot detect a real
model incorrectly calling an unrelated excerpt supportive. Complete live
runs have since passed (see [Current status](#current-status)); they do not measure
live reliability or semantic accuracy.

| Case | Final supported/unsupported/insufficient claims | Accepted citations traceable | Unsafe acceptances | Contradiction safeguard | Retries observed/expected |
| --- | --- | --- | --- | --- | --- |
| Supported comparison | 2/0/0 | 2/2 | N/A | N/A | 0/0 |
| Direct contradiction | 0/1/0 | N/A | 0/1 | 1/1 | 0/0 |
| Conflicting documents | 0/0/1 | N/A | 0/1 | 1/1 | 1/1 |
| Evidence-gap recovery | 1/0/0 | 1/1 | N/A | N/A | 1/1 |
| Missing evidence | 0/0/0 | N/A | N/A | N/A | 1/1 |
| Qualifier preservation | 0/0/1 | N/A | 0/1 | N/A | 1/1 |

Aggregate classification correctness: supported **3/3**, unsupported **1/1**,
insufficient evidence **2/2**. Unsafe acceptances: **0/3** eligible unsafe claims;
citation traceability: **3/3** accepted citations; contradiction safeguards:
**2/2** eligible cases; retry correctness: **6/6** cases. Four research retries
occurred in total, with at most one per case. Each comparison claim resolves to
a different authored source identity; this is not real-world source independence.
N/A means no eligible observations, not a passing percentage. Final claims are
counted once; initial/retry rounds remain available separately in JSON.

After installing `.[backend]`, run from the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -B tests/offline_evaluation.py
if ($LASTEXITCODE -ne 0) { throw 'Offline evaluation failed; inspect the saved observations.' }
```

The runner writes a new `artifacts/offline-evaluation-v1.0/run-<id>/` containing
`results.json` and `results.md`, including the evaluation version, manifest and
document hashes, evaluation-code hash, Git checkpoint and dirty-tree flag.
Artifacts retain actual report/round/audit observations and failed checks; a
failed case causes a nonzero exit. Later invocations do not overwrite prior runs.
The reported local measurements were made on baseline `de78e25` with uncommitted
evaluation changes, explicitly recorded as a dirty tree. Rerunning after checkout
records that checkout's actual commit. Generated artifacts are ignored and are
not published; the fixture manifest and runner are version controlled.

Classification metrics compare exact claim text/status to separately authored
expectations. Unsafe acceptance counts any accepted claim not expected supported.
Citation checks follow evidence/source IDs and compare URL, fixture-byte digest,
fixed retrieval timestamp, locator and excerpt bytes. Contradiction scoring
requires observed contradictory assessments and their expected effective verdict
and exclusion from synthesis. Retry scoring reads actual audit events. The
regression suite also mutates verdicts/citations and injects failures to check
that scoring preserves failures rather than substituting expected results.

## Private credential configuration

Provider settings automatically fall back to the protected private file
`D:\Developments\DeepDive_AI.env`. Process variables take precedence, including
an explicitly empty value. Use the [masked, one-time PowerShell setup](docs/private_configuration.md).
No credential file is created automatically and no existing keys are copied.
Source-only diagnostics and the offline dashboard remain credential-independent.
The fallback is a fixed Windows path, not a repository `.env` search or model
configuration file. An empty process key overrides a stored key and fails
validation. Isolated provider modes call only their selected provider, but
fallback parses the shared two-key file. Windows ACL inspection uses a bounded,
metadata-only PowerShell subprocess; file contents are never evaluated as shell
code. Do not print keys or private file contents to troubleshoot configuration.

## Execution modes, limits and outputs

| Mode | Local bounds | Data and authorization |
| --- | --- | --- |
| Dashboard | Fixed scenarios and bounded fixture workflow | No credentials/external requests; in-memory audit and explicit Markdown download |
| Provider connectivity | At most one request per selected provider, zero retries; Mesh 15 seconds/64 output tokens, Brave 10 seconds | Offline preflight by default; execution and spending acknowledgements required |
| Source-only diagnostic | One fixed public-source GET, zero retries/redirects, 10 seconds, 256,000 response bytes | Offline preflight by default; no provider keys; separate execution authorization |
| Live research CLI | One task, two claims per round, at most one research retry; Mesh <=7 calls, Brave <=2 calls, source <=2 fetches/<=4 HTTP exchanges including redirects; the second search result is fetched only if the first source fails | Offline preflight by default; explicit execution, rates and risk acknowledgements; no fixture fallback |
| GitHub live demo | The live CLI profile above, run once per manual trigger on a Windows runner, with no retries | Owner-approved `live-demo` environment; public sanitized step summary; no artifacts. See [GitHub live demo](docs/live_research.md#github-live-demo) for setup, visibility and cost |
| Library adapters | Configurable defaults, distinct from the live CLI profile | Generic library retry defaults do not describe live execution |

The live CLI has a 120-second awaited workflow deadline, Mesh 25-second and
Brave/source 10-second request deadlines, zero client HTTP retries, and 1,024
output tokens per Mesh call. Timeouts span multiple stages, not a fresh allowance
per DNS/TCP/TLS/header/body step. Cancellation is not a guaranteed process-exit
deadline for blocking DNS/worker cleanup. Research-again rounds are distinct
from HTTP retries. Client limits do not guarantee gateway attempt counts or a
hard USD ceiling. See [the live profile](docs/live_research.md#fixed-first-run-profile-and-spending-boundary)
and [isolated connectivity controls](docs/controlled_connectivity.md).

The live Mesh budget was increased from 15 to 25 seconds after an operator
dashboard record confirmed a remotely successful, billed request whose local
client timed out at response headers. This is bounded operational tuning, not
proof of the underlying latency cause or a successful live fix. It remains below
the unchanged 30-second agent deadline; the 120-second workflow deadline can
terminate a run before every request allowance is used. Isolated connectivity
diagnostics retain their original 15-second Mesh limit.

Live execution writes ignored private `reports/live/<run-id>/manifest.json`,
`audit.jsonl`, `outcome.json` and, on completed workflows, `report.json`. Audit
events contain metadata; reports contain claims, evidence and source provenance
and should be treated as private research data. Writes are exclusive and
flushed/fsynced; crashes/storage failures can leave partial runs. No automatic
resume or full original-document archive exists. Use a trusted output directory
with appropriate inherited Windows ACLs. Dashboard audit is in memory, with no
durable run-history store; its Markdown download is separate from live persistence.

## Troubleshooting

| Symptom | Safe interpretation and next step |
| --- | --- |
| Mesh HTTP 200 with identity/routing rejection | Inspect `model_matches` and `routing_metadata_valid`. Only the confirmed Gemini pair may normalize on version 2026-08 onward. Missing fallback metadata, any routing-model header, fallback or malformed metadata still fails closed. Do not bypass validation. |
| Agent JSON failure | Inspect fixed failure codes. Planner, Drafter and Verifier accept one complete standalone JSON fence (lowercase `json` tag or none); prose outside it, multiple or partial fences, malformed JSON, duplicate keys, nonfinite values, invalid schemas and unsupported evidence or quotes still fail. `object_required` means the reply (or a claim row) was not a JSON object, e.g. a top-level array or `null`. A Drafter with no justified claim returns `{"claims": [], "status": "insufficient_evidence"}` (or just `{"claims": []}`), which leads to research-again and then a `needs_review` report; any other status, or a status with claims, fails as `drafter_invalid_status`. `malformed_agent_json` outcomes add a fixed `agent_json_shape` (`fenced_valid_json`, `fenced_invalid_json`, `embedded_fence`, `non_json_prefix`, `trailing_content`, `incomplete_json`, `invalid_syntax`) describing form only; it never changes acceptance. Truncation by token limit is rejected earlier, because the completion must report `finish_reason: stop`. Never infer unavailable response text. |
| Brave/source timeout | Inspect the last observed stage and source hostname where available. A TCP timeout does not prove authentication failure or source outage. Do not automatically retry or increase budgets. Earlier HTTP 200 does not guarantee later availability. |
| Missing/unsafe credentials | Check presence without displaying values, empty-process precedence and private-file ACL metadata. Use masked setup; never paste keys into commands or logs. |
| Unsafe URL/redirect or oversized/invalid document | Retrieval fails closed. Do not bypass DNS/IP, TLS, redirect or extraction checks to obtain a citation. |
| Port 8000 already in use | Reuse the existing dashboard; do not stop or restart another instance. |

## Publication and CI

The existing [Offline CI workflow](.github/workflows/offline-ci.yml) runs on push
and pull requests with Windows Server 2025, Python 3.12 and Node 24. It installs
the backend extra and runs offline Python tests, compilation, JavaScript syntax
and installed-Chrome smoke checks without provider credentials. Dependency
installation needs package-index access; offline tests are not live validation.

The official checker is active as
[.github/workflows/agent-arena-2026-checks.yml](.github/workflows/agent-arena-2026-checks.yml),
an unchanged copy of [docs/agent-arena-workflow.yml.template](docs/agent-arena-workflow.yml.template).
It runs on every push, uses OIDC and the `SUBMISSION_TOKEN` and `MESH_API_KEY`
repository secrets (a dedicated, spending-limited checker key), and downloads the
organizer's `checks.py` at run time, which this repository cannot review. It is
separate from Offline CI, which receives no secrets. Organizer acceptance is not
established by Offline CI.
No license is added; organizer and licensing requirements need confirmation.

## Historical phase records

These records preserve what was known during earlier phases, through the
Architecture heading. Their checkpoints, counts and statements of unverified
connectivity are historical, not current status. The [current status table](#current-status)
takes precedence. Later private-file fallback also means isolated provider modes
can parse a shared credential file; historical claims of never reading the
other provider's credentials must not be treated as the current loading contract.

### Historical submission status and demo

*Historical record; superseded by [Current status](#current-status), including
the first completed live run.*

The five-agent pipeline is verified end to end with mocked HTTP provider
responses, and the six-scenario dashboard remains explicitly offline. The
published Phase 9 checkpoint `1e181bd` passed its own [Offline CI run](https://github.com/shabir061974-ops/DeepDive_AI/actions/runs/38929566748).
Mesh text connectivity and Brave HTTP 200 are operator-reported verified; Mesh
agent-JSON compatibility and a complete live five-agent run remain unverified. No live research success or benchmark accuracy
is claimed. See the [demo rehearsal, YouTube script and submission checklist](docs/submission_readiness.md)
and [controlled live-run instructions](docs/live_research.md).

The latest operator-reported Planner probe returned HTTP 200 and
`malformed_agent_json`; the actual response format was not retained. Planner
now accepts a single complete standalone Markdown JSON fence while preserving
strict JSON and task-schema validation. [Planner diagnostic findings](docs/planner_compatibility.md)
explain the scope and remaining uncertainty. This is offline-tested compatibility,
not a confirmed fix for the live failure; no new provider request was made.

### Historical publication and CI readiness

*Historical record from Phase 8; see [Current status](#current-status) for live results.*

Phase 8 preparation starts at `4f415ac` on `master`. The application remains a
local **OFFLINE FIXTURE DEMO — NO LIVE RESEARCH**. No complete live research run
has passed. See [local setup](#local-setup), [verification](#verification) and the
[publication audit, official hackathon CI requirements and live-integration gaps](docs/publication_readiness.md).

The prepared `.github/workflows/offline-ci.yml` runs on pushes and pull requests:
Windows Server 2025, Python 3.12, Node 24, a new `.venv`, the backend extra,
all offline Python tests, compilation, JavaScript syntax and the installed-Chrome
smoke test. It uses no Mesh, Brave or submission credentials. Hosted execution
passed for published checkpoint `1e181bd`; later changes must pass their own CI
run before being described as verified.
Package installation downloads dependencies; the test workload is offline.

The exact official hackathon workflow was retrieved and preserved as the
**inactive** `docs/agent-arena-workflow.yml.template` (historical; it was later activated, see Current status). It is separate from our
offline CI, requires OIDC/submission/provider secrets, and downloads a protected
checker whose behavior could not be reviewed. Do not activate it until the
official checks and possible provider usage are separately approved. The public
workflow template was downloaded; no protected checker or provider was called.

### Historical Phase 9 live-run preparation

From clean checkpoint `3dea62b`, `python -m deepdive_ai.live` now provides an
explicit real-adapter entry point, defaulting to **offline preflight**. It
requires a question, approved model, both provider credentials, account rates
and spending/gateway acknowledgements. Only a separately authorized `--execute`
invocation starts real research. The runner persists metadata audit events and
private report/outcome JSON under ignored `reports/live/<run-id>/`; no fixture
fallback exists. See the [exact future command, fixed request limits, diagnostic
findings and storage limitations](docs/live_research.md).

Live Mesh agent-JSON compatibility and Brave connectivity are still unverified;
no live run was performed here. The dashboard remains the labeled offline
fixture demo. GitHub Offline CI success was operator-reported, and its workflow
is unchanged. The official hackathon checker remained inactive at that time
(historical; it has since been activated, see Current status).

### Historical Phase 6 status

The local dashboard and end-to-end offline fixture demo are implemented from
clean checkpoint `1ff0e5b`. Start the installed backend from PowerShell:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
.\.venv\Scripts\python.exe -m uvicorn deepdive_ai.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open **http://127.0.0.1:8000/**. No API keys, Node build, external fonts, CDN or
live provider services are needed. Keep the server bound to loopback; stop it
with Ctrl+C. FastAPI and uvicorn are the existing optional `backend` dependencies
and are already installed in the project environment.

The dashboard is permanently labeled **OFFLINE FIXTURE DEMO — NO LIVE RESEARCH**.
Six fixed scenarios execute all available steps through the real five-agent
workflow: supported evidence, direct contradiction, conflicting sources, evidence
gap recovery, empty evidence and malformed assessment. Agent starts/completions
and audit events stream from actual execution; no delays or timing figures are
invented. Inspect accepted claims, the verification/contradiction ledger, prior
rounds, evidence/provenance, research-again loops, computed run counts and a
Markdown download. There is no arbitrary-question or live-research route.

Evidence bytes and scripted model responses reuse the authored fixtures from
`tests/test_agents.py`. Exact source hashes are calculated locally and fixture
timestamps are explicitly synthetic. Verification runs the existing strict
schemas and contradiction vetoes, but scripted assessments are not independent
model accuracy evaluation. Counts are not benchmark scores. See the
[Phase 6 runbook, security boundaries and verification](docs/phase6_dashboard.md).

### Phase 7A Mesh-only readiness

Preparation from clean checkpoint `bfa152d` adds `--mesh-only` to the controlled
connectivity harness. It skips Brave credentials, pricing, transport and calls;
Brave connectivity does not block this isolated test. Default execution remains
offline preflight. The existing Mesh TLS, one-request/zero-retry policy, timeouts,
token/byte limits and spending acknowledgements are unchanged. No live Mesh
request was performed; authentication, model availability, wire compatibility
and account pricing remain unverified. See the [readiness findings and separately
authorized future test commands](docs/mesh_readiness.md). The dashboard remains
an offline fixture demonstration. Await SPA review before live execution.

### Phase 7B HTTP 200 validation investigation

The operator reports one Mesh HTTP 200 response with parsed usage but a failed
application-level compatibility check. Investigation from `763c197` confirms
the strict response parser had already passed; the retained report cannot
identify the failed model, optional version or exact-reply condition. Added
allowlisted check results and failing-check names without changing acceptance
rules or exposing response values. See the [findings, remaining hypotheses and
SPA recommendation](docs/mesh_validation_investigation.md). No additional live
requests were made. The dashboard remains an offline fixture demonstration.

The Phase 7B correction from `8006fa9` makes exact `OK` diagnostic-only in the
connectivity harness: complete, nonempty assistant text is sufficient when model,
schema, confirmed output cap and any requested version checks pass. Missing or
invalid usage fails because the cap cannot be confirmed. Production agent JSON,
claim verification and evidence contracts are unchanged. See the [acceptance
correction and its limits](docs/mesh_validation_investigation.md#acceptance-correction-from-8006fa9).
No new live test was performed during this correction.

### Earlier provider checkpoints

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

Brave-only retest preparation from `626b394` adds `--brave-only` to the harness.
It performs at most one Brave request and never reads Mesh credentials or invokes
Mesh, on success or failure. Existing Brave spending and security controls remain
required. See the [preflight and proposed retest commands](docs/controlled_connectivity.md#brave-only-retest-preparation);
no live retest was performed during preparation.

The later operator-run Brave-only retest timed out; Mesh remained unattempted.
The [timeout investigation and demo-continuity assessment](docs/timeout_and_demo_continuity.md)
from `52bd82b` documents the shared 10-second exchange budget and adds sanitized
per-request failure stages without widening limits. Existing fixtures exercise
the five-agent workflow offline; no dashboard/demo API or live accuracy claim
is introduced by this investigation.

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
- `src/deepdive_ai/api.py`: local FastAPI dashboard, fixed-scenario catalog and
  streamed offline demo runs. `/health` still reports
  `{"status": "ok", "phase": "foundation"}`. This is process health,
  not live research readiness. No live research HTTP endpoint is exposed. The Phase 1 health
  payload, greeting behavior and agent protocol signatures remain unchanged.
  Phase 1/2 validation and bounded retries remain intact. All 104 earlier tests
  continue to run unchanged. Disabled Phase 1 adapters remain available and disabled.
- `src/deepdive_ai/demo.py`, `fixtures/research.json`: explicitly scripted fixture
  dependencies injected into the existing coordinator and concrete agents; actual
  execution/audit records, computed counts and a canonical report-derived export.
- `src/deepdive_ai/static/`: responsive dark dashboard using local HTML/CSS/JS/SVG,
  streamed run progress, report, claim/contradiction filters, evidence and audit views.

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

Credentials use process environment variables `MESH_API_KEY` and
`BRAVE_SEARCH_API_KEY`, falling back to the protected private file described
[above](#private-credential-configuration). No project-local `.env` search occurs.
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

### Optional Mesh SDK diagnostic

The production pipeline and all five agents use only the validated, bounded HTTP
adapter above (standard library; no LLM SDK). Separately, the optional `mesh-sdk`
extra installs the OpenAI-compatible Python client (`openai`) for one explicit,
opt-in compatibility diagnostic, `deepdive_ai.mesh_sdk`. It is never imported by the
pipeline, the dashboard, the tests' research paths or CI, and nothing runs it
automatically.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[mesh-sdk]'
.\.venv\Scripts\python.exe -m deepdive_ai.mesh_sdk            # preflight, no request
.\.venv\Scripts\python.exe -m deepdive_ai.mesh_sdk --execute  # one small billable request
```

- It reads `MESH_API_KEY` from the process environment only (never from the private
  credentials file) and talks only to `https://api.meshapi.ai/v1`, with no retries, a
  16-token cap and a fixed prompt. `OPENAI_*` environment settings cannot change the
  endpoint, organization or project.
- With `--execute` it sends one non-streaming request and runs the same
  routing-header, cache and model-identity classification as production on the SDK's
  response, then prints only fixed fields: status, classification, rejection codes,
  check results, finish reason and token counts. It prints no key, prompt or
  completion, and a rejected or failed result exits non-zero.
- Offline tests cover endpoint configuration, missing and invalid keys, cache and
  routing rejection, sanitized errors and isolation from the pipeline, with network
  connections blocked. No live SDK request has been made, so real-provider
  compatibility of this path is unverified.

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

DNS uses the operating system by default. `DEEPDIVE_DNS_RESOLVER=doh` instead
resolves IPv4 (A) records over DNS-over-HTTPS JSON at fixed IP endpoints
`https://1.1.1.1/dns-query`, then once `https://8.8.8.8/resolve` on failure, with
certifi verification against the endpoint IP, no proxy, a 2.5-second deadline and
65,536-byte cap per attempt. Status, truncation, question name, answer count and
every address are validated strictly; the returned addresses still pass the same
public-IP check and pinning above. Successful all-public answers are cached per
process for their minimum record TTL (capped at 300 seconds, at most 64 hosts,
least-recently-used eviction); failures, zero/invalid TTLs and any non-public
address are never cached, and hits are re-checked before use. Any other value is rejected; unset it or use
`system` to roll back. This is for hosts whose OS resolver waits on an
unresponsive DNS server; it bypasses the hosts file and OS DNS cache and discloses
looked-up hostnames to Cloudflare/Google. No live DoH request is made by tests.

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
are bundled as real research; authored `.invalid` records are used only by the
explicit offline fixture demo and tests, never as fallback live evidence.

## Local setup

Use Python 3.12 (the supplied environment is Python 3.12.10). From the cloned
repository directory in PowerShell, install the backend extra for the dashboard
and complete test suite. Package installation requires public package-index
access, including the declared setuptools/wheel build dependencies.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[backend]'
.\.venv\Scripts\python.exe main.py
```

`main.py` prints the greeting. Launch the offline dashboard separately:

```powershell
.\.venv\Scripts\python.exe -m uvicorn deepdive_ai.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://127.0.0.1:8000/ and stop with Ctrl+C. Keep it bound to loopback.
No API keys are needed. The core-only `pip install -e .` supports library/greeting
use; it does not install FastAPI and cannot run the complete dashboard test suite.
The pinned core dependencies are `pypdf==6.20.1` and `certifi==2026.7.22`.
FastAPI and uvicorn remain in the declared `backend` extra; its version ranges
are not a complete dependency lock. Python versions other than 3.12 are not
covered by this CI preparation.

Future separately authorized live research requires process environment names
`MESH_API_KEY` and `BRAVE_SEARCH_API_KEY`, an approved model and explicit provider
budgets. The protected private file is an optional fallback; credential files
are ignored by Git and excluded from packaging. These installation commands do
not execute the existing live CLI. No live dashboard endpoint is provided.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src main.py tests
node --check src/deepdive_ai/static/dashboard.js
node --check tests/browser_smoke.cjs
node tests/browser_smoke.cjs
git diff --check
```

Browser checks require Node 24 and installed Chrome on Windows; an alternative
installed Chromium executable can be passed as the script's first argument.
They start temporary loopback services on ports 8765 and 9337, block external
page requests, verify all six scenarios and the exact developer attribution at
desktop/mobile sizes, and save generated output only under ignored `artifacts/`.
No npm install or provider credential is needed. The offline GitHub workflow
uses this same Windows setup and fails if the required browser is unavailable.

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

Support-confirmed model normalization is implemented offline; a completed,
independently reviewed live five-agent run remains outstanding. Other deferred work includes
additional discovery providers and search quality evaluation, browser-rendered
HTML, OCR, better PDF layout/relevance selection, independent semantic accuracy
evaluation, source-quality/completeness checks, durable dashboard history, a full
source-document archive, authenticated live research endpoints and deployment.
Prompts separate untrusted data and forbid embedded instructions, and
strict schemas/citation/quote checks constrain output, but prompt injection and
semantic misclassification remain model risks. No live source or model accuracy
claim is made. The workflow is not started by the greeting or health endpoint.

The earlier Phase 6/7B dashboard checkpoint was `4f415ac`; current baseline and CI
evidence are recorded at the top. PDF export and durable dashboard history remain
deferred. Live adapters, a CLI and file-based audit/report persistence exist;
complete live research validation does not. The dashboard remains a local
fixture demonstration, not an authenticated production service. The repository
is public; no hosted application deployment is claimed.
