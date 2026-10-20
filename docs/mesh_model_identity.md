# Non-streaming Mesh model identity and routing

On October 18, 2026 the operator supplied official Mesh support confirmation:
`google/gemini-2.5-flash` and `gemini-2.5-flash` identify the same model, and
provider-native response naming is valid for API version 2026-08 and newer.
This is operator-supplied provider confirmation, not a newly fetched public
document or an independently inspected support ticket. No live request was made
to validate the implementation.

Support also confirmed that non-streaming `X-Mesh-Routing-Model` means another
model answered, and `X-Mesh-Routing-Fallback: true` means backup-provider routing.
Streaming lacks those headers and needs dashboard-log verification. This client
continues to send `stream: false`; streaming is not implemented or enabled.

## October 20 response classification

The operator supplied further Mesh support confirmation: normal non-streaming
responses carry a true/false fallback header, while cached responses may carry
`X-Cache: HIT` without routing headers because no model was called for that
request. The latest historical failure did not retain cache metadata; it cannot
be retrospectively classified as a cache hit.

The client, live observer and connectivity harness share a fixed classification:
`normal_non_streaming`, `cached_response`, `unexpected_streaming`,
`unknown_response` or `invalid_metadata`. Only one exact `X-Cache: HIT` identifies
an explicit cache hit; `MISS` does not excuse absent routing metadata. Duplicate,
unknown or malformed cache values reject. A cache hit combined with routing
headers or an event stream is treated as contradictory and rejected.
Classification describes the response form, not successful validation; a
`normal_non_streaming` response can still fail any existing routing/model check.

**Cache classification does not establish no-fallback routing.** All explicit
cache hits remain rejected with `cached_response_unverified`, including those
with otherwise valid model metadata. Unexpected `text/event-stream` responses
reject with `unexpected_streaming`; the client never requests streaming.
`invalid_cache_metadata` and `contradictory_metadata` provide fixed additional
rejection codes. No raw cache/header values or response bodies are persisted.
Failure outcomes and audit events retain only fixed classifications and codes;
unrelated audit events have no Mesh classification.

Normal responses retain all existing model/version, routing, completion and
usage checks at their respective client/runner boundaries. No request budgets,
timeouts, retries, configured model or verification rules change. Authenticated
usage-request lookup remains unimplemented pending confirmation of its schema,
request-ID binding and cache/routing semantics. No lookup or live request is
performed by this change.

## Acceptance policy

The Mesh client, live runner and isolated connectivity harness share one policy:

- Exact requested/returned model equality remains valid for other identifiers.
  There is no arbitrary prefix removal, case folding or alias registry.
- Only the exact Gemini pair above can compare equivalent in either direction.
  Normalization requires one valid `X-Mesh-Version: YYYY-MM` response header at
  least `2026-08`. Missing/older version metadata cannot authorize normalization.
- Any `X-Mesh-Routing-Model` header rejects the response, including an empty value
  or one naming the requested model: its presence contradicts the allowed route.
- `X-Mesh-Routing-Fallback` must be present exactly once with value `false`.
  Rejecting missing fallback metadata is an **internal fail-closed policy**, not
  a claim that Mesh support guaranteed this header is always present.
- Duplicate checked metadata is rejected even when values agree, including
  case-variant names. Invalid versions, control/whitespace/non-ASCII values,
  combined lists and malformed optional attempt counts are rejected.
- Optional routing attempts must be bounded positive ASCII integers. A valid
  count greater than one remains a connectivity warning, not a local guarantee
  about upstream attempts or billing. Client retries and request budgets do not change.

Exact-name responses do not require a version header unless the operator asks
the connectivity harness to check a version. A supplied malformed version still
fails routing-metadata validation. Neither alias acceptance nor display
allowlisting bypasses routing, completion-schema, token-usage or agent checks.

## Diagnostics and limits

Rejections use `response_validation`. Live failed outcomes retain boolean
`model_matches` and `routing_metadata_valid` checks plus the existing bounded
model-display diagnostics. Raw routing identifiers, arbitrary header values,
prompts, completions and credentials are not added to logs or persisted outcomes.
Connectivity retains safe parsed usage and failed-check names for identity failures
after the completion schema has passed.

Live routing failures additionally persist an allowlisted `routing_rejection_codes`
list in `outcome.json` and the existing `failed` event in `audit.jsonl`:
`fallback_missing`, `fallback_true`, `fallback_invalid`, `routing_model_present`,
`duplicate_metadata`, `invalid_version`, `invalid_attempts`, and
`invalid_metadata_value`. Multiple applicable codes may appear, in fixed order.
For example, duplicate fallback fields also prevent reading a single valid
fallback value, so both `duplicate_metadata` and `fallback_invalid` can appear.

The original routing rejection codes remain diagnostic; the original model and
routing requirements remain in force. The shared classifier additionally rejects
unverified cache hits, streaming and invalid/contradictory cache metadata.
Rejection diagnostics retain no header values. Audit codes are typed, unique,
bounded to the fixed enum values and permitted only on failure events. Other
events have an empty code list. No extra audit event or network request is added.
Offline tests cover every category, accepted metadata, overlapping malformed
metadata, persistence and secret redaction. Earlier failed runs contain no such
codes and cannot be reconstructed from this update; no live rerun is implied.

This deliberately tightens earlier behavior that treated missing/malformed
routing data or reported fallback as warnings. Historical phase records describe
that old behavior and do not override this policy. Header absence can therefore
still block a future live run; validation must not be relaxed to make it pass.

No API-key provider pinning, request headers, retry counts, timeouts, spending
acknowledgements, TLS, SSRF, citations, agent JSON or evidence verification rules
were changed. Offline fixtures now explicitly model an accepted no-fallback
response. The six authored evaluation cases remain separate from live research.

The previously observed Drafter mismatch is now an offline-tested supported
normalization case. At the latest verified checkpoint `7a028fd` (320 offline
tests passed), the latest live attempt `f1728f2859d04a00943f67187561b970`
returned HTTP 200 and passed model identity validation, but failed with
`fallback_missing`: `X-Mesh-Routing-Fallback` was absent. This does not prove
fallback occurred or establish a completed live workflow. Resolve the missing
metadata without weakening safeguards before a separately authorized live test.
The official Agent Arena checker has not yet been activated.
