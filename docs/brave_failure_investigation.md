# First live Brave failure: offline investigation

Starting checkpoint: `ed37933`, verified with a clean working tree. The operator
reported one failed authorized Brave attempt, null HTTP status, incompatible
response, unknown potentially billable cost, zero Mesh attempts and no preflight
blockers. No further live provider calls or credential inspection were performed
during this investigation. Only source files and mocked fixtures were inspected.

## What the original result establishes

The endpoint remains pinned to
`https://api.search.brave.com/res/v1/web/search`, using GET, subscription-token
authentication, `count=1`, `en/US`, web-only filtering and identity encoding.
No endpoint/configuration defect was found in this path. That does not prove
current endpoint reachability or credential validity.

An attempt increments immediately before calling the transport. It does not
prove DNS succeeded, a connection opened or a provider received the request.
Preflight validates configuration format and explicit safety acknowledgements;
it does not authenticate keys or test connectivity.

Previously `_Observation` saved a response only after the transport returned
the entire accepted body. Thus a null status did not necessarily mean no HTTP
headers were received. The transport could reject oversized headers/body,
compression, framing or an incomplete response after receiving status 200.
DNS/TLS/socket errors and timeouts also return no complete response.

Three layers then removed useful distinctions: the transport collapsed several
exception types, Brave replaced request errors with a generic `DiscoveryError`,
and the harness caught all ordinary exceptions and printed only failure flags.
`response_compatible=false` meant the check did not succeed; it did not identify
a JSON parsing error. Unknown cost was a conservative default, not evidence of
a charge or proof of a free failure.

## Likely failure domain, not a proven root cause

| Candidate | Expected old status | Interpretation |
| --- | --- | --- |
| DNS, TLS, connection failure | null | Consistent with report |
| 10-second attempt timeout | null | Consistent; includes connection/body work |
| Response exceeds 16,384 bytes | null in real transport | Consistent even if HTTP headers arrived |
| Unsolicited compression, invalid framing, incomplete body | null in real transport | Also consistent |
| Complete authentication error | 401 or 403 | Null does not specifically support an invalid-key diagnosis |
| JSON/schema/content-type parsing after full response | 200 | Does not directly explain the reported null |

The report most directly points to failure before a complete transport response
was returned, rather than ordinary application-level JSON parsing or a complete
authentication rejection. It cannot distinguish the consistent candidates.
Even an authentication response could have been interrupted or rejected while
reading its body. Do not assert that authentication succeeded or that no request
was billed. The exact historical exception cannot be recovered from this output.

## Changes

- HTTP errors carry a fixed `FailureCategory` enum and an optional validated
  integer HTTP status. Categories never derive from exception messages.
- DNS resolution, TLS/socket failures, timeouts, incomplete responses, unsafe
  destinations, byte limits, encoding and protocol failures retain distinctions.
  Known status is preserved when headers preceded a body failure.
- The bounded client and Brave adapter propagate only these safe fields through
  their existing sanitized exceptions. HTTP status errors distinguish auth,
  billing, rate limiting, redirects, server errors and other rejected statuses.
- Harness output adds `failure_category`, null for successful or unattempted
  checks. Unknown exceptions become `internal_error`; they are not guessed from
  message text. Mesh compatibility failures use `response_validation`.

Representative fixed values include `dns_failure`, `tls_failure`,
`network_failure`, `timeout`, `response_too_large`, `unsupported_encoding`,
`http_protocol_failure`, `incomplete_response`, `authentication_http_status`,
`response_parsing` and `unsafe_destination`. These describe the observed failure
path, not necessarily its ultimate operating-system or provider cause.

No raw exception strings, response bodies, URLs, DNS answers, headers or
credentials were added to reports. Limits, retries, TLS verification, SSRF
checks, fail-closed parsing, redirect rejection and stop-before-Mesh behavior
remain unchanged. The 16 KB limit and identity-only encoding were not relaxed
without evidence. A timeout can still leave billing uncertain, and some async
timeouts cannot recover a status from unfinished worker-thread work.

## Verification and next step

Nine new offline regression tests reproduce DNS, TLS, connection and timeout
failures; received-status/body-read failures; oversized/compressed/framing/
incomplete responses; authentication and other HTTP errors; JSON/unsafe results;
unknown exceptions; and private DNS rejection. They exercise the real protected
transport with mocked connections or injected transports, with guards against
live DNS and socket connections. Every Brave failure leaves Mesh unattempted.
Existing tests continue to run unchanged.

Do not rerun the live harness or change credentials based on this ambiguous
result. The safest immediate step is SPA review of these diagnostic changes and
private review of already-existing DNS/firewall/proxy/TLS logs around the original
attempt, if available. Do not enable body/header tracing, disable certificate
verification, increase limits blindly, or paste raw logs containing secrets.
Any future billable diagnostic attempt needs separate authorization; a targeted
Brave-only investigation should precede Mesh testing. This task authorizes no
additional network diagnostics or provider requests.

Validation: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
passed all 163 tests in 7.084 seconds, with zero failures, errors or skips
(154 existing tests unchanged, nine new). Python compilation of `src`, `tests`
and `main.py` passed. `git diff --check` and new-file whitespace checks passed.
HEAD remains `ed37933`; seven intended files are modified/new and nothing is
staged. No commit, push or deployment was performed. Stop for SPA review.
