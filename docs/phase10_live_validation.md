# Phase 10: staged live validation preparation

Latest operator result: Brave separately passed HTTP 200; the Mesh Planner
probe returned HTTP 200 with `agent_contract_or_runtime_failure`. The exact
condition remains unknown. The probe below now emits fixed `agent_failure_code`
values for typed contract failures; redefine `$meshJsonScript` from this updated
block before any separately authorized next attempt. No new live attempt is
authorized by this update. See [confirmed findings and hypotheses](planner_compatibility.md).

Checkpoint `bd3f9a2` was verified clean before preparation. No live provider,
source website or other external request was made. Application code, security
limits, CI and the offline dashboard are unchanged. Developed by Chabir Ahamad
Abusali. This runbook prepares commands; it does not authorize their execution.

## Decision and execution order

SPA: **APPROVED WITH NOTES for preparation; live execution remains unapproved**.
Use this order, one invocation of each stage only after independent authorization:
Brave-only diagnostic -> Mesh Planner JSON probe -> bounded full research ->
inspect persisted sources/verification -> record a demonstration. Stop on any
failure or inconclusive result; do not automatically rerun or advance.

The previous Brave result did not retain a failing stage, so its root cause
remains unproven. Existing instrumentation can distinguish `worker_queue`, `dns`,
`tcp_connect`, `tls_handshake`, `send_request`, `response_headers`, `response_body`.
These are last-observed operations, not proof of an underlying provider/OS cause.
The shared ten-second budget is not a new allowance for each stage. Missing
status does not imply invalid authentication or zero cost. The earlier TLS
success does not establish subsequent HTTP responsiveness. No new diagnostic
needs a separate unauthenticated network probe: the single Brave request already
uses stage instrumentation, certifi, SNI and certificate/hostname verification.

The real Planner, Drafter and Verifier use `MeshJSON` and strict role schemas.
Offline real-adapter integration tests confirm this wiring into `run_live`,
including malformed JSON, fabricated evidence references/quotes, contradictions,
bounded retries and persistence failures. Mesh text connectivity alone does not
verify these contracts on the service. The isolated probe below tests the real
Planner only; it does not establish Drafter/Verifier live compatibility.

## 1. Brave-only: one request maximum

From the existing PowerShell terminal holding the credentials, privately review
account prices and spending controls before setting acknowledgements. These
prompts accept non-secret prices only; never enter or display API keys.

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
$braveArgs = @(
    '--brave-only'
    '--confirm-spend-controls'
    '--brave-usd-per-request', (Read-Host 'Approved Brave USD per request')
)
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @braveArgs
if ($LASTEXITCODE -ne 0) { throw 'Offline preflight blocked; stop.' }
```

Only after separate authorization for this one diagnostic:

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.connectivity @braveArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Brave failed or inconclusive; stop, do not retry.' }
```

This uses the existing fixed query `Python documentation`, one result, no result
URL fetches, no Mesh access, zero retries, ten seconds per exchange and a
16,384-byte response cap. Record only its sanitized status/category/stage and
HTTP status. A pass proves this exchange worked, not permanent connectivity.

## 2. Mesh real Planner JSON: one request maximum

The existing `--mesh-only` command checks plain text, not the Planner contract.
Use this inline, checkpoint-specific diagnostic to call the existing real
Planner without starting Brave or fetching sources. It reuses the existing
Mesh client, strict parser, policy factory and safe metadata observer. It adds
no runtime feature, fixture path, credential storage or relaxed parser.

Copy this complete block to define the probe in PowerShell memory. Defining it
does not run Python or contact a service:

```powershell
$meshJsonScript = @'
import argparse, asyncio, json
from decimal import Decimal
from deepdive_ai.agents.common import AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.planner import MeshPlannerAgent
from deepdive_ai.connectivity import MODEL, TestOptions, _Observation, _rate
from deepdive_ai.http_transport import PublicHTTPTransport
from deepdive_ai.live import http_failure, policy
from deepdive_ai.mesh import MeshAPIClient, MeshSettings
from deepdive_ai.models import ResearchQuestion

QUESTION = "According to MDN, what does HTTP status 404 indicate?"

async def probe(args):
    approvals = TestOptions(mesh_only=True,
        spending_confirmed=args.confirm_spend_controls,
        gateway_risk_accepted=args.accept_gateway_uncertainty,
        mesh_input_rate=args.mesh_input_usd_per_million,
        mesh_output_rate=args.mesh_output_usd_per_million)
    if (not approvals.spending_confirmed or not approvals.gateway_risk_accepted
            or approvals.mesh_input_rate is None or approvals.mesh_output_rate is None):
        print(json.dumps({"status":"blocked","reason":"approved_prices_and_spend_review_required"}))
        return 2
    settings = MeshSettings.from_environment(policy=policy(15, 1, 32768), max_output_tokens=1024)
    if not args.execute:
        print(json.dumps({"status":"ready","mode":"preflight","attempts":0}))
        return 0
    observed = _Observation(PublicHTTPTransport())
    result = {"status":"failed","agent_json_compatible":False,
              "cost_status":"unknown_failure_may_be_billable"}
    try:
        limits = AgentLimits(max_tasks=1, max_text_chars=160, max_question_chars=160,
            max_prompt_chars=20000, max_response_chars=16000,
            max_model_calls=1, output_tokens=1024)
        planner = MeshPlannerAgent(MeshJSON(MeshAPIClient(settings, observed), MODEL, limits))
        async with asyncio.timeout(30):
            tasks = await planner.plan(ResearchQuestion("mesh-json-probe", QUESTION))
        compatible, usage, routing = observed.mesh_metadata(None)
        compatible = compatible and usage is not None and usage["completion_tokens"] <= 1024
        result.update(agent_json_compatible=compatible, task_count=len(tasks),
                      token_usage=usage, diagnostics=routing)
        if compatible:
            cost = (usage["prompt_tokens"] * approvals.mesh_input_rate
                    + usage["completion_tokens"] * approvals.mesh_output_rate) / Decimal(1000000)
            result.update(status="passed", estimated_cost_usd=format(cost,"f"),
                          cost_status="response_usage_estimate_not_total_bill")
        else:
            result["failure_category"] = "response_validation"
    except Exception as error:
        details = http_failure(error)
        result["failure_category"] = ("agent_contract_failure" if isinstance(error, AgentOutputError) else
            details["category"] if details else
            "timeout" if isinstance(error, TimeoutError) else "agent_contract_or_runtime_failure")
        if isinstance(error, AgentOutputError):
            result["agent_failure_code"] = error.code.value
        if details is not None:
            result["failure_details"] = details
    result.update(attempts=observed.attempts, http_status=observed.status)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-spend-controls", action="store_true")
    parser.add_argument("--accept-gateway-uncertainty", action="store_true")
    parser.add_argument("--mesh-input-usd-per-million", type=_rate)
    parser.add_argument("--mesh-output-usd-per-million", type=_rate)
    args = parser.parse_args()
    try:
        return asyncio.run(probe(args))
    except KeyboardInterrupt:
        print('{"status":"interrupted","cost_status":"unknown_may_be_billable"}')
        return 130
    except Exception:
        print('{"status":"configuration_or_runtime_failure"}')
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
'@
$meshJsonArgs = @(
    '--accept-gateway-uncertainty'
    '--confirm-spend-controls'
    '--mesh-input-usd-per-million', (Read-Host 'Approved Mesh input USD per million tokens')
    '--mesh-output-usd-per-million', (Read-Host 'Approved Mesh output USD per million tokens')
)
$meshJsonScript | .\.venv\Scripts\python.exe - @meshJsonArgs
if ($LASTEXITCODE -ne 0) { throw 'Mesh JSON offline preflight blocked; stop.' }
```

After Brave succeeds and a separate one-request Mesh authorization is granted:

```powershell
$meshJsonScript | .\.venv\Scripts\python.exe - @meshJsonArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Mesh Planner JSON incompatible or inconclusive; stop, do not retry.' }
```

Limits: one Mesh POST, zero retries, 15-second exchange / 30-second outer awaited
deadline, 1,024 requested output tokens, 24,000 request-body / 32,768 response
bytes. It requires complete strict assistant JSON, a valid one-task Planner
result, matching served model and valid usage <=1,024 completion tokens. It
outputs no task text, response body, completion ID or credential. Missing routing
metadata is uncertainty; stop for review if exposure is unclear even after exit 0.
This uses internal helpers verified for this checkpoint; re-review after changes.

## 3. One bounded end-to-end run

Suggested public demonstration question, chosen without fetching or asserting an
answer: **According to MDN, what does HTTP status 404 indicate?** It is short,
specific and asks for a definition that can be independently checked against
the returned MDN citation and an HTTP standard. Do not pre-fill a citation or
guarantee that search will return a particular source. A first result can be
irrelevant, too large, blocked or absent; review the actual retrieved evidence.

After reviewing both diagnostics, prepare the existing runner's offline preflight:

```powershell
$liveArgs = @(
    '--question', 'According to MDN, what does HTTP status 404 indicate?'
    '--model', 'google/gemini-2.5-flash'
    '--accept-gateway-uncertainty'
    '--confirm-spend-controls'
    '--mesh-input-usd-per-million', (Read-Host 'Approved Mesh input USD per million tokens')
    '--mesh-output-usd-per-million', (Read-Host 'Approved Mesh output USD per million tokens')
    '--brave-usd-per-request', (Read-Host 'Approved Brave USD per request')
)
.\.venv\Scripts\python.exe -m deepdive_ai.live @liveArgs
if ($LASTEXITCODE -ne 0) { throw 'Full-run offline preflight blocked; stop.' }
```

Only after separate full-run/spend authorization:

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.live @liveArgs --execute
if ($LASTEXITCODE -ne 0) { throw 'Run failed or needs review; stop, do not retry automatically.' }
```

Inspect `reports/live/<run-id>/manifest.json`, `audit.jsonl`, `outcome.json` and
`report.json` privately. Verify final source URLs, exact quotes, timestamps,
hashes and claim status before recording. Never count a partial report file as
success. No automatic fixture fallback or fixture import exists in the runner.
Only explicit test callers can inject mocked HTTP transports; the CLI cannot.
The dashboard still displays fixtures and cannot display a live report as its
own run. For a genuine live video, show the actual CLI/audit/report separately
and label any dashboard footage as offline. See [video/rehearsal script](submission_readiness.md).

## Client request and cost envelope

| Stage | Mesh | Brave | Source HTTP | Tokens / deadlines |
| --- | ---: | ---: | ---: | --- |
| Brave diagnostic | 0 | 1 | 0 | 10s exchange; 30s outer |
| Planner JSON probe | 1 | 0 | 0 | 1,024 output requested; 15s exchange / 30s outer |
| Full run | <=7 | <=2 | <=4, including redirects | 1,024 output requested per Mesh call; 120s overall awaited |
| All three once | <=8 | <=3 | <=4 | <=15 client HTTP attempts total |

All use zero automatic HTTP retries. The full run has one task, at most two
claims per round, one research retry, source exchanges <=10s, one source redirect
per fetch, 256,000 response bytes per source exchange and PDF <=3 pages. Bounds
reset in a new invocation; there is no global daily client or spend budget.

Let `Rin`/`Rout` be approved Mesh USD per million input/output tokens, `Rb` the
approved Brave USD/request, `I` reported prompt tokens and `O` completion tokens.
The JSON probe's nominal estimate is `(I*Rin + O*Rout)/1,000,000`; its requested
output contribution is at most `0.001024*Rout` if reported usage obeys the cap.
For the full run, nominal cost is `sum(I)*Rin/1,000,000 +
sum(O)*Rout/1,000,000 + Nbrave*Rb`. Requested output contribution is at most
`0.007168*Rout` and `Nbrave<=2`. Across all three stages once the requested
output contribution is at most `0.008192*Rout`, plus unknown input cost and
up to `3*Rb`. These are conditional arithmetic envelopes, not invoices or
provider-enforced monetary ceilings. No numeric model price or free credit is
assumed; direct Google prices cannot substitute for Mesh account prices.

Gateway retries/fallback, thinking-token mapping, billing after timeouts, final
request cap overruns, taxes and other unconfirmed costs remain outside local
control. Model/usage rejection is post-response and cannot undo a charge.
The full runner deliberately reports unknown potentially billable cost. Review
account rates, balance, auto-recharge and approved overrun exposure privately.
If acceptable exposure cannot be established, do not execute any paid stage.
OS DNS cleanup and synchronous persistence can outlast awaited deadlines.

Remaining live blockers: unproven Brave timeout cause, untested service-side
Planner/Drafter/Verifier JSON, source availability/extraction limits, strict
160-character task/claim/rationale limits, partial-file/Windows ACL constraints
and separate spend authorization. Offline checks cannot resolve these by
silently weakening validation or substituting research findings.

## Offline preparation verification

The existing live integration suite passed 20 tests, timeout-stage suite eight,
and Brave-only suite eight: **36 focused tests passed**, zero failures/errors/
skips. The exact inline Planner probe above was extracted and executed only
against explicit fake HTTP responses and fake environment credentials, with
DNS/socket guards: seven success/error/JSON/model/usage cases and zero-request
preflight passed. All six PowerShell blocks parsed without execution, and Git/
new-file whitespace checks passed. No live command, key inspection or network
request occurred. The previously passing 241-test application/CI checkpoint is
unchanged; no development phase or application feature was rebuilt.

No genuine application code defect was found that needed a fix in this phase,
so there is no code-fix commit/push/CI cycle. Only this new preparation runbook
is uncommitted; HEAD remains `bd3f9a2`. SPA's remaining notes are the unverified
live provider/JSON behavior, gateway/spending uncertainty and existing trusted
local storage limitations. Stop before live provider execution.
