# Phase 8: publication and CI readiness

Started from verified clean HEAD `4f415ac` on `master`, Python 3.12.10.
Developed by Chabir Ahamad Abusali. No repository creation, commit, push,
deployment, live Mesh/Brave requests, paid operations or credential inspection
was performed. Public documentation, the official workflow and free Python
installation packages were fetched; the protected hackathon checker was not.

## Repository and installation audit

The starting repository had 60 tracked files, no publishable untracked files,
and no `.github` workflows. `pyproject.toml` declares Python >=3.12; the project
is tested here and in the prepared CI with Python 3.12. Core dependencies are
pinned certifi/pypdf; the backend extra declares FastAPI/uvicorn ranges. The
setuptools/wheel build requirements are intentional build-time dependencies.
Node 24 and an installed Chromium browser are needed only for browser checks;
there are no npm dependencies or remote dashboard assets.

Use `python -m venv .venv` (or `py -3.12 -m venv .venv` on Windows), then
`.\.venv\Scripts\python.exe -m pip install -e '.[backend]'`. The complete suite
requires the backend extra: core-only installation is insufficient for the
dashboard tests. README previously described FastAPI tests as only optional;
that guidance is corrected. Do not copy the existing virtual environment into
a new checkout. Dependency installation needs package-index access and may
resolve newer backend versions within the declared ranges.

The new `artifacts/phase8-env` environment successfully installed the backend
extra using the documented package method, with no application-source changes.
It resolved FastAPI 0.143.2 versus 0.143.1 in the original environment, while
uvicorn remained 0.54.0. This is environment verification, not a dependency lock.
Generated environments, caches, wheels, logs and research exports are ignored.

`main.py`/`deepdive_ai.app` prints the greeting. The loopback dashboard starts
with `python -m uvicorn deepdive_ai.api:create_app --factory --host 127.0.0.1
--port 8000 --no-access-log`. Its only run endpoint is fixed-scenario offline
execution. Library composition and connectivity commands are separate;
installation and dashboard startup do not activate providers.

## Public-data review

Tracked files, publishable untracked files and all 127 reachable historical
file blobs were scanned for private-key material, common provider/GitHub/AWS
credential patterns and credential-bearing URLs. Only two authored negative
test fixtures matched credential-URL syntax (`tests/test_adapters.py` and
`tests/test_foundation.py`); they are synthetic unsafe-URL rejection cases, not
account credentials. No suspected real secret or private runtime artifact was
found. No actual environment variables, downloaded key files or ignored browser
profiles were opened. A pattern scan is not a guarantee against every secret
format; review the final staged file list before eventual publication.

Existing ignores cover `.env` variants, `.venv`, Python caches, logs, `reports/`,
`artifacts/`, build output and package metadata. Additional `.secrets/` and
private-key/container extensions are ignored. These are preventive rules, not
evidence that any such files exist. `.env.example` remains allowed but must
contain only non-secret instructions. Offline tests supply authored credential
strings in memory; no real provider or submission secret is required.

Git history was not changed. No project license file is currently present;
the owner should choose licensing terms if redistribution rights are intended.
Public visibility alone does not establish an open-source license. No license,
affiliation or third-party ownership claim was invented.

## Offline GitHub Actions

`.github/workflows/offline-ci.yml` is an independent push/pull-request workflow.
It uses the documented Windows installation because the retained browser
harness uses Windows `.venv/Scripts/python.exe` and Chrome paths and the suite
tests Windows PDF Job Objects. GitHub's [Windows Server 2025 image inventory](https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md)
lists Chrome; CI explicitly fails if its expected executable is absent.

The job uses Python 3.12, Node 24 and a new virtual environment. It installs
`.[backend]`, runs the complete offline unittest suite, compiles source/tests,
checks both JavaScript files, runs all browser scenarios and checks tracked
files remain unchanged. Job permissions are `contents: read`; checkout does
not persist credentials. There is no `pull_request_target`, OIDC grant, secret
reference, provider invocation, publication or deployment. Provider variables
are blank; only fake test credentials exist during offline exercises.
Nonzero native command statuses propagate; combined steps explicitly preserve
earlier failures. A missing browser is a failure, not a skipped passing check.

The action usage follows the official [checkout](https://github.com/actions/checkout),
[Python setup](https://github.com/actions/setup-python) and
[Node setup](https://github.com/actions/setup-node) documentation. Major-version
tags and hosted runner images are updateable dependencies, not immutable pins.
GitHub-hosted execution cannot be verified without a future authorized push.
No GitHub run, status badge or branch-protection setting is claimed here.

Browser verification found a genuine test issue: a prior Markdown file could
satisfy the download assertion. The harness now removes only its known ignored
export before requesting a new one. Existing browser assertions verify the new
download, all six offline scenarios, exact attribution, mobile layout and no
external page requests. No application behavior or provider safety rule changed.

## Official hackathon workflow: preserved, not activated

The exact [official workflow](https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml)
was safely retrieved over verified HTTPS. Its 2,658 original UTF-8 bytes are
preserved as `docs/agent-arena-workflow.yml.template`, outside `.github/workflows`.
A narrowly scoped `-text` Git attribute preserves its bytes across checkouts.
SHA-256: `f34c4aba59caa82bd992a3fb77b51c0c94d3d2c3551acb55f72ddaf4e6e91436`.

The official `Agent Arena Checks` workflow:

- Runs on push on Ubuntu, with a distinct concurrency group.
- Requests `id-token: write` and `contents: read`.
- Sets its own Python 3.11 for a downloaded standard-library checker; this is
  separate from DeepDive's Python 3.12 runtime and need not be rewritten.
- Uses the exact `agent-arena-2026` identifier and OIDC audience supplied by
  the provider; do not substitute an invented audience or environment URL.
- Uses `SUBMISSION_TOKEN`, OIDC and `MESH_API_KEY`, downloads protected
  `checks.py`, and invokes it with a reporting webhook configuration.

No protected checker was downloaded and no secret was read. The public template
alone does not establish whether the checker makes billable LLM requests,
transmits repository data or imposes further submission rules. Treat those as
unresolved requirements for activation. Our offline workflow does not satisfy
the official registration/checker requirement by itself, and does not replace
its contents. The two workflows can coexist with their own permissions and
Python versions after separate review. Before enabling the unchanged official
workflow in `.github/workflows`, privately configure the organizer-issued
submission token, review protected-checker behavior and approve potential Mesh
spend. Do not run its script locally or use it in pull-request CI as a workaround.

## Full research integration path and remaining requirements

The actual library path already exists:

`ResearchQuestion -> MeshPlannerAgent -> RetrievalResearcherAgent ->
BraveSearchDiscovery -> HTTPWebRetriever -> bounded HTML/PDF extraction ->
MeshDrafterAgent -> MeshVerifierAgent -> bounded research-again loop ->
VerifiedClaimsSynthesizerAgent -> Report + AuditSink events`.

`build_research_workflow` requires injected Mesh client, model, discovery,
retriever and audit sink. Planner, drafter and verifier call Mesh through
`MeshJSON`; researcher uses real discovery/retrieval interfaces. Synthesis is
deterministic and copies accepted claim text/citations exactly. Only insufficient
evidence or no claims causes bounded follow-up; contradictions downgrade
verification. Source digests/relationships are validated across retries.
Agent-call budgets are shared over the workflow lifetime; underlying HTTP
attempt/redirect budgets must also be configured explicitly. Construct a new
workflow for a separately approved run rather than assuming budgets reset.

Remaining requirements before a controlled full live run:

1. Privately configure `MESH_API_KEY` and `BRAVE_SEARCH_API_KEY`, approved Mesh
   model/rates and per-provider HTTP budgets. No automatic `.env` loading exists.
2. Resolve Brave's operator-reported timeout through separately reviewed work;
   no Brave investigation or new provider probe was performed in Phase 8.
3. Verify live Mesh structured agent-JSON behavior, not just bounded text
   connectivity. The latest operator diagnostic identified the old exact-OK
   smoke-test rule as its only failure. The corrected harness is committed,
   but no complete live workflow or independent semantic evaluation has passed.
4. Prepare and review an explicit live runner/caller that composes the existing
   real adapters and invokes `WorkflowCoordinator.run` with a bounded question,
   run ID, limits and audit sink. There is no live CLI or live dashboard route;
   the current factory is a library entry point, not automatic execution.
5. Choose private audit/report retention and export behavior. An `AuditSink`
   protocol exists, but a production persistent storage adapter is not provided.
   Never substitute the offline demo's scripted fixtures for live providers.
6. Independently authorize external requests and spend exposure. Timeouts/token
   parameters and local attempt limits do not prove a hard USD ceiling, prevent
   all gateway retries or undo provider work after cancellation.

The dashboard stays labeled **OFFLINE FIXTURE DEMO — NO LIVE RESEARCH**.
No new live runner, autonomous action, fabricated evidence or benchmark claim
was introduced. Existing provenance, verification, retries and security remain.

## Verification and final local state

- Original `.venv`: **221 tests passed in 11.401 seconds**, zero failures,
  errors or skips.
- New editable-install environment: **221 tests passed in 10.452 seconds**,
  zero failures, errors or skips.
- New environment after installing the independently built standard wheel:
  **221 tests passed in 9.546 seconds**, zero failures, errors or skips.
  Packaged static assets, fixture JSON and exact attribution were verified
  under `site-packages`, independently of the source package path.
- `node tests/browser_smoke.cjs`: all six scenarios, progress, citation
  navigation, contradictions, retry/empty/error states, a newly created
  Markdown download and desktop/mobile attribution/layout passed; zero browser
  errors or external page requests. Renderer permissions were needed locally;
  no browser/TLS safeguards were disabled. Owned loopback processes were stopped.
- Python compilation and both JavaScript syntax checks passed. YAML parsing,
  offline workflow trigger/permission checks, all six PowerShell run-step syntax
  checks, official-template byte identity, fourteen ignore probes and tracked/
  new-file whitespace checks passed. PyYAML was used only from an ignored
  temporary wheel for validation and is not a new project dependency.

HEAD remains `4f415ac` on `master`. Four tracked files are modified:
`.gitattributes`, `.gitignore`, `README.md`, `tests/browser_smoke.cjs`.
Three files are new: `.github/workflows/offline-ci.yml`,
`docs/agent-arena-workflow.yml.template`, `docs/publication_readiness.md`.
Nothing is staged or committed. The working tree is intentionally not clean
because it contains this reviewable preparation; generated data remains ignored.

The local offline preparation has no remaining implementation blocker. Hosted
CI and official checker activation remain unverified until separately authorized
publication/configuration. Full live research remains blocked by the requirements
above. Stop for SPA review; do not commit, push, enable the official checker,
deploy or execute providers during this phase.
