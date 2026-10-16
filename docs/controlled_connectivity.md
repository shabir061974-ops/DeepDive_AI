# Phase 5B controlled connectivity preparation

Prepared offline from clean checkpoint `6e44a0e` on 2026-10-16. No live request
has been executed. This harness does not run agents, fetch search results,
activate services, or authenticate during preflight. It adds no dependencies
and changes no existing adapter or workflow behavior.

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

Before execution, verify the Mesh inference key's gateway policy has one upstream
attempt, zero retries, fallback disabled and at most 10 seconds gateway latency.
`--confirm-routing` attests this was checked; it does not configure the provider.
Client limits alone cannot prevent internal gateway retries. If the policy
cannot be verified, do not use this flag or execute the test.

The effective approved key version is provided with `--mesh-version`; the harness
checks the response against it but does not change the key or add a version header.
It also requires the response model to match the fixed candidate, routing attempts
to be exactly `1`, fallback to be `false`, and a complete text result `OK`.
Missing or unexpected metadata is an inconclusive failed test, never a reason
to retry. This intentionally strict subset may reject a usable provider response.
Model availability and wire compatibility are not established until such a
reviewed test succeeds.

Any non-200 response, redirect, timeout, transport/security failure, malformed or
oversized response, unexpected completion/model/version, or unconfirmed routing
stops the run. There are no alternative endpoints, catalogs, balance queries,
provider fallbacks or automatic resubmissions.

## Cost and safe output

Supply account-approved prices, not assumed free credits or another provider's
rates: Mesh USD per million input and output tokens, and Brave USD per request.
Zero is accepted only when the account terms actually justify it. These arguments
are non-secret billing inputs. No numeric model price is claimed by this harness.

Output contains only fixed status/blocker labels, HTTP status codes, client attempt
counts, compatibility booleans, validated token counts when available, and cost
estimates. It never outputs credentials, raw headers, provider error bodies,
completion text, source URLs, query results, IDs or exception messages. Nothing is
written to a report file automatically. Do not enable HTTP tracing or dump objects.

Brave's estimate is the supplied nominal per-request rate on a successful parsed
response. Mesh estimates `(prompt_tokens * input_rate + completion_tokens *
output_rate) / 1_000_000` only when compatibility and usage checks pass. Missing
or invalid usage yields null counts/cost, never an assumed zero. Model mismatch
or request failures yield unknown cost and may still be billable. Estimates
exclude unconfirmed credits, taxes, cached-token discounts and other billable
overhead; reconcile against account billing. Rates and token caps do not enforce
a currency ceiling. Retain the Phase 5A requirement for an approved spend ceiling
and appropriate key caps before any execution.

Exit codes: `0` = ready preflight or both live checks passed; `1` = failed or
inconclusive execution/configuration; `2` = preflight blocked or invalid CLI
arguments; `130` = interrupted. `ready` refers to local preflight only, not
credential authentication or provider readiness.

## PowerShell commands

Run these from the **existing PowerShell terminal holding both credentials**.
The child `.venv` Python process inherits that terminal's environment. No `.env`
loader, persistent Windows variables, credential command arguments, or new
terminal is needed. The agent's tool process need not share that environment.

After privately checking account rates, effective Mesh version, the approved
spend ceiling and key routing policy, prepare the arguments and run preflight:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'

$smokeArgs = @(
    '--confirm-routing'
    '--mesh-version', (Read-Host 'Approved effective Mesh key version (YYYY-MM)')
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
pricing gates, successful compatibility and cost calculations, request bounds,
redirects, authentication/payment/rate-limit/server failures without retry,
malformed/unsafe discovery, missing or mismatched Mesh metadata, unavailable or
invalid usage, unexpected text, output-cap violations, cancellation deadlines,
redacted exceptions and bounded numeric configuration.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests main.py
git diff --check
```

Preparation validation on 2026-10-16: 149 tests passed in 6.864 seconds, zero
failures/errors/skips (136 existing and 13 new). Compilation and whitespace
checks passed. The PowerShell command blocks were parsed successfully without
execution. HEAD remains `6e44a0e`; README plus this document, the harness and its
tests are the only changes, all unstaged. Await SPA review; no live calls made.
