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

This deliberately tightens earlier behavior that treated missing/malformed
routing data or reported fallback as warnings. Historical phase records describe
that old behavior and do not override this policy. Header absence can therefore
still block a future live run; validation must not be relaxed to make it pass.

No API-key provider pinning, request headers, retry counts, timeouts, spending
acknowledgements, TLS, SSRF, citations, agent JSON or evidence verification rules
were changed. Offline fixtures now explicitly model an accepted no-fallback
response. The six authored evaluation cases remain separate from live research.

The previously observed Drafter mismatch is now an offline-tested supported
normalization case, not proof of a successful live fix. A separately authorized
live run is still needed to validate actual headers and all five-agent stages.
