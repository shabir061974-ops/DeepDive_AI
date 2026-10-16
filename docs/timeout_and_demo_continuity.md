# Brave timeout investigation and demo continuity

Starting checkpoint `52bd82b` was verified clean. The operator reported a new
Brave-only attempt: one client attempt, `timeout`, null HTTP status, incompatible
response, unknown cost and zero Mesh attempts. The previous certifi TLS handshake
was a separate successful diagnostic. Neither was repeated here. No credentials
were inspected, network requests made or paid operations performed.

## Exact request and deadline path

`connectivity --brave-only` constructs Brave settings with `_policy(10)` and
calls `BraveSearchDiscovery.discover(SearchRequest("Python documentation", 1))`.
The GET targets the pinned Brave HTTPS web-search endpoint, with `en/US`,
`spellcheck=false`, `result_filter=web` and identity encoding. There are no source
fetches, redirect following or Mesh calls in this mode.

The bounded client wraps the entire transport call in a 10-second `wait_for`.
The public transport establishes another 10-second deadline before scheduling
its blocking worker. These are overlapping bounds, not extra time allowances.

1. Worker scheduling and URL validation happen before DNS resolution.
2. All resolved addresses must pass public-IP checks. The first validated
   address is used; there is no second-address fallback in this attempt.
3. TCP connects to that pinned IP. TLS uses certifi, certificate verification,
   hostname checking and the original hostname for SNI.
4. Request transmission, status/header parsing and bounded body reads follow.
5. DNS elapsed time is deducted before creating the connection. Socket timeouts
   are refreshed to the remaining total budget before sending/receiving/reading;
   TCP and TLS share the connection's remaining budget plus the deadline abort
   timer. They do not each receive a fresh 10 seconds.

The 30-second outer workflow-test deadline remains unchanged. It does not grant
a Brave request more time. Size/header/framing/encoding checks are also unchanged.
OS DNS work can outlive async cancellation; late resolution is checked against
the deadline before any connection. Event-loop/executor cleanup can outlast the
awaited timeout, so 10 seconds is not a guaranteed process-exit or billing bound.

## What can and cannot be concluded

| Hypothesis | Why it remains possible |
| --- | --- |
| Executor scheduling or DNS delay | Both consume the same attempt deadline |
| TCP path/address issue | Only one public address is tried; the successful handshake may have used another address/time |
| TLS delay | A previous successful handshake does not guarantee the next handshake's latency |
| Slow request send or response headers | A complete verified handshake does not establish HTTP/API responsiveness |
| Body-read stall | Earlier async cancellation could lose a worker's known status before it returned |
| Combined stage latency | Several individually successful stages can exhaust the total budget |

There is no evidence to rank these as a proven root cause. Null HTTP status does
not establish invalid credentials, absence of provider work, or a free failure.
Ordinary application JSON parsing would follow an accepted full HTTP response
and usually retain its status. No timeout or retry increase is justified by the
reported data alone. Certificate verification must stay enabled.

## Smallest secure remedy implemented

Add diagnostics, not a wider network budget. A per-request, thread-safe progress
record holds only fixed stage enums and a validated HTTP status. The transport
marks `worker_queue`, `dns`, `tcp_connect`, `tls_handshake`, `send_request`,
`response_headers` and `response_body`. It contains no hostnames, URLs, headers,
credentials, bodies or raw errors.

`failure_stage` is added beside `failure_category` in the sanitized harness
report. Typed error propagation retains it through Brave and the bounded client.
The progress snapshot also survives an outer attempt timeout cancelling an
unfinished transport; a status already observed can be retained without waiting
for the worker. DNS socket timeouts are now categorized as `timeout` at `dns`.
Uninstrumented injected transports report an unknown stage, not a guessed one.
External task cancellation still propagates as cancellation.

Stages are the last observed operation at the deadline, not independently timed
measurements or proof of the underlying provider/OS cause. No host/IP details or
request timing distributions are published. Each attempt has its own record,
so concurrent requests do not share diagnostics. All budgets, retry counts,
spending gates, certifi/TLS checks, SSRF rules and fail-closed verification remain
unchanged. The next network action still needs separate SPA/live approval.

## Existing offline demonstration capability

`tests/test_agents.py` already constructs the real five concrete agents through
`build_research_workflow`, injecting `FakeMesh`, `FakeDiscovery`, controlled
`FixtureTransport`, a fixed clock and an in-memory audit sink. These are useful
demonstrations of orchestration and safeguards, not live research or independent
model evaluations:

- `test_five_concrete_agents_end_to_end` exercises planning, retrieval, drafting,
  verification and synthesis, asserting exact accepted text, resolved citation
  URLs and a completed audit event.
- `test_unsupported_claims_are_retained_only_in_audit_ledger` excludes unsupported
  claims from the accepted summary while retaining the report's claim ledger.
- `test_contradiction_retry_exhaustion_never_emits_accepted_claim` supplies support
  plus counter-evidence and checks that no claim is accepted after bounded retry.
- `test_retry_can_resolve_evidence_gap_without_losing_provenance` exercises a
  successful bounded retry while retaining source timestamps/provenance.
- Other existing tests cover missing evidence, invented quotes/citations,
  synthesis tampering, API failures, audit failures and request budgets.

`tests/test_discovery_extraction.py` additionally exercises mocked discovery and
authored local HTML/PDF extraction with hashes, transformed spans and provenance.
Fixture sources use `.invalid` URLs and authored text. Fixture timestamps and
scripted verifier assessments must be described as fixtures, never as live
retrieval timestamps or demonstrated semantic accuracy.

These demonstrations are currently executable **tests**, not an existing
dashboard or demo API. The FastAPI application currently exposes only health.
This change does not build a UI, expose new endpoints or start a service.

Offline demonstration commands, with no credentials or external services needed:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_discovery_extraction.py -v
```

## Fastest safe dashboard path after review

1. Keep live research explicitly unavailable while Brave connectivity is blocked.
   Adopt an explicit **Offline fixture demo - no live research** mode with no
   automatic fallback from a failed live run.
2. Package the existing authored scenarios behind dedicated injected fixture
   adapters, outside the test modules, after approval. Run the actual coordinator
   and derive view data from its `Report`, evidence and `AuditEvent` records.
   Do not hardcode final dashboard answers or mark provider calls successful.
3. Build a narrow read-only dashboard: scenario selector, plan, evidence excerpts
   and provenance, claim-to-evidence citations, supported/unsupported/insufficient
   states, accepted report, and ordered audit timeline. Keep rejected claims
   visible outside the accepted narrative. Add persistent labels for authored
   evidence and scripted verification; fixture citations should resolve to local
   fixture details, not pretend to be browsable research sources.
4. Show genuine computed scenario counts only, not invented precision, accuracy,
   benchmark scores, live citations or latency claims. The verifier is consuming
   scripted assessments; mechanical validation is demonstrated, not model quality.
5. Investigate connectivity separately using the reviewed stage diagnostics and
   existing local DNS/firewall/proxy/TLS logs. Authorize any subsequent Brave-only
   or Mesh-only probe separately; do not widen timeouts, add retries, change TLS,
   or activate live research merely to keep the demo moving.

This lets dashboard work progress on the existing domain contracts without
waiting for a provider fix. A fixture adapter/demo entrypoint and dashboard API/UI
remain future implementation work requiring SPA approval. No estimate of a
completion date or claim of hackathon compliance is made without its criteria.

## Validation

Full `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` passed:
184 tests in 8.192 seconds, zero failures/errors/skips. All 176 existing tests
remain unchanged; eight new tests cover the fixed stages, shared deadline,
cancelled-attempt status preservation, unknown stages and progress validation.
Existing concrete-agent, contradiction, citation, provenance and audit scenarios
passed as part of this suite. A Windows-specific socket-mocking issue in a new
test was corrected by patching only the transport's socket-module binding,
without changing application behavior.

Compilation of `src`, `tests` and `main.py`, whitespace checks and parsing of the
documented offline PowerShell commands passed. HEAD remains `52bd82b`; seven
intended files are modified/new, nothing staged. No live requests, credentials,
paid operations, timeout increases, retry changes, UI implementation, commits,
pushes or deployments were part of this work. Stop for SPA review.
