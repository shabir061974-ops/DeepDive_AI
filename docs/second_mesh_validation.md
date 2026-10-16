# Phase 10: second Mesh HTTP-200 validation failure

## Current model-identity display diagnostic

From checkpoint `cb3cfde`, Mesh `response_validation` failures now add three
safe fields under `diagnostics.mesh`, persisted by the existing failed-run
`outcome.json` writer:

- `model_field_present`: boolean indicating whether a parsed root object has
  the `model` key.
- `model_field_type`: fixed JSON-type label `null`, `boolean`, `number`,
  `string`, `array` or `object`; `missing` denotes no such root field and
  `unavailable` denotes failed JSON decoding or a non-object root. When
  unavailable, false presence means not established, not proven absence.
- `public_model_identifier`: an exact match for `google/gemini-2.5-flash` or
  `gemini-2.5-flash`, otherwise JSON null. No trimming, case folding, prefix
  removal, regex-based acceptance, arbitrary strings or hashes are recorded.

This finite display allowlist is completely separate from response acceptance.
The exact `data.get("model") == requested_model` comparison is unchanged.
In particular, the short identifier is still rejected for a provider-prefixed
request even when it is safe to display. All other metadata, completion-envelope,
agent-schema, citation and verification rules continue to apply. No additional
requests, retries, timeouts, spending policy, credentials or dashboard changes
are introduced. Audit events and outcome persistence behavior are unchanged.

The operator's Activity Log description of a short requested label, Vertex AI,
no retries/fallback and Mesh version 2026-08 does not establish the response
body's model value or an alias contract. No live request was made to investigate
it. Future separately authorized execution can provide the safe field metadata;
it cannot reconstruct older responses or authorize alias acceptance.

Current verification: all 295 offline Python tests passed, including five new
identity-diagnostic regression tests covering allowlisted display values,
unexpected strings, invalid/missing types, redaction and exact-match acceptance.
All six browser scenarios, compilation, JavaScript syntax, whitespace and
publication security checks passed. No live provider/source request was made.

The following sections preserve the preceding investigation's historical
findings, before these three display fields were added.

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
