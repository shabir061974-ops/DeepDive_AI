# Submission readiness and demonstration runbook

Developed by Chabir Ahamad Abusali.

## Current submission evidence

Latest verified code checkpoint is `ee50eb28a2ce8769664a86f13ab5bad26458181a` on
`master`. [Offline CI run 39494702168](https://github.com/shabir061974-ops/DeepDive_AI/actions/runs/39494702168)
passed for that checkpoint: **385 offline Python tests**, compilation, JavaScript
syntax checks and the six-scenario browser smoke test. From that checkpoint, **one
complete live five-agent research run passed** with report status `verified`
(details below). The six separate scripted self-evaluation cases measure
deterministic offline correctness, not live research performance; their recorded
results remain unchanged.

## Live run evidence

On October 20, 2026 (20:52 UTC) the operator authorized one bounded live run of
`python -m deepdive_ai.live --execute` at `ee50eb2`, with
`DEEPDIVE_DNS_RESOLVER=doh` set for that PowerShell session only.

| Item | Result |
| --- | --- |
| Run ID | `56943dceac174beebd53e9b10573344c` |
| Question | "According to MDN, how does the HTTP 405 Method Not Allowed response differ from HTTP 501 Not Implemented?" |
| Model | `google/gemini-2.5-flash` via Mesh; model identity and no-fallback routing checks passed on every response; no cache hit |
| Agents | Planner, Researcher, Drafter, Verifier and Synthesizer all completed (audit: started → planned → researched → drafted → verified → completed) |
| Requests | Mesh 4 (Planner, Drafter, Verifier ×2), Brave 1, source 1; profile limits 7/2/4; no research retry |
| Duration | 25.8 s process wall time; 24.9 s between the first and last audit events; limit 120 s |
| Report status | `verified`; 2 of 2 drafted claims supported |
| Citation | Both claims cite <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/501>, extracted text characters 0–2000, with SHA-256 provenance for the page, extracted text and excerpt |
| Cost | At most about USD 0.02: Brave 1 × USD 0.005, plus Mesh at operator-approved rates (USD 0.30 input / 2.50 output per million tokens) under the 1,024-output-token cap. Upper-bound estimate only; actual billing not verified |

Verified claims, each with an exact quote from the retrieved excerpt:

1. "A 501 Not Implemented response is appropriate when the server does not
   recognize the request method and cannot support it for any resource."
2. "If a server recognizes a method but intentionally does not allow it, the
   appropriate response is 405 Method Not Allowed."

Private run files (`manifest.json`, `audit.jsonl`, `outcome.json`, `report.json`)
remain in ignored `reports/live/56943dceac174beebd53e9b10573344c/` and are not published.

**Limitations.** This is one successful run, not a reliability measure. Five earlier
October 20 attempts (table below) and the older `f1728f28…` attempt failed closed at different stages, and model-output
variability remains. Semantic support was judged by the live Mesh Verifier;
quotes were checked programmatically as exact substrings, but no person
independently reviewed the claims, and no accuracy metric is claimed. Evidence came
from a single source and one 2,000-character excerpt; the second allowed Brave
request and further source requests were not used. The Brave/Mesh account prices
are operator-attested, not verified.

Earlier October 20 attempts, all from approved commits, all within limits and all
stopped by existing safeguards rather than weakened ones:

| Run | Stopped at | Cause and later fix |
| --- | --- | --- |
| `a8bfd225b93b4ab5a883399a461232dc` | Planner response | Mesh cache hit without routing metadata, rejected as `cached_response_unverified`; later runs used distinct questions |
| `3e9c61faf37844d8b21a743f27dcfc2b` | Drafter | `malformed_agent_json`; Drafter and Verifier now accept one whole-document JSON fence, with shape diagnostics (`8188a02`) |
| `beb6b6a8b55a428eacb63427bbaadd8c` | Planner | `list_limit_exceeded` (more than one task); one-task instruction clarified (`b062ebf`) |
| `bc64041bbec74a939753dd9351b99d70` | Drafter | `text_limit_exceeded`; Drafter/Verifier instructions now state the configured text limit (`fd140a7`) |
| `8a61c25a8e0c47ccb3aa61726bc0909d` | Verifier, second claim | DoH lookup timed out at both endpoints; bounded TTL cache of DoH answers added (`ee50eb2`) |

Before these runs, slow cold DNS on the server (about 12 s, above the 10 s
request deadline) was addressed by the optional DNS-over-HTTPS resolver (`38bf0f2`)
without changing Windows networking.

Use the [fresh-checkout quickstart](../README.md#fresh-checkout-quickstart),
[six-scenario table](../README.md#six-offline-scenarios),
[execution modes and output limits](../README.md#execution-modes-limits-and-outputs)
and [private configuration guide](private_configuration.md) for current operation.
The video script is maintained below; no separate video artifact or submission
URL has been verified here.

## Historical final-sprint starting checkpoint

This subsection records the earlier final sprint, not the latest checkpoint.
Its older commit and CI evidence remain valid only for that historical state.

The final sprint started from clean local `master` at
`1e181bdc6083c87cf164381fec7e2f6f963b8951`. Public GitHub API reads confirmed
that exact commit was published on `origin/master`, not just present locally.
Its [Offline CI run 38929566748](https://github.com/shabir061974-ops/DeepDive_AI/actions/runs/38929566748)
completed successfully. This is evidence for Phase 9, independently of Phase 8.

Verified repository: [shabir061974-ops/DeepDive_AI](https://github.com/shabir061974-ops/DeepDive_AI).
Verified starting [commit](https://github.com/shabir061974-ops/DeepDive_AI/commit/1e181bdc6083c87cf164381fec7e2f6f963b8951).
Do not call a later commit verified until its own CI run succeeds.

## Architecture and verified behavior

The live entry point composes existing implementations in this order:

1. Planner asks Mesh for bounded structured tasks.
2. Researcher discovers candidates through Brave, retrieves public HTTP content,
   and extracts bounded text with source URL, timestamp, original digest and
   explicit extraction provenance.
3. Drafter asks Mesh for atomic claims referencing supplied evidence IDs.
4. Verifier asks Mesh for per-evidence semantic assessments and exact quotes.
   Missing support, fabricated references/quotes and contradictions prevent
   acceptance. Insufficient evidence triggers at most one research-again loop.
5. Synthesizer copies accepted claim text and supporting citations exactly.
   A metadata audit and final report/outcome are persisted locally.

All application LLM requests use Mesh. Researcher retrieval and deterministic
synthesis do not add LLM requests. Mocked HTTP integration tests execute the
real adapters and all five agents, including contradictions, empty discovery,
seven-call exhaustion, malformed agent JSON, persistence failures and deadlines.
The offline dashboard uses authored fixtures/scripted assessments through the
real workflow; it is not a demonstration of independent model accuracy.

The historical final sprint corrected a diagnostic defect: an enforced inner HTTP timeout
could previously lose its stage/category through cancellation and sanitized
agent errors. It now retains fixed safe diagnostics without changing limits.
External cancellation stays distinguishable from a classified provider timeout.
Existing audit/report durability limits and trusted-local-directory/Windows ACL
requirements remain documented in [live research](live_research.md).

## Live versus offline: honest presentation

| Capability | Evidence/status |
| --- | --- |
| Local dashboard, six scenarios, citations, contradictions, retry/audit views | Automated offline/browser checks |
| Full real-adapter five-agent orchestration | Mocked HTTP integration tests |
| Complete live five-agent research | One run passed: `56943dce…`, report `verified`, 2/2 claims supported with MDN citations ([evidence](#live-run-evidence)) |
| Live Mesh routing, model identity and agent JSON contracts | Passed on all four Mesh responses in that run under unchanged strict checks |
| Live Brave discovery and source retrieval | Passed in that run, with DNS-over-HTTPS enabled for the session |
| Live reliability | Not established; five earlier October 20 attempts and the older `f1728f28…` attempt failed closed at different stages |
| Independent semantic accuracy | Not demonstrated; model-verified only, no metrics claimed |
| Historical blockers | Model-ID mismatch, missing fallback header (`f1728f28…`), Brave DNS timeouts and the attempts in the evidence table are historical |
| Official hackathon checker | Exact inactive template retained; not activated |

Do not use fixture results as live research. A green offline test does not remove
live-provider risk. Live preflight can validate configuration but cannot
authenticate a key, check account billing or establish model-ID equivalence. Use only
the [reviewed live commands and spending gates](live_research.md#future-powershell-commands-do-not-execute-during-preparation)
with separate live/spend authorization for every run, and use a distinct question
to avoid unverifiable Mesh cache hits. Header absence does not prove
fallback occurred. The support-confirmed Gemini equivalence does not authorize
arbitrary aliases; see the [identity/routing policy](mesh_model_identity.md). Do not repeat successful connectivity probes automatically.
A planner call may incur cost before a later stage fails; client bounds are not
a hard dollar ceiling on gateway work.

## Demo rehearsal checklist

- Install Python 3.12 with `pip install -e '.[backend]'` in a new `.venv`.
  Use the [fresh-checkout quickstart](../README.md#fresh-checkout-quickstart);
  no keys are needed for the demo. An existing working installation need not be rebuilt.
- Close credential terminals, private reports and unrelated browser tabs before
  recording. Do not display environment variables, provider dashboards or keys.
- Reuse the existing loopback dashboard. Only if no instance is running, start:

  ```powershell
  .\.venv\Scripts\python.exe -m uvicorn deepdive_ai.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
  ```

- Open http://127.0.0.1:8000/ and confirm the prominent offline fixture label and
  exact footer attribution. No public hosted URL is claimed.
- Run **Supported evidence**: show actual agent sequence, accepted claim,
  inline citation and source provenance. Counts are run counts, not accuracy.
- Run **Conflicting sources**: show rejected/uncertain claims, contradictions,
  bounded research-again events and audit trail. Do not hide a needs-review result.
- Run **Evidence gap resolved**: show the real retry sequence and accepted claim.
  Optionally show empty-evidence and malformed-assessment safe failure states.
- Export Markdown and confirm it retains the offline notice and citations.
- Show a passing automated test/CI result for the exact recorded commit, while
  explaining that provider responses in integration tests are mocked.
- If mentioning live results, state exactly one completed live run with two
  model-verified claims, and that reliability and accuracy are not established.
  Do not display private report files or credentials. Do not execute
  the live runner during recording without separate approval. If you started a
  disposable recording server, stop only that instance afterward; leave an
  existing dashboard untouched.

## Concise YouTube demonstration script

Suggested length: about three minutes; this is an internal rehearsal suggestion,
not a verified organizer duration requirement.

**Opening:** "I'm Chabir Ahamad Abusali. DeepDive AI is a self-verifying research
analyst with five agents and traceable evidence. This recording uses the
explicitly labeled offline fixture demo; it performs no live research."

**Supported scenario:** "The planner creates tasks, the researcher gathers
evidence, the drafter proposes claims, the verifier checks them, and the
synthesizer includes accepted claims with citations. These are real backend
steps driven by authored fixtures and scripted model assessments. Here is the
authored fixture source identifier, extraction provenance and content hash behind
this claim. This is traceability within the demonstration, not a live citation."

**Conflict scenario:** "A citation alone does not establish truth. This scenario
contains contradictory evidence. The system preserves the contradiction,
limits follow-up research, and keeps uncertain claims out of the final accepted
summary. The audit trail shows the actual decisions."

**Recovery/export:** "This evidence-gap scenario shows the bounded research-again
loop. The exported Markdown retains citations and the offline demonstration
notice. We do not invent accuracy scores or claim that these fixtures are live
web findings."

**Engineering/close:** "The live runner uses Mesh for every LLM request and Brave
for discovery, with public-source retrieval, strict JSON contracts, request
budgets and private persistent audit/report files. In one authorized live run,
all five agents completed against a real MDN page: two claims about HTTP 405 and
501 were verified with exact quotes and citations, using four Mesh requests and
one Brave search in about 26 seconds. Earlier attempts failed safely, and each
cause was fixed without weakening a check. One run is not a reliability or
accuracy measure, and the official Agent Arena checker has not yet been
activated. Offline integration tests and GitHub CI cover the orchestration with
mocked providers."

## Completed evidence and outstanding submission items

- [x] Existing public repository identified; no new repository is needed.
- [x] Checkpoint `ee50eb2` has the successful Offline CI evidence linked above.
- [x] One bounded, authorized live five-agent run completed with report status
  `verified` (run `56943dce…`); see [live run evidence](#live-run-evidence).
- [x] Recorded offline workflow and six browser scenarios cover the demo behavior.
- [x] Developer attribution and explicit offline labeling are present.

The following remain open; completed baseline checks do not complete these items:

- [ ] Use the existing public repository URL above; record the final verified
  commit and its successful CI run after publication. Do not create another repo.
- [ ] Rehearse and record the demo; no verified YouTube URL is recorded here.
  Upload/publish the video yourself and verify the intended audience can view it.
- [ ] Confirm the actual organizer submission portal, form fields, video duration,
  required attachments and eligibility rules. These were not independently
  retrieved during this session; do not infer them from an OIDC audience string.
- [ ] Confirm the operator-supplied deadline: October 20, 2026 at 8:30 PM Kuwait
  time. This document records the supplied deadline, not a newly verified rule.
- [ ] If the organizer requires more live evidence than one completed run,
  separately authorize each further bounded run with its spend. Never claim a
  run not performed, and do not present one run as a reliability measure.
- [ ] Review official checker activation separately. The supplied
  [official workflow URL](https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml)
  was retrieved in Phase 8; the exact local template hash is
  `f34c4aba59caa82bd992a3fb77b51c0c94d3d2c3551acb55f72ddaf4e6e91436`.
  It requires OIDC permissions, `SUBMISSION_TOKEN`, `MESH_API_KEY`, downloads
  protected code and reports to the organizer. It has not been activated, granted
  secrets or executed. Do not enable it without separate approval and review of
  checker behavior/possible spend. Confirm the current organizer requirement;
  Offline CI alone is not evidence of official acceptance.
- [ ] Review the final publication for secrets and private outputs. Preserve the
  exact developer attribution and offline labels. Choose licensing terms if
  redistribution rights are intended, after checking organizer requirements;
  no license has been invented or added.
- [ ] Complete the organizer's submission manually; no form, email, outreach,
  payment, checker activation or hosting deployment is authorized by this sprint.

No guaranteed hackathon eligibility or submission completeness is claimed while
these organizer, video and checker items remain unresolved.

## Historical final-sprint local verification

These are retained results from the earlier sprint, not current suite counts.
Use the baseline evidence at the top for the latest documented checkpoint.

- Complete suite: **241 tests passed in 9.888 seconds**, zero failures/errors/
  skips, including the original 238 tests and three new diagnostic regressions.
- Focused real-adapter mocked integration/persistence suite: **20 tests passed**,
  including verified report/provenance, contradictions, retries, seven-request
  bound, invalid JSON/citations/quotes, storage errors and deadline cancellation.
- Browser smoke: all six offline scenarios, citations, contradictions, retries,
  error/empty states, new Markdown download, desktop/mobile layout and exact
  attribution passed; zero browser errors or external page requests.
- Python compilation, both JavaScript syntax checks and Git whitespace checks
  passed. Live preflight passed using explicit offline credential fixtures with
  a guard forbidding real transport construction; it did not authenticate keys.
- All 68 publishable files were scanned for common credential/private-key
  patterns and private runtime artifact paths: no suspected matches. Ignored
  artifacts and reports are excluded. The official template's exact SHA-256
  was checked and it remains inactive. No actual key values were accessed.

SPA: **APPROVED WITH NOTES** for the scoped offline/publication changes. The
notes are the unchanged trusted-local-output/Windows ACL boundary, partial-file
behavior on storage failure and absence of a hard dollar ceiling. These do not
authorize providers or certify a live research run. The successful existing CI
workflow and dashboard are unchanged. Publication of this sprint is conditional
on these local gates and a separate successful CI run for its resulting commit.
