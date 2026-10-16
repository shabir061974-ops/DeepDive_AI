# Phase 5B controlled connectivity preparation

Originally prepared from `6e44a0e`; blocker review from clean checkpoint
`af7926d` on 2026-10-16. A subsequent authorized operator-run Brave attempt
failed with no complete transport response; Mesh was not attempted. See the
[offline investigation from `ed37933`](brave_failure_investigation.md).
No additional live requests were made during that investigation.
This harness does not run agents, fetch search results,
activate services, or authenticate during preflight. The harness originally
added no dependencies. The subsequent secure CA-bundle update from `6c8e416`
pins `certifi==2026.7.22` and passes its bundle to the shared HTTPS transport's
verified SSL context. Certificate/hostname verification, SNI, DNS/IP safeguards,
budgets, retries, timeouts and spending controls remain in force. That integration
was verified offline only and does not authorize another live request.

## Bounds and stop conditions

`python -m deepdive_ai.connectivity` defaults to **offline preflight**.
Only `--execute` opts in to external requests. Each invocation permits at most
one Brave GET and one Mesh POST, sequentially, with zero automatic retries.
Brave runs first; any failure prevents Mesh from being contacted. Do not rerun
live mode after a failure without reviewing the outcome and authorizing another
potential charge. Limits apply per invocation, not across separate processes.

- Mesh model: fixed `google/gemini-2.5-flash`; prompt: `Reply exactly OK.`
- Mesh timeout: 15 seconds; requested output limit: 64 tokens.
- Brave query: `Python documentation`, one result, `en/US`, web-only; timeout:
  10 seconds. No candidate URLs are opened.
- Both policies: one lifetime attempt, concurrency one, request-body limit 2,048
  bytes, response limit 16,384 bytes; existing SSRF, DNS pinning, TLS, identity
  encoding and redirect rejection remain unchanged.
- Overall awaited execution deadline: 30 seconds. OS DNS resolution can outlive
  cancellation in a worker thread; timeout does not prove provider work stopped.

The budget means one **client HTTP request per provider**, not one Mesh upstream
attempt. The old `--confirm-routing` flag is removed: it could not enforce or
verify that assertion. `--accept-gateway-uncertainty` instead acknowledges that
gateway attempts, fallback, processing time and total charges are outside local
control. `--confirm-spend-controls` confirms review of account prices, approved
spend exposure and provider spending controls, including final-request overrun
risk. Both are required, along with prices. Neither changes provider settings.
Do not set them without that review. If exactly one upstream attempt or a hard
transactional dollar ceiling is required, do not run Mesh with this interface.

`--mesh-version` is optional expected-response validation, not authentication or
a request pin. If supplied, missing/mismatched response version still fails. It
does not change the key or add a version header. Without it, version is unchecked.
The served model must still match the fixed candidate, with a complete text
result `OK`. Routing attempt/fallback headers are parsed into bounded numbers,
booleans and fixed warnings; absent or invalid values stay unknown. Reported
multiple attempts or same-model fallback are diagnostic, not proof that our
client retried. A passing result establishes only this connectivity/response
subset, not a one-upstream-attempt, billing, routing or production guarantee.

Any non-200 response, redirect, timeout, transport/security failure, malformed or
oversized response, unexpected completion/model or explicitly checked version
stops the run. There are no alternative endpoints, catalogs, balance queries,
provider fallbacks or automatic resubmissions.

## Cost and safe output

Supply account-approved prices, not assumed free credits or another provider's
rates: Mesh USD per million input and output tokens, and Brave USD per request.
Zero is accepted only when the account terms actually justify it. These arguments
are non-secret billing inputs. No numeric model price is claimed by this harness.

Output contains only fixed status/blocker labels, HTTP status codes, client attempt
counts, compatibility booleans, bounded gateway diagnostics/fixed warnings,
validated token counts when available, and cost estimates. It never outputs
credentials, raw headers, provider error bodies,
completion text, source URLs, query results, IDs or exception messages. Nothing is
written to a report file automatically. Do not enable HTTP tracing or dump objects.
Failures now include a fixed `failure_category` and any status already received
before a rejected body. A null status still does not prove the server was never
contacted; successful/unattempted checks have a null failure category.

Brave's estimate is the supplied nominal per-request rate on a successful parsed
response. Mesh estimates `(prompt_tokens * input_rate + completion_tokens *
output_rate) / 1_000_000` only when compatibility and usage checks pass. Missing
or invalid usage yields null counts/cost, never an assumed zero. Model mismatch
or request failures yield unknown cost and may still be billable. Estimates
exclude unconfirmed credits, taxes, cached-token discounts and other billable
overhead; reconcile against account billing. The cost status is explicitly
`response_usage_estimate_not_total_bill`. Rates and token caps do not enforce
a currency ceiling. Approved spend exposure and appropriate key caps remain
required; the cap can be exceeded by a final request. Review balance,
auto-recharge and any BYOK exposure privately; do not assume a small balance
alone guarantees a hard ceiling. If unacceptable exposure cannot be excluded,
stop before execution. The harness does not calculate or enforce a pre-call USD
ceiling, and a timeout does not imply zero cost.

## Provider requirements versus local policies

Public sources and pricing findings are in the [blocker review](provider_readiness.md).
HTTPS and the inference bearer credential authenticate Mesh; Brave uses its
subscription-token header. Neither provider requires our confirmation flags.
Those are internal safety attestations and cannot prove account state.

| Safeguard | Enforceable locally | Boundary |
| --- | --- | --- |
| Client count/retries | One call each, zero retries per invocation | Separate invocations reset budgets; gateway attempts are separate |
| Timeouts | 10s Brave, 15s Mesh, 30s overall awaited work | Cannot terminate upstream billing; OS DNS thread may outlive cancellation |
| Tokens/bytes | Send 64 output-token limit; 2,048/16,384 byte caps | Provider token/thinking enforcement is not locally guaranteed |
| Security/output | Existing SSRF/TLS/redirect checks and allowlisted output | Never output bodies, headers, exceptions or credentials |
| Compatibility | Strict JSON, exact served model/text; optional version | Missing routing headers produce uncertainty, not invented verification |
| Spending | Require approved rates and explicit controls acknowledgement | No local hard USD cap or authenticated account audit |

The 64-token cap is checked against valid reported completion usage when present.
It is not a tokenizer, a thinking-token guarantee or a billable-token ceiling.
Google's published direct rates must not substitute for unverified Mesh rates.

Exit codes: `0` = ready preflight or both live checks passed; `1` = failed or
inconclusive execution/configuration; `2` = preflight blocked or invalid CLI
arguments; `130` = interrupted. `ready` refers to local preflight only, not
credential authentication or provider readiness.

## PowerShell commands

Run these from the **existing PowerShell terminal holding both credentials**.
The child `.venv` Python process inherits that terminal's environment. No `.env`
loader, persistent Windows variables, credential command arguments, or new
terminal is needed. The agent's tool process need not share that environment.

After privately confirming Mesh-specific rates, accepted spend exposure and
provider spending controls, prepare the arguments and run preflight. No version
or assertion of a single gateway attempt is required:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'

$smokeArgs = @(
    '--accept-gateway-uncertainty'
    '--confirm-spend-controls'
    '--mesh-input-usd-per-million', (Read-Host 'Approved Mesh input USD per million tokens')
    '--mesh-output-usd-per-million', (Read-Host 'Approved Mesh output USD per million tokens')
    '--brave-usd-per-request', (Read-Host 'Approved Brave USD per request')
)

.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @smokeArgs
if ($LASTEXITCODE -ne 0) { throw 'Preflight blocked; do not execute live requests.' }
```

Preflight prints only sanitized status and validates configuration without
contacting providers. It cannot verify credentials, pricing or dashboard settings.
Do not enter API keys at any of these non-secret prompts.

For optional strict version matching, append
`$smokeArgs += @('--mesh-version', (Read-Host 'Expected Mesh version (YYYY-MM)'))`
before preflight. This compares the response; it does not select or authenticate
a version.

**Only after SPA review and explicit live-test/spend approval**, run once in that
same terminal with the prepared arguments:

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @smokeArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Connectivity test failed or inconclusive; do not retry.' }
```

Do not run that last command during preparation. Review the sanitized result
before any subsequent action. No complete research workflow is started.

## Offline verification

Tests use injected responses, explicit offline credentials and DNS/connection
guards. Coverage includes zero-request preflight, missing credentials/policy/
pricing/spending gates, successful compatibility and cost calculations, request bounds,
redirects, authentication/payment/rate-limit/server failures without retry,
malformed/unsafe discovery, optional version checks, diagnostic-only gateway
metadata, missing/mismatched served models, unavailable or
invalid usage, unexpected text, output-cap violations, cancellation deadlines,
redacted exceptions and bounded numeric configuration.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests main.py
git diff --check
```

Original preparation validation on 2026-10-16: 149 tests passed in 6.864 seconds, zero
failures/errors/skips (136 existing and 13 new). Compilation and whitespace
checks passed. The PowerShell command blocks were parsed successfully without
execution. HEAD remains `6e44a0e`; README plus this document, the harness and its
tests are the only changes, all unstaged. Await SPA review; no live calls made.

Blocker-review validation on 2026-10-16 from `af7926d`: 154 tests passed in
6.810 seconds, zero failures/errors/skips. Five regression tests were added and
the existing harness tests migrated to the documented policy change. Compilation,
PowerShell command parsing (without execution), and `git diff --check` passed.
Only README, the two provider/test documents, the connectivity harness and its
tests are modified; nothing is staged. No credentials were accessed and no live
provider API requests were made. Await SPA review; do not execute yet.
