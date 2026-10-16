# Phase 10: second Mesh HTTP-200 validation failure

Reviewed from clean checkpoint `d4cf883`. The explicitly identified failed
run's private outcome and metadata audit were inspected without reading keys,
raw response bodies or unrelated research artifacts. No live provider/source
requests were performed. The running dashboard was not changed or restarted.

## Verified findings

The persisted outcome records two Mesh attempts, one Brave attempt and one
source attempt, `response_validation`, Mesh HTTP status 200, no known transport
stage and no `agent_failure_code`. Its audit sequence is
`started -> planned -> researched -> failed`, all in research round zero.
There is no final report. No raw response was retained in this run directory.

The coordinator emits `researched` after successful retrieval and bundle
validation, then invokes Drafter and emits `drafted` only on success. Planner
makes the first Mesh call, Researcher makes no LLM call, and Drafter makes the
next call for nonempty evidence. This establishes the failed second call as
Drafter's; this conclusion uses the code and audit, not attempt count alone.

The `response_validation` category on HTTP 200 is raised by the live runner's
`ObservedTransport` model/usage gate **before** `MeshAPIClient` completion-envelope
parsing and `MeshJSON`/Drafter JSON/schema validation. Thus a Drafter Markdown
fence, missing claim field or malformed claim JSON is not the recorded rejection.
This also does not prove those later contracts would have passed.

The gate requires the exact requested model, a usage object, three integer
token fields in range 0..1,000,000,000 (excluding booleans), consistent token
totals and completion tokens <=1,024. The previous combined check retained only
one category, so the exact failed field cannot be recovered. Model mismatch,
missing/wrongly typed usage, inconsistent totals or an output-limit violation
remain unranked hypotheses. Malformed outer JSON/root is also possible because
this gate parses the body before the stricter completion parser. No response
content or token count is inferred from earlier successful connectivity tests.

## Smallest safe change

The gate now retains fixed boolean `mesh_validation_checks` under
`diagnostics.mesh` in the terminal result and existing persisted `outcome.json`:
`response_json_valid`, `root_object`, `model_matches`, `usage_object`,
`token_fields_valid`, `token_total_matches`, `output_limit_satisfied`.
True means established by that check. False means failed or could not be
established because prerequisite parsing/type checks failed; these flags are
not independent evidence that every false field was actually present/invalid.
No raw model ID, usage values, arbitrary metadata, response text, prompts,
credentials, headers or exception messages are added. No response is archived.
The existing terminal category, attempt budgets and audit events remain intact.

Acceptance is unchanged: all the same metadata checks must pass. The existing
Mesh completion parser and each agent's strict JSON/schema validation still
run afterward. Planner-only whole-document fence support remains Planner-only;
there is no evidence justifying extending it to Drafter for this failure.
No fallback, repair, limit increase, new parameter or automatic retry was added.

Offline regressions reproduce each metadata failure on the second request
through the real agent/client/workflow composition with authored HTTP doubles.
They check safe persisted diagnostics, exact attempt counts, audit sequence,
missing/invalid usage and JSON, model mismatch, totals, the 1,024/1,025 boundary,
optional metadata and unchanged rejection of fenced Drafter JSON.

SPA: **APPROVED WITH NOTES** for the offline diagnostic fix. The particular live
metadata failure remains unknown. A separately authorized future run can reveal
the failing check on its own response; it cannot reconstruct this old response.
Review that result before changing model matching, token accounting or limits.
Do not automatically repeat a paid full run or claim live research success.

Verification: all 276 offline Python tests passed, including five new full-path
regression tests with multiple rejection cases. Six browser scenarios, Python
compilation, JavaScript syntax and whitespace checks passed. A publication scan
of 77 project files found no credential-pattern or private-artifact findings.
The CI workflow, inactive checker template and dashboard files are unchanged.
