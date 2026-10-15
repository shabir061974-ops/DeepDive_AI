# DeepDive AI

Agent Arena 2026, Track 01: DeepDive — Self-Verifying Research Analyst.

## Phase 1 status

This repository contains a Python 3.12 foundation: validated immutable domain
models, five async agent interfaces, an injectable workflow coordinator, audit
events, and disabled Mesh/web adapters. Concrete research agents are deliberately
not implemented. The application cannot yet answer research questions.

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
  synthesizer protocols. Future concrete agents must use `MeshClient` for every
  application LLM call and `WebRetriever` for external source retrieval.
- `src/deepdive_ai/workflow.py`: caller-supplied agents, audit sink, run ID and
  optional clock. The planner runs once; each research attempt drafts and verifies
  again. The researcher receives the full prior bundle and insufficient-evidence
  feedback, and returns a complete updated bundle preserving previous records.
- `src/deepdive_ai/audit.py`: structured metadata events for start, planning,
  research, drafting, verification, retry, completion and failure. Use opaque
  run/question IDs; events contain no research text or exception details.
- `src/deepdive_ai/mesh.py`: typed Mesh requests/responses and client protocol.
  `MeshSettings.from_environment()` reads `MESH_API_KEY`, rejects missing/blank
  values and excludes the key from its representation. No provider endpoint or
  wire format is assumed. The supplied adapter always raises without networking.
- `src/deepdive_ai/retrieval.py`: real search/fetch contracts and original-byte
  digest validation. The supplied adapter always raises without networking.
- `src/deepdive_ai/api.py`: optional FastAPI factory with a `/health` endpoint
  reporting `{"status": "ok", "phase": "foundation"}`. This is process health,
  not research readiness. No research HTTP endpoint is exposed.

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

These checks establish structural consistency. They do not prove that an excerpt
entails a claim, that a URL is authentic, or that summary prose accurately reflects
claims. Those responsibilities require future real retrieval and concrete
verification/synthesis implementations. No real findings, citations or answers
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

FastAPI and Uvicorn are not installed in the supplied environment. The health
endpoint test is explicitly skipped when FastAPI is unavailable. No dependency
downloads, API calls, searches, server launch or deployment are needed for Phase 1.
No credential is needed for the tests. Supply `MESH_API_KEY` through the process
environment when integrating Mesh later; never commit it or log settings as a
dictionary. `.env` files are ignored and are not automatically loaded.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src main.py tests
```

Tests use fixed timestamps and explicit in-memory agent doubles. They cover the
sequence, bounded retries and exhaustion, absent evidence, invalid references,
provenance preservation/digest checks, synthesis integrity, audit failures,
credential handling, disabled adapters and existing greeting behavior. They do
not exercise live services or prove research accuracy.

## Deferred beyond Phase 1

Concrete LLM agents and semantic claim checking; Mesh transport/authentication
against official API specifications; real web search and fetching; destination
and redirect restrictions, timeouts, download limits and untrusted-page handling;
persistent evidence/audit storage; research API endpoints, authentication and UI;
production deployment and integration tests. A future transport must implement
its own network and secret protections before enabling external operations.

No autonomous external actions, real research, commits, pushes or deployment are
part of this phase. This workspace currently has no Git repository metadata, so
Git cannot report tracked/untracked changes until a repository is provided.
