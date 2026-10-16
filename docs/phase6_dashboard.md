# Phase 6: local dashboard and offline workflow demo

Starting HEAD `1ff0e5b` and a clean working tree were verified before editing.
This implements the requested local dashboard; it does not investigate Brave,
activate providers, inspect credentials, deploy, push or commit. Claude Code was
not opened or used.

## Run locally

Python 3.12 and the project's existing `backend` extra (FastAPI/uvicorn) are
required. They are installed in the supplied `.venv`; no dependency download or
frontend build is needed in this environment.

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
.\.venv\Scripts\python.exe -m uvicorn deepdive_ai.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open **http://127.0.0.1:8000/** in a browser. Stop the server with Ctrl+C.
No `MESH_API_KEY` or `BRAVE_SEARCH_API_KEY` is required. Existing environment
credentials are not read by these routes. Keep the server bound to loopback;
it has no user-account system and is not intended for network/public hosting.
`main.py` and the original `/health` payload remain unchanged.

## Demonstration walkthrough

The persistent banner reads **OFFLINE FIXTURE DEMO — NO LIVE RESEARCH**.
The question is fixed and read-only. Choose one of these scenarios and press
**Run offline demo**:

| Scenario | Actual backend result |
| --- | --- |
| Supported evidence | One accepted fixture claim, resolved citation, verified report |
| Direct contradiction | One unsupported claim, no accepted final claim, needs review |
| Conflicting sources | One research retry adds counter-evidence; a positive scripted verdict is vetoed to insufficient evidence |
| Evidence gap resolved | One research retry adds support; the report is accepted with prior provenance preserved |
| No evidence available | One retry, zero claims/sources, needs review; no answer invented |
| Malformed assessment | Verifier output parsing fails; failure audit and no report/export |

Inspect the five workflow cards, then switch between:

- **Report:** canonical accepted claim text, local inline citation buttons and
  report status. Unresolved/rejected claims never enter the accepted narrative.
- **Verification:** all latest-draft claims, status filters (including contradictory
  evidence), scripted proposed verdict, actual effective classification, exact
  quotes, reasons and prior verification rounds when research repeats.
- **Evidence:** exact source excerpts, byte locators, source/evidence IDs, SHA-256,
  explicit local fixture method and labeled synthetic fixture timestamp. `.invalid`
  fixture identifiers are text, not links to supposedly live sources.
- **Audit trail:** actual agent start/completion/failure and coordinator audit
  records in sequence, with real run timestamps. Research-again events are
  highlighted. No agent activity, sleeps or durations are manufactured.
- **Run overview:** computed classification/source/evidence/retry/scripted-call
  counts only. No accuracy percentage, benchmark score or live evaluation metric
  is available or claimed.

**Export Markdown** downloads the completed report/ledger/evidence/audit with the
offline warning included. Citation anchors point to local source sections. The
filename is derived from an allowlisted scenario, and report content is escaped
where needed. No server file or persistent history is written. Failed runs cannot
export a report. Empty and disconnected-backend states explain what is missing.

## What executes

`demo.py` uses `build_research_workflow` and its existing concrete Planner,
Researcher, Drafter, Verifier and Synthesizer. Injected `FixtureMesh` implements
the existing Mesh abstraction with scripted responses; `FixtureDiscovery` and
`FixtureRetriever` supply allowlisted authored bytes without an HTTP transport.
They are demo adapters, not live providers. Unknown fixture URLs fail closed.

The text/question/URLs/timestamp in packaged `fixtures/research.json` are reused
from `tests/test_agents.py`. Sources are constructed with real hashes of those
fixture bytes and explicitly local/synthetic provenance. Tests remain unchanged.
No final research report or verdict is pre-rendered: real agent parsing, quote/
citation checks, contradiction vetoes, bounded coordinator retry, canonical
synthesis and audit validation run on every click. The semantic assessments
themselves are **scripted**, not independent factual/model evaluation. In the
conflict scenario a scripted `supported` proposal is actually downgraded by the
verifier's existing logic.

Thin observation wrappers delegate calls unchanged and record starts, completions
and safe failures. The existing audit-event model is preserved. The frontend
receives newline-delimited JSON while execution happens; it never replays events
with fake delays. Fast scenarios can finish almost immediately. One agent may
have several real executions after a retry; the UI shows those invocation counts.
No attempt was made to animate a slower workflow for presentation.

## API and security boundaries

- `GET /`: packaged dashboard. Local assets at `/static/`; no CDN, remote font,
  analytics, build service or third-party frontend script.
- `GET /api/demo/scenarios`: allowlisted scenario catalog and explicit
  `live_research_enabled=false`.
- `POST /api/demo/runs`: only `{"scenario_id":"supported"}` (or another known
  ID), JSON content type and `X-DeepDive-Demo: offline`. Extra/duplicate fields,
  arbitrary questions, unknown IDs and bodies over 1,024 bytes are rejected.
  Responses stream actual events and a terminal result/error. There is no live
  research endpoint and no provider-key input or output.
- Two concurrent runs per app process; excess runs receive 429. Each run has
  bounded agent/source/evidence budgets, zero or one research retry and a
  10-second offline workflow deadline. This is separate from unchanged live
  adapter timeouts/retries/spending controls. Disconnect cancels the execution
  task and releases its slot; no network task exists to continue elsewhere.
- Loopback client checks and restricted Host headers prevent remote access and
  DNS rebinding. Mutation requests require the custom same-origin header, with
  Origin checked when supplied. No permissive CORS rules are added. This is not
  account authentication against other local users/processes; use only on a
  trusted machine and do not expose it through a proxy or port-forward.
- CSP restricts content/connections/scripts/styles to this origin, with frame,
  object and form restrictions. No inline scripts, `innerHTML`, `eval` or remote
  citation navigation are used. No-store, nosniff and no-referrer headers apply.
  Export uses a browser Blob, not shell commands or a filesystem path supplied
  by the user. Unexpected workflow errors produce fixed sanitized messages.
- `pyproject.toml` packages static assets and fixture JSON. No new Python/npm
  dependency was added; live adapters, TLS/certifi, request budgets, SSRF checks,
  verification rules and audit contracts are unchanged.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests main.py
node --check src/deepdive_ai/static/dashboard.js
node --check tests/browser_smoke.cjs
git diff --check
```

The browser check uses installed Node 24 and Chromium, without npm packages or
downloads:

```powershell
node tests/browser_smoke.cjs
```

An optional first argument selects an installed Chromium executable. It starts
an isolated localhost server on 8765 and browser debugger on 9337, uses only a
repository-local browser profile, blocks non-loopback page requests and denies
external DNS resolution, then stops its test processes. Chrome must be able to
start its renderer; managed process sandboxes may prevent that. No browser or
TLS sandbox was disabled for this verification. Logs/screenshots/downloads go
under Git-ignored `artifacts/`, including desktop, mobile and contradiction views.
Close other uses of these test ports before running; use a trusted local machine.

## Deliberately deferred

Live Mesh/Brave operation and arbitrary research questions; production accounts,
hosting and public deployment; persistent runs or shared-user history; PDF report
export; independent semantic evaluation and benchmark claims. Existing HTML/PDF
extraction tests remain in the full suite; this dashboard's initial scenarios
use the existing short text fixtures for fast, deterministic inspection.

## Recorded Phase 6 validation

- Complete offline unittest suite: **200 tests passed in 10.123 seconds**, zero
  failures, errors or skips. All 184 previous tests remain unchanged; 16 tests
  were added for the real workflow scenarios, provenance/citations, audit/stream
  output, invalid inputs, origin/Host/client restrictions, concurrency limits and
  cancelled-request cleanup.
- Real headless Chromium: all six scenarios passed, including streamed progress,
  citation navigation, contradiction/prior-round and retry views, empty/error
  states, Markdown download and 1440px desktop / 390px mobile layouts. No browser
  errors or non-loopback page requests were recorded. Screenshots were visually
  inspected. The managed tool process sandbox initially blocked Chrome renderer
  startup; running the isolated test outside that process sandbox succeeded,
  without disabling browser/TLS safeguards or changing application controls.
- Python compilation, both JavaScript syntax checks, TOML/package-data
  declaration checks and whitespace checks passed.
- An optional offline wheel build (`pip wheel --no-deps --no-build-isolation
  --no-index --no-cache-dir`) could not run because `setuptools.build_meta` is
  not installed in this `.venv`. No packages were downloaded. Wheel contents
  therefore remain unverified; the source-based installed environment and
  dashboard were verified operational. The existing build-system declaration
  already requires setuptools/wheel; resolving that build environment is deferred.
- HEAD remains `1ff0e5b`. Twelve intended files are modified/new, nothing staged.
  Generated browser profiles, screenshots, logs, exports and build-check artifacts
  remain under ignored generated locations. Test servers/browser processes were
  stopped. No live Mesh/Brave requests, credential inspection, paid operations,
  Claude Code, commits, pushes or deployment were performed. Stop for review.
