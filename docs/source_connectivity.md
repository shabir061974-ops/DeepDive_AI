# Standalone source-connectivity diagnostic

Prepared from clean checkpoint `f342118`. No live provider or source request
was made during implementation or tests. This is not a research run and does
not establish that the previous failed source is reachable.

The fixed candidate endpoint is MDN's HTTP 404 documentation:
`https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/404`.
It has not been checked live in this preparation. The expected response is
HTTP 200 for the documentation page, not an HTTP 404 response. A redirect fails
without following it; review it rather than issuing another request automatically.
No arbitrary URL, provider key or model configuration is accepted.

## Boundaries and output

- Default execution is offline preflight: no transport construction, environment
  credential access, DNS, sockets or requests. `--execute` explicitly enables
  one public-source GET through the existing `BoundedHTTPClient` and
  `PublicHTTPTransport`.
- Maximum one client HTTP attempt, zero retries, zero redirects, ten-second
  total request budget and 256,000 response bytes. The transport selects the
  first validated public address; this diagnostic does not add address fallback.
- Existing public-address checks, DNS pinning, certifi certificate/hostname
  verification, response framing/encoding checks and socket cleanup remain.
  No Mesh, Brave, source extraction, agent calls or research evidence is created.
- Output contains fixed status/category/stage fields, attempts, limits and the
  validated hostname only. It excludes full URLs, paths, queries, fragments,
  credentials, headers, response bodies and raw exceptions. Unknown CLI
  arguments are rejected without echoing their values. No artifact is written
  automatically; the operator may retain the sanitized terminal JSON privately.
- `stages_reached` separately records worker scheduling, DNS, TCP, TLS, sending,
  response headers and response body. `true` means observed stage entry, not
  completion or a latency measurement; `false` means not observed. The failure
  stage is the last observed operation, not proof of an underlying network cause.
  All markers belong to the same attempt; there are no separate DNS/TLS probes.
- On DNS failure, `source_hostname` identifies the syntax-validated requested
  hostname, not a successfully resolved public address. Unsafe DNS answers omit
  hostname metadata and are rejected before connecting.
- HTTP 200 demonstrates a complete bounded HTTP response from this endpoint,
  not citation correctness, Mesh/Brave readiness or full research success. Any
  non-200, timeout, security failure or malformed/oversized response fails closed.
- The ten-second limit bounds the awaited exchange, not guaranteed process-exit
  time. OS DNS workers and executor shutdown may take longer. No credits are
  consumed at Mesh/Brave by this command; ordinary network access is still an
  external action requiring separate live authorization.

## PowerShell 5.1 commands for operator review

No keys are needed. Run offline preflight first:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
.\.venv\Scripts\python.exe -m deepdive_ai.source_connectivity
if ($LASTEXITCODE -ne 0) { throw 'Source diagnostic preflight failed; stop.' }
```

Only after reviewing the ready preflight and separately authorizing one public
source request, run once:

```powershell
.\.venv\Scripts\python.exe -m deepdive_ai.source_connectivity --execute
if ($LASTEXITCODE -ne 0) { throw 'Source diagnostic failed; retain safe output and stop. Do not retry automatically.' }
```

Exit codes: 0 = ready preflight or successful HTTP 200; 1 = failed diagnostic;
2 = invalid arguments; 130 = interruption (attempt count may be unknown).
Never use this diagnostic's success as authorization for a paid workflow run.
The existing dashboard on port 8000 does not need to be restarted or modified.

Offline verification: all 271 Python tests passed, including 12 new diagnostic
tests. All six browser scenarios, Python compilation, JavaScript syntax,
PowerShell 5.1 parsing of both command blocks, whitespace and publication
security checks passed. Live reachability remains unverified.
