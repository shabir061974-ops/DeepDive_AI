# Live Planner compatibility investigation

Started from clean checkpoint `8441680`. The operator reported a separately
authorized Mesh Planner probe with HTTP 200, one attempt, incompatible agent
JSON, `agent_contract_or_runtime_failure` and failed status. Brave separately
passed HTTP 200. These are operator-provided observations, not provider requests
performed during this investigation. No response body or private research file
was inspected, no key was accessed and no live API/source request was made.

## Confirmed findings

The exact request path in the documented probe is:

`MeshPlannerAgent.plan -> MeshJSON.ask -> MeshAPIClient.complete -> bounded
POST -> strict completion envelope parser -> strict agent JSON parser ->
Planner root/task/instruction validation -> safe metadata checks`.

The completion parser requires JSON media type, a nonempty ID, exactly one
assistant string completion, `finish_reason: stop`, no truthy tool calls and
strict JSON. Its errors are typed `HTTPError` derivatives. `MeshJSON` then
requires nonempty bounded text and JSON without duplicate keys, nonfinite
constants or fenced/prose wrappers. Planner requires exactly a `tasks` root
field, a list of one task in this profile, an exact `instruction` field and a
nonempty string <=160 characters. Missing/extra fields, wrong types, excess or
empty tasks and invalid instruction text are rejected.

The old probe mapped both `AgentOutputError` and unrelated non-HTTP exceptions
to one `agent_contract_or_runtime_failure` string. A completion-parser HTTP
error would normally have produced its own typed failure category. Therefore
the reported path is consistent with successful completion parsing followed
by an agent contract rejection, but the generic category does not exclude an
unrelated runtime error. HTTP 200 alone is not proof of valid agent JSON.

The probe does not automatically persist raw responses and no retained
check-level diagnostic establishes which condition failed. The original live
root cause cannot be proved or reconstructed from its five reported fields.

Two implementation issues are confirmed offline:

1. The generic diagnostic loses the particular agent validation condition.
2. The prompt explicitly requested one task but did not communicate the
   enforced 160-character instruction bound. It now states the actual bound.
   This omission is confirmed; there is no evidence that the live instruction
   actually exceeded that bound, so this change is not claimed to fix that run.

## Hypotheses, not observed live content

Plausible causes include non-JSON/fenced/prose output, a wrong root or extra
fields, missing/wrong task fields, zero or more than one task, empty/non-string
instruction text, an instruction longer than 160 characters, duplicate keys or
invalid Unicode. A runtime failure remains possible. There is insufficient
evidence to rank these with confidence or assert any exact live response text.
No wrapper stripping, JSON repair, field dropping, truncation, limit increase,
provider JSON-mode parameter or speculative schema normalization was added.

## Smallest safe improvement

`AgentOutputError` now carries an allowlisted `AgentFailureCode`, retaining its
existing exception type and strict rejection behavior. Generic validation
helpers distinguish invalid/oversized text, object/field mismatch, list shape/
count, duplicate keys, nonfinite constants, malformed JSON and invalid response
records. Planner adds empty/duplicate-task codes. The diagnostic exports only
the enum's fixed code, never compared values, response snippets, arbitrary keys,
task text, IDs, headers or raw exception messages. Invalid code values fall
back to `invalid_agent_output`.

The full live runner persists `agent_failure_code` for typed contract failures.
The exact inline Planner probe is updated to report `agent_contract_failure`
with that code, while unrelated runtime errors retain the generic fallback.
Its one-request/zero-retry policy, model/usage checks and spending gates remain
unchanged. PowerShell retains an already-defined script in memory: redefining
it from the updated runbook is required before a future authorized invocation.

The Planner prompt now states `max_text_chars` explicitly using its configured
value. It still requests the same schema and retains the same validator. The
160-character boundary remains enforced; a 161-character instruction still
fails. Production Drafter/Verifier acceptance, citations, evidence, provenance,
TLS/SSRF, request limits, timeouts, prices and gateway-risk controls are unchanged.

## Offline evidence and next step

New tests execute the exact documented probe against authored HTTP 200 responses
through the real Mesh client, JSON parser and Planner validator. Thirteen
rejection variants now yield distinct fixed codes with one call and no data
exposure. Tests also verify a valid response, zero-call preflight, truncated
completion rejection, explicit prompt bound, unchanged 160/161 boundary,
duplicate-task rejection and invalid diagnostic-code fallback. These examples
reproduce possible failure paths; none is asserted to be the original response.
The existing full-run malformed-JSON test now checks the persisted code too.

SPA recommendation: **APPROVED WITH NOTES** for the offline diagnostic and prompt
clarification. Actual live compatibility remains unresolved. After reviewing
the published change, a separately authorized single Planner probe using the
updated runbook can identify the failing condition on a new response. It cannot
recover the original response or guarantee future compatibility. Stop on failure
and review the safe code; no automatic retries or full research run are justified
by this investigation. Brave's separate pass does not validate Mesh agent JSON.

## Verification and publication gates

Complete offline suite: **247 tests passed in 10.267 seconds**, zero failures,
errors or skips (241 existing and six new). All six offline browser scenarios,
desktop/mobile attribution/layout and fresh Markdown download passed, with no
browser errors or external page requests. Python compilation, both JavaScript
syntax checks, all six PowerShell block syntax checks and Git/new-file whitespace
checks passed. The official checker/template, workflow, provider adapters,
request/timeout limits and evidence verification were not changed.

Only these scoped files are included: README, this investigation, the Phase 10
runbook, `agents/common.py`, `agents/planner.py`, `live.py`, `tests/test_live.py`
and `tests/test_planner_diagnostics.py`. Publication is authorized after the
security/staging gates; GitHub CI must pass for the resulting commit. No live
request is authorized or performed as part of these gates.
