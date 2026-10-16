# Phase 7B: Mesh HTTP 200 response validation investigation

Starting HEAD `763c197` was verified clean. This investigation used local source,
tests and documentation only. No live requests, credential inspection, paid
operations, TLS changes, commits, pushes or deployments occurred.

## What the reported result proves

The operator reported HTTP 200, one client attempt, one reported gateway attempt,
no reported fallback, usage 4 prompt / 27 completion / 31 total tokens,
`response_validation`, incompatible response and zero Brave attempts. These are
operator-reported observations, not a response captured by this investigation.
No retained live response structure was found in the reviewed docs/tests. The
harness intentionally does not write raw responses to disk. The authored test
fixtures are not recordings and must not be described as the live response.

The exact code path is:

1. The bounded shared transport returns a complete response; existing TLS,
   SSRF, redirects, timeouts, size limits and error handling apply.
2. `MeshAPIClient.complete` requires HTTP 200 and JSON content type. It parses
   strict JSON and requires a nonempty string ID, exactly one choice, an
   assistant message with nonempty string content, no truthy tool calls, and
   `finish_reason == "stop"`. A failure here raises `MeshError`, resulting in
   the existing coarse `http_protocol_failure`, not `response_validation`.
3. Only after that parser succeeds does the harness read safe routing metadata
   and usage, compare the returned model, optionally compare the expected
   version, check `response.text.strip() == "OK"`, and enforce reported valid
   completion usage <= 64. A false condition sets `response_validation`.

Consequently transport completion, HTTP success and this narrow response-schema
compatibility succeeded on the reported path. The application smoke-test
contract failed. This does not establish correct instruction-following, valid
research, claim verification, billing correctness or all-model compatibility.
Routing headers describe gateway-reported activity, not independently audited
gateway behavior.

27 <= 64 and 4 + 27 == 31: the supplied usage is not an output-cap violation.
Missing/invalid usage is already allowed as unknown and cannot itself trigger
this category. Finish reason, non-string content or malformed JSON cannot be
the immediate cause of this particular reported failure at this checkpoint.

## Remaining hypotheses

The exact cause is not provable from the retained aggregate report. Conditional
ranking, not a statistical estimate:

1. **Exact-reply mismatch** is the leading explanation if the returned model
   really matched and no expected version was supplied. Extra punctuation,
   formatting or explanation would fail. These are examples, not claims about
   the actual response; 27 completion tokens alone does not prove verbosity
   because usage can include unobserved internal work.
2. **Returned-model mismatch/missing model** remains possible if the supplied
   model name describes the requested configuration rather than a checked
   response field. The harness does not expose the raw returned value. If the
   operator verified that field independently, this hypothesis is eliminated.
3. **Expected-version mismatch/missing version** is possible only if
   `--mesh-version` was supplied. If supplied, it is as viable as the other
   remaining checks; if omitted, this condition cannot fail.

Multiple conditions can fail simultaneously. The previous combined boolean
and short-circuit comparisons did not preserve which condition failed.

## Smallest justified change

Add fixed structural diagnostics after the existing schema parser succeeds:

| Check | Meaning |
| --- | --- |
| `response_schema_compatible` | True only after the existing Mesh parser returned a completion |
| `model_matches` | Returned model exactly equals the fixed candidate |
| `expected_version_matches` | True/false when explicitly configured; null otherwise |
| `reply_matches` | Trimmed response text exactly equals `OK` |
| `output_limit_satisfied` | Valid completion usage <= 64; null when usage is unavailable/invalid |

`failed_checks` lists only false check names. Checks are evaluated independently,
so simultaneous failures remain visible. No response text, lengths, IDs, model
strings, version values, arbitrary field names, bodies, headers or exceptions
are added to output. Existing counts/metadata/unknown-cost handling remain.
Parser errors do not claim schema compatibility. Unknown does not mean verified.

**No acceptance rule or parser behavior was relaxed.** Exact `OK` is an internal
instruction-following smoke-test contract, not a provider authentication rule.
Accepting arbitrary nonempty text would hide its failure without establishing
the unknown live response was appropriate. A future change to that contract
needs an explicit justification, rather than a guessed response reconstruction.

## Response-variant review

The client intentionally implements a narrow non-streaming text subset, not a
complete OpenAI-compatible schema. Text-part arrays, nullable content, tool
responses and finish reasons other than `stop` remain unsupported. Some other
interfaces may use such representations; no retained Mesh response demonstrates
they are needed here. Partial/filtered/tool output must not be silently accepted
as a completed text inference. Adding normalization now is not justified by this
failure, which already passed the current parser.

Extra optional top-level metadata is ignored. Additional usage-detail fields
are ignored while the three bounded nonnegative integer totals must agree for
a cost estimate. Missing usage remains unknown; it is not invented or zeroed.
No routing headers or version header are required for schema compatibility;
strict version comparison applies only when explicitly requested. Duplicate
JSON keys, empty content, malformed choices, active tool calls and truncation
continue to fail closed. Existing schema restrictions may reject future valid
provider variants, and would need separately evidenced tests before changes.

## Offline validation and SPA recommendation

Eight new tests use authored fake exchanges to reproduce each possible harness
failure, including identical 4/27/31 usage with different failing conditions.
They also cover simultaneous mismatches, safe redaction, success/unchecked
version, exact-reply whitespace/near matches, unknown usage, optional metadata,
strict schema rejection and the 64-token boundary. They do not reconstruct the
live body. One existing test's expected diagnostics shape is updated; its
acceptance expectations are unchanged.

Verification on 2026-10-16:

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`: **219 tests
  passed in 9.506 seconds**, zero failures, errors or skips (211 existing plus
  eight new).
- `.\.venv\Scripts\python.exe -m compileall -q src tests main.py`: passed.
- `git diff --check` and explicit whitespace checks for the two new files: passed.

Six files changed, all unstaged: `README.md`, `docs/controlled_connectivity.md`,
this document, `src/deepdive_ai/connectivity.py`, `tests/test_connectivity.py`
and `tests/test_mesh_validation.py`. HEAD remains `763c197`; nothing is staged
or committed.

SPA can review this diagnostic-only change. No request/retry/timeout/token/byte
budget, spending control, TLS configuration, agent or dashboard code changed.
Remaining uncertainty is the exact failed check and actual inference quality;
there is still no hard local USD ceiling or independent gateway audit.

If no existing sanitized check-level record is available from the original run,
**one separately authorized Mesh-only request is necessary to observe the checks
on a new run**. It cannot recover or prove the original response contents.
Use the existing reviewed Mesh-only commands and unchanged spending gates only
after SPA and independent live/spend approval. No automatic resubmission. A
future pass must not be retroactively attributed to the original response.
No further request is necessary to establish the code-path conclusions above.
