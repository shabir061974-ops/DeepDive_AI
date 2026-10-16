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

The operator's later Brave-only attempt timed out with null HTTP status and zero
Mesh attempts. The [investigation from `52bd82b`](timeout_and_demo_continuity.md)
adds `failure_stage` using fixed stage labels and per-request progress snapshots.
The 10 seconds cover worker scheduling, DNS, TCP, TLS, request sending and all
response reading together; it is not a separate allowance for each stage. No
timeouts or retry counts were changed and no further live probe was performed.

## Bounds and stop conditions

`python -m deepdive_ai.connectivity` defaults to **offline preflight**.
Only `--execute` opts in to external requests. In the default two-provider mode, each invocation permits at most
one Brave GET and one Mesh POST, sequentially, with zero automatic retries.
Brave runs first; any failure prevents Mesh from being contacted. Do not rerun
live mode after a failure without reviewing the outcome and authorizing another
potential charge. Limits apply per invocation, not across separate processes.

The Brave-only retest preparation from clean checkpoint `626b394` adds
`--brave-only`. In that mode both Brave success and failure leave Mesh `not_run`
with zero attempts. Mesh credentials are not read, and Mesh settings, client and
transport are not constructed. Only the Brave price and existing spend-control
confirmation are required; Mesh-specific price/version/gateway inputs do not
apply. The default two-provider path retains its existing gates and behavior.

Phase 7A preparation from `bfa152d` adds mutually exclusive `--mesh-only`.
It skips Brave credentials, settings, prices, transport and discovery on success
and failure. Brave stays `not_run` with zero attempts. Mesh credentials, both
Mesh rates and the existing gateway-risk/spend-control acknowledgements remain
required. See the [Mesh readiness findings](mesh_readiness.md). Neither isolated
mode changes the default two-provider behavior or authorizes live execution.

- Mesh model: fixed `google/gemini-2.5-flash`; prompt: `Reply exactly OK.`
- Mesh timeout: 15 seconds; requested output limit: 64 tokens.
- Brave query: `Python documentation`, one result, `en/US`, web-only; timeout:
  10 seconds. No candidate URLs are opened.
- Both policies: one lifetime attempt, concurrency one, request-body limit 2,048
  bytes, response limit 16,384 bytes; existing SSRF, DNS pinning, TLS, identity
  encoding and redirect rejection remain unchanged.
- Overall awaited execution deadline: 30 seconds. OS DNS resolution can outlive
  cancellation in a worker thread; timeout does not prove provider work stopped.

Whenever Mesh is selected the budget means one **client HTTP request per provider**, not one Mesh upstream
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
The served model must still match the fixed candidate, with a complete, nonempty
assistant text result. Exact `OK` is diagnostic only; a non-OK reply can pass.
Valid reported usage must confirm completion tokens <= 64; unavailable/invalid
usage fails because the output cap cannot be confirmed. Routing attempt/fallback headers are parsed into bounded numbers,
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
Phase 7B adds `diagnostics.validation_checks` after successful schema parsing:
fixed boolean/null results for `response_schema_compatible`, `model_matches`,
`expected_version_matches` and `output_limit_satisfied`.
The acceptance correction from `8006fa9` moves `reply_matches` to a standalone
diagnostic at `diagnostics.reply_matches`; false does not fail connectivity.
`diagnostics.failed_checks` names false required checks and an unknown output cap.
Null means unchecked/unknown, not passed (optional version or unavailable usage).
Every check is evaluated even after a mismatch; no compared values are emitted.
Schema failures retain the existing error category and do not claim schema success.
See the [HTTP 200 investigation](mesh_validation_investigation.md).
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
| Compatibility | Strict JSON, nonempty assistant text, exact served model, confirmed output cap; optional version | Does not assess instruction-following, factual accuracy or agent contracts |
| Spending | Require approved rates and explicit controls acknowledgement | No local hard USD cap or authenticated account audit |

The 64-token cap must be confirmed against valid reported completion usage.
It is not a tokenizer, a thinking-token guarantee or a billable-token ceiling.
Google's published direct rates must not substitute for unverified Mesh rates.

Exit codes: `0` = ready preflight or all selected live checks passed; `1` = failed or
inconclusive execution/configuration; `2` = preflight blocked or invalid CLI
arguments; `130` = interrupted. `ready` refers to local preflight only, not
credential authentication or provider readiness.

## PowerShell commands

### Mesh-only test preparation

Use the existing PowerShell terminal holding `MESH_API_KEY`. Brave credentials
are unnecessary. Supply only privately reviewed, account-approved Mesh rates;
these prompts are for non-secret prices, never API keys. A protected private
credential file is now supported as a fallback; see [one-time setup](private_configuration.md).

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
$meshTestArgs = @(
    '--mesh-only'
    '--accept-gateway-uncertainty'
    '--confirm-spend-controls'
    '--mesh-input-usd-per-million', (Read-Host 'Approved Mesh input USD per million tokens')
    '--mesh-output-usd-per-million', (Read-Host 'Approved Mesh output USD per million tokens')
)
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @meshTestArgs
if ($LASTEXITCODE -ne 0) { throw 'Mesh preflight blocked; do not execute.' }
```

This preflight is offline and cannot authenticate the key or confirm prices.
Do not attest gateway risk or spending review until those reviews are complete.
Do not substitute Google's direct prices for unverified Mesh account prices.

**Proposed live command, only after SPA review and independent live-test/spend
authorization; not executed during preparation:**

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @meshTestArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Mesh test failed or inconclusive; do not retry.' }
```

Run at most once after approval. This selects only one Mesh POST, zero retries,
the existing 15-second attempt timeout, 30-second outer awaited deadline,
64 requested output tokens and 16,384-byte response cap. There is no Brave
request, source fetching or five-agent workflow. Stop for review on failure,
missing usage, unexpected routing diagnostics or any other inconclusive result;
never automatically resubmit. Unknown routing can accompany exit code 0;
unknown usage now fails. A pass means response compatibility, not verified
billing, gateway behavior, exact instruction-following or factual accuracy.
If account pricing/spend exposure cannot be approved, do not execute.

### Brave-only retest preparation

Use the existing PowerShell terminal holding `BRAVE_SEARCH_API_KEY`. Python
inherits that environment without printing the key; no Mesh credential is
needed or inspected. After reviewing approved Brave account pricing and spending
controls, prepare the arguments and run the **offline preflight**:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
$braveRetestArgs = @(
    '--brave-only'
    '--confirm-spend-controls'
    '--brave-usd-per-request', (Read-Host 'Approved Brave USD per request')
)
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @braveRetestArgs
if ($LASTEXITCODE -ne 0) { throw 'Brave preflight blocked; do not execute.' }
```

**Proposed live command, only after SPA review and explicit retest/spend approval:**

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @braveRetestArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Brave retest failed or inconclusive; do not retry.' }
```

Run live mode at most once after approval. It issues at most one Brave GET, with
zero retries, a 10-second attempt timeout, 16,384-byte response cap and unchanged
30-second overall awaited deadline. Query/result count, certifi CA bundle,
certificate/hostname checking, SNI, SSRF/redirect protections, cost handling and
sanitized failure categories are unchanged. No result URLs are fetched and no
agents run. Requests may still be billable on failure; there is no local hard
dollar ceiling. This preparation does not execute or authorize the command.

### Default two-provider test

Run these from the **existing PowerShell terminal holding both credentials**.
The child `.venv` Python process inherits that terminal's environment, which
takes precedence over the protected private file. With [one-time setup](private_configuration.md),
a new terminal can use that file without persistent Windows environment
variables or credential command arguments. Tool processes need not share the
original terminal environment.

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

Brave-only preparation from `626b394`: all 176 offline tests passed in 7.832
seconds, with zero failures/errors/skips (168 existing unchanged and eight new).
Compilation, PowerShell command parsing without execution, `git diff --check`
and new-file whitespace checks passed. Only the harness, its new Brave-only
tests, README and this document are changed, all unstaged. No live requests,
real credential inspection, commits, pushes or deployments were performed.
Stop for SPA review before execution.
