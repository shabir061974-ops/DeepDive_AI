# Phase 7A: isolated Mesh test readiness

Historical readiness record. The [Phase 7B acceptance correction](mesh_validation_investigation.md#acceptance-correction-from-8006fa9)
supersedes the exact-OK rule and unknown-usage acceptance described below.
Current operator policy is in [controlled connectivity](controlled_connectivity.md).

Starting HEAD `bfa152d` was verified with a clean working tree. This review uses
local source, tests and the earlier public-documentation audit; provider pages
were not rechecked in this phase. No credentials were inspected and no live
Mesh, Brave, catalog, account or other network requests were made.

## Findings and smallest change

`MeshAPIClient.complete(MeshRequest(...))` already supports a standalone request
through the exclusive application LLM interface. It has no Brave dependency.
The harness previously offered Brave-only or Brave-first/both execution, so
Brave configuration/failure blocked its Mesh test. The only runtime change is
a mutually exclusive `--mesh-only` selection in `connectivity.py`.

Mesh-only mode never reads `BRAVE_SEARCH_API_KEY`, builds Brave settings/client/
transport, or invokes discovery. Both success and failure leave Brave `not_run`
with zero attempts. Default two-provider and Brave-only behavior are preserved.
No changes were made to agents, providers, HTTP transport or dashboard behavior.

| Item | Inspected implementation |
| --- | --- |
| Endpoint | POST `https://api.meshapi.ai/v1/chat/completions`, pinned approved origin |
| Authentication | Bearer `MESH_API_KEY` inherited from the invoking PowerShell process; no `.env` loader |
| Model | Fixed harness candidate `google/gemini-2.5-flash`; no catalog lookup or fallback client |
| Request | One user message `Reply exactly OK.`, `max_tokens: 64`, `stream: false` |
| Parser | HTTP 200, JSON content type, unique JSON keys, nonempty completion ID, exactly one assistant text choice, `finish_reason: stop`, no tools |
| Smoke-test compatibility | Exact returned model, trimmed reply `OK`, optional expected version match, valid reported completion usage no greater than 64 |
| TLS | Shared transport uses `ssl.create_default_context(cafile=certifi.where())`, certificate checking and hostname checking enabled; original host used for SNI |
| Local policy | One client attempt, zero retries, concurrency one, request body 2,048 bytes maximum, response 16,384 bytes maximum |
| Deadline | 15 seconds for the complete Mesh exchange; unchanged 30-second outer awaited deadline |
| Diagnostics | Fixed failure category/stage labels, validated HTTP status, attempts, bounded usage/routing metadata and estimate; no bodies, headers, completion IDs, credentials or raw exceptions |

The 15-second exchange bound covers worker scheduling, DNS, TCP, TLS, request
sending and response reading together. It is not a fresh timeout per stage.
OS DNS/worker cleanup may outlive cancellation; a timeout cannot cancel provider
billing. Existing SSRF, DNS pinning, TLS, framing/size checks and redirect
rejection remain intact. Do not use the general-purpose client's defaults for
this controlled test: they allow more requests/retries than the harness policy.

## Documented requirements versus unverified assumptions

The [earlier documentation audit](provider_readiness.md#phase-5b-blocker-review-2026-10-16)
records provider documentation supporting the HTTPS endpoint, inference bearer
key, model/messages envelope and `choices[0].message.content`. It also records
optional versioning and gateway retry/fallback behavior. Those are previously
documented requirements, not newly verified authentication or runtime results.

Exact candidate availability, account-specific input/output prices, balance,
key permissions, full response shape and `max_tokens`/thinking-token semantics
remain unverified. The strict parser may reject a real provider variant; no
speculative relaxation or alternative payload was introduced. Missing/invalid
usage yields unknown cost, never an assumed zero. A missing/mismatched served
model, truncated response or malformed completion fails closed. Current Mesh
parse failures use the existing coarse `http_protocol_failure` category.

An expected `--mesh-version` is optional metadata validation, not a required
authentication secret or sent version pin. Missing routing headers are explicit
uncertainties; multiple reported gateway attempts are diagnostics. No local
client budget can guarantee one upstream attempt, no fallback, provider latency
or a hard dollar ceiling. A compatibility pass is not verified spending.

Before any independently authorized live test, privately approve Mesh account
rates and spend exposure/controls, including final-request overrun and possible
thinking/fallback charges. Unknown/unacceptable exposure remains a blocker.
Local preflight checks configuration syntax and policy acknowledgements only;
it cannot establish authenticated provider readiness. Do not print a key, put
it in command arguments, or enter it at a price prompt.

## Operator commands and stop conditions

Use the [exact PowerShell preflight and future live commands](controlled_connectivity.md#mesh-only-test-preparation)
in the existing terminal holding `MESH_API_KEY`. Preflight performs zero requests.
The proposed `--execute` command must wait for SPA review and separate explicit
live-test/spend approval. It does not require Brave credentials or connectivity.

After an approved single invocation, stop on failure, unexpected model/version,
timeout, missing usage or uncertain routing/billing. Review the sanitized report
before considering any further authorization; no automatic reruns. The test
does not run research agents or change the dashboard's offline fixture labeling.

## Offline verification

Eleven new tests cover isolated success, exact payload and limits, preflight
without transport construction, missing Mesh configuration/spending gates,
HTTP/transport/size failures without retry, preserved timeout stage, malformed
or incompatible completions, unknown usage/routing, safe output, CLI exit codes
and mutually exclusive provider selection. Fake transports and fake credentials
are injected; DNS/connection guards prevent live access. Existing adapter and
TLS regression tests remain unchanged.

Run the complete suite, compilation and whitespace checks before SPA review:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests main.py
git diff --check
```

Validation on 2026-10-16: **211 tests passed in 9.197 seconds**, zero failures,
errors or skips (200 existing tests and 11 new). Compilation of `src`, `tests`
and `main.py`, `git diff --check` and explicit whitespace checks for both new
files passed. All seven PowerShell blocks in the controlled-connectivity
runbook parsed without execution. HEAD remains `bfa152d`; changes are unstaged.
No live request, credential inspection, commit, push or deployment occurred.
Stop for SPA review before independently authorizing execution.
