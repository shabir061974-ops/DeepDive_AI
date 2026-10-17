# Submission readiness and demonstration runbook

Developed by Chabir Ahamad Abusali.

## Current submission evidence

Current documentation baseline is `b8b6f90` on `master`. The previously verified
[Offline CI run 38954357490](https://github.com/shabir061974-ops/DeepDive_AI/actions/runs/38954357490)
passed for that exact checkpoint. The recorded baseline is 295 offline Python
tests and six browser scenarios passing; this documentation edit does not claim
a fresh test run, a new CI run or a completed live research run.

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
| Mesh connectivity | Operator-reported HTTP 200/text connectivity; not proof of all agent contracts |
| Planner, discovery and source retrieval | Operator-reported live run progressed through these stages to Drafter |
| Brave connectivity | Separately passed HTTP 200 according to operator evidence; earlier timeouts are historical |
| Drafter response validation | HTTP 200 rejected: requested `google/gemini-2.5-flash`, returned `gemini-2.5-flash`; exact model equality remains required |
| Drafter/Verifier live JSON contracts and final live report | Not established; Drafter's metadata rejection occurs before its agent JSON can be accepted |
| Full live research and independent semantic accuracy | Not demonstrated; no metrics claimed |
| Official hackathon checker | Exact inactive template retained; not activated |

Do not use fixture results as live research. A green offline test does not remove
live-provider blockers. Live preflight can validate configuration but cannot
authenticate a key, check account billing or establish model-ID equivalence. Use only
the [reviewed future live commands and spending gates](live_research.md#future-powershell-commands-do-not-execute-during-preparation)
after the identity contract is resolved and separate live/spend authorization.
Do not treat routing labels or the diagnostic display allowlist as authoritative
alias confirmation. Do not repeat successful connectivity probes automatically.
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
- State the model-ID blocker and unverified live stages clearly. Do not execute
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
budgets and private persistent audit/report files. The operator has separately
observed Mesh and Brave HTTP 200, and a live run reached Drafter. It stopped
because the returned model identifier did not exactly match the requested one.
We have not accepted an undocumented alias or completed a verified live report.
Offline integration tests and GitHub CI cover the orchestration with mocked
providers; they do not establish live research accuracy."

## Completed evidence and outstanding submission items

- [x] Existing public repository identified; no new repository is needed.
- [x] Baseline `b8b6f90` has the successful Offline CI evidence linked above.
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
- [ ] If actual live research is required for acceptance, separately authorize
  one bounded run after authoritative model-ID compatibility, remaining agent
  JSON contracts and spend exposure are reviewed. Never claim a run not performed.
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
these organizer, video, checker and live-validation items remain unresolved.

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
