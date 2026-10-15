# Phase 5A provider readiness audit

Audited 2026-10-15 from clean checkpoint `39cc3a0`. This is an offline readiness
audit, not evidence of successful live integration. Only public documentation
pages were consulted; no inference, search API, catalog, balance or account API
requests were made. No credentials were displayed, copied, or logged.

## Configuration readiness

Both `MESH_API_KEY` and `BRAVE_SEARCH_API_KEY` were absent from the auditing
process environment. Presence/nonblank checks do not authenticate a credential.
No `.env` file, credential store, dashboard or account state was inspected.
The application reads process environment variables; it does not load `.env`.
Set credentials privately in the process that will eventually run the adapters.
Do not include real keys in command examples, source, reports or logs.

No live model is configured: the workflow factory requires an explicit model.
Account access, selected-model availability, balance, rate limits and billing
authorization remain unverified. Do not use test model identifiers with a service.

## Documentation compared with implementation

Documentation findings below are provider statements, not runtime observations.
Public pages and prices can change; recheck before authorized live use.

### Mesh

The [official quickstart](https://developers.meshapi.ai/docs/getting-started/quickstart)
confirms `https://api.meshapi.ai/v1/chat/completions`, bearer authentication and
chat requests containing a model and messages. Its example reads
`choices[0].message.content`. Our endpoint, header and basic message envelope
match that example.

The [authentication guide](https://developers.meshapi.ai/docs/getting-started/authentication)
distinguishes inference keys from management keys and requires HTTPS. Provision
an inference key with minimal spend/rate permissions; a nonblank environment
value alone does not prove it is the correct key type.

The [model reference](https://developers.meshapi.ai/docs/reference/models)
describes model identifiers, modalities, capabilities and context limits. Choose
an explicitly available text chat-completions model and verify its output-token
parameter and total context allowance. The adapter accepts caller-supplied names;
it does not check a catalog, tokenize prompts or select a model automatically.
Agent JSON requests are prompt instructions, not enforced provider JSON mode.

The [versioning guide](https://developers.meshapi.ai/docs/reference/api-versioning)
documents optional `X-Mesh-Version` pinning and an effective version response
header. Our client does not pin or retain these headers; behavior may depend on
the inference key's version configuration. An approved version must be recorded
before a smoke test. No speculative version header was added.

The [routing guide](https://developers.meshapi.ai/docs/platform/retry-and-fallback)
describes gateway retries and fallback inside one HTTP request. Its documented
system policy permits up to three upstream attempts and 60 seconds; key policies
can differ. A client `max_requests=1` therefore does not guarantee one upstream
attempt. Before testing, verify a per-key policy with `retry.max_retries=0`,
`fallback.enabled=false`, `budget.max_attempts=1` and
`budget.max_total_latency_ms=10000`, with no model substitution. The response
can expose routing attempts, fallback and served-model information. The current
public `MeshResponse` discards this metadata and usage; its `request_id` is the
completion body's `id`, not a verified gateway trace ID. An authorized future
harness must inspect only allowlisted metadata before declaring a smoke test
successful. Default routing is not sufficiently bounded for that test.

The documentation index linked a full chat reference/OpenAPI document, but those
pages could not be retrieved by the documentation browser during this audit.
Consequently the exact `max_tokens` behavior for the chosen model, mandatory
completion ID, finish reasons, and response shape remain compatibility
assumptions. The client deliberately accepts only one nonempty assistant text
completion with `finish_reason="stop"`; tools, truncation and other response
variants fail closed. No live compatibility claim follows from offline fixtures.

### Brave Web Search

The [service guide](https://api-dashboard.search.brave.com/documentation/services/web-search)
confirms the GET endpoint `https://api.search.brave.com/res/v1/web/search` and
`X-Subscription-Token` authentication. The endpoint is pinned in settings, and
the key is sent only in that header.

The [current API reference](https://api-dashboard.search.brave.com/api-reference/web/search/get)
documents default country `US`, language `en`, 1-20 requested results,
`spellcheck=false` and `result_filter=web`. Phase 5A replaces the undocumented
three-character default `ALL` with `US`, requires two uppercase ASCII country
letters, and requests web results only. Existing explicit `ALL` configurations
must migrate. Country/language validation checks format, not the full provider
enumeration: other codes still require provider confirmation.

The reference allows queries up to 600 characters/75 words; our unchanged local
400-character/50-word limits are intentionally stricter. Returned counts can be
smaller. URLs come from `web.results`; no snippets become evidence. The schema
allows nullable `web`; our existing parser rejects explicit null safely, while
absent web results or an empty results list yield no candidates. This conservative
subset may reject a valid no-results response. An optional date-based API version
header is documented; our adapter omits it, so default version changes remain a
compatibility risk.

## Transport and failure review

Both adapters exclusively use the existing bounded HTTP transport. HTTPS
certificate/hostname validation, all-answer public DNS checks, connection IP
pinning, private-network rejection, byte/header caps, identity-only response
encoding and bounded timeouts remain in place. No proxies, cookies or automatic
redirects are enabled. Authenticated redirects, even to the same host, fail.
Brave candidates are syntax/IP checked; their DNS is checked only if subsequently
retrieved. Search itself does not fetch those URLs.

Bodies must be UTF-8 JSON of the expected media type. Duplicate keys, nonfinite
numbers, unsafe result URLs, excessive counts and malformed records fail. Errors
omit credentials, prompts, queries and upstream response bodies. Settings and
HTTP records redact sensitive fields in `repr`; do not log `dataclasses.asdict`,
raw headers, exception internals from third-party transports, or payloads.

Current defaults are not smoke-test policies:

| Bound | Mesh default | Brave default |
| --- | --- | --- |
| Per-attempt timeout | 20 seconds | 10 seconds |
| Client retries | 2 | 1 |
| Lifetime HTTP attempts | 100 | 32 |
| Concurrent awaited requests | 4 | 2 |
| Response bytes | 2,000,000 | 256,000 |

Each retry consumes the same instance budget. Transient HTTP statuses can retry;
authentication errors, redirects and malformed successful responses do not.
Ambiguous Mesh POST transport failures are never retried, since completion may
already have occurred. Numeric Retry-After delays are capped. A timed-out call
does not establish that provider work or billing stopped. OS DNS resolution can
outlive cancellation in a worker thread; it must not trigger a replacement call.
No changes were made to provenance, extraction, agents, semantic verification,
workflow retries or audit logging.

## Future minimal live-test plan (not executed or authorized by this audit)

1. Obtain SPA review and explicit authorization for the exact paid-use ceiling.
   Privately provision the two environment variables. Confirm plan access and
   billing in provider dashboards without adding catalog/balance calls to this
   test. Choose and record a fixed Mesh model, compatible versions, context and
   token pricing. Never use `auto` or an unpriced model. Verify the single-attempt
   server routing policy above; if it cannot be confirmed, stop before any call.
2. Prepare a reviewed harness using the existing adapters, with an allowlisted
   metadata observer for Mesh served model/version/routing and usage. The present
   adapter API alone cannot validate these fields; this harness is deferred, not
   implemented here. Never persist raw headers, keys or prompt/response bodies.
3. Run **one Brave GET first**, query `Python documentation`, `count=1`, `en/US`,
   web-only. Use `RequestPolicy(timeout_seconds=10, max_retries=0, max_requests=1,
   max_concurrency=1, max_request_bytes=2048, max_response_bytes=16384)`.
   Require HTTP 200 and a safe parsed result or an accepted empty-results shape.
   Do not open result URLs. Only proceed to Mesh after a successful search check.
4. Run **one Mesh POST**, prompt `Reply exactly OK.`, approved fixed model,
   `max_output_tokens=64` on both settings and request. Use the same policy with
   `timeout_seconds=15`. Require HTTP 200, a complete text response `OK`, approved
   served model/version, no fallback and no more than one upstream attempt.
   Missing required metadata is inconclusive: stop rather than call again.
5. Maximum: **two client HTTP requests total**, zero client retries, zero source
   fetches, zero additional provider endpoints and no five-agent workflow. Do not
   recreate clients to evade budgets. Run sequentially with an outer 30-second
   deadline; per-call deadlines total at most 25 seconds of awaited HTTP work.
   Cancellation is not proof of no charge. Record only status, duration, attempt
   count, approved model/version, token counts and cost reconciliation.

Stop the entire run on missing configuration, any redirect, TLS/DNS/security
failure, timeout, HTTP 401/402/403/429/5xx or other non-200 status, malformed,
oversized or unexpectedly compressed data, unexpected model/version/output,
fallback, excess attempts, missing required metadata, or an unknown/exceeded
spend ceiling. No automatic resubmission or fallback follows any failure.

### Cost gate

The [Brave public price page](https://brave.com/search/api/) advertised Search
at $5 per 1,000 requests when reviewed: nominally $0.005 for the single GET,
before credits, taxes or account-specific terms. It also advertised monthly
credits; eligibility and remaining credit were not checked. Do not assume free
usage. Confirm the account's actual charge ceiling before authorization.

The [Mesh pricing guide](https://developers.meshapi.ai/docs/getting-started/pricing)
describes prepaid, model-dependent input/output token charges. No model was
selected, so a numeric estimate or free allowance cannot be verified. With
approved per-million rates `P_in` and `P_out`, estimate
`(billable_input_tokens * P_in + billable_output_tokens * P_out) / 1_000_000`.
The 2,048-byte request bound is not a tokenizer or billing bound. Establish a
provider-appropriate input-token ceiling and allow for billable overhead before
approving spend. Key spend caps are useful additional controls but final request
settlement may exceed a cap. A dollar ceiling remains unresolved until model,
token accounting and account terms are confirmed. This blocks execution now.

## Offline validation scope

Seven new deterministic tests cover default/explicit country configuration,
web-only query parameters, single-attempt failure behavior, payment-required
handling and per-client lifetime budgets. Fixtures are authored, not research
findings. All existing 129 tests are retained unchanged. Offline tests cannot
establish credential validity, actual billing, endpoint availability, provider
schema stability, search quality or model accuracy. No dependencies were added.

Validation on 2026-10-15:

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`: 136 tests in
  6.393 seconds, OK; zero failures, errors or skips. The original test files are
  unchanged.
- `.\.venv\Scripts\python.exe -m compileall -q src tests main.py`: passed.
- `git diff --check`: passed. Git reports only an existing automatic LF-to-CRLF
  normalization warning for `search.py`, not a whitespace error.
- HEAD remains `39cc3a0`; four intended modified/new files, nothing staged and
  no commit. Await SPA review.
