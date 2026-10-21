"""Write a sanitized GitHub Step Summary for one `deepdive_ai.live` run.

Reads only the CLI's JSON output and that run's own `audit.jsonl` and
`report.json`. Prints fixed codes, counts, allowlisted failure diagnostics
(category, transport stage, HTTP status, Mesh completion check, validated source
hostname), claim text,
verdicts and citation URLs; never prompts, raw model output, raw exceptions,
rationales, excerpts or credentials.
Model and source text is untrusted: it is cleaned, bounded and rendered only
inside literal code spans, so it cannot form Markdown, HTML, links, images,
mentions or autolinks. Missing or malformed files never raise.

Usage:
  python scripts/summarize_live_run.py --summary-file PATH [--preflight PATH]
      [--outcome PATH] [--reports-root reports/live]
"""

import argparse
import ipaddress
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit


MAX_FILE_BYTES = 2_000_000
MAX_TEXT_CHARS = 300
MAX_CLAIMS = 20
MAX_CITATIONS = 5
MAX_URL_CHARS = 300
MAX_BLOCKERS = 10
MAX_SUMMARY_CHARS = 60_000
CODE = re.compile(r"[a-z0-9_]{1,64}")
RUN_ID = re.compile(r"[0-9a-f]{32}")
# Audit events in workflow order; Synthesizer completion is the terminal `completed` event.
STAGES = (("Planner", "planned"), ("Researcher", "researched"), ("Drafter", "drafted"),
          ("Verifier", "verified"), ("Synthesizer", "completed"))
LIMITS = {"mesh": 7, "brave": 2, "source": 4}
UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
# Fixed values written by deepdive_ai.http_transport / deepdive_ai.live diagnostics.
FAILURE_CATEGORIES = frozenset({
    "dns_failure", "tls_failure", "network_failure", "timeout", "unsafe_destination", "response_too_large",
    "unsupported_encoding", "http_protocol_failure", "incomplete_response", "request_limit",
    "authentication_http_status", "rate_limited", "billing_http_status", "redirect_rejected",
    "provider_http_status", "unexpected_http_status", "response_parsing", "response_validation",
    "internal_error", "request_cancelled"})
TRANSPORT_STAGES = frozenset({"worker_queue", "dns", "tcp_connect", "tls_handshake", "send_request",
                              "response_headers", "response_body"})
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# deepdive_ai.mesh.MeshCompletionCheck values: which completion check a Mesh response failed.
MESH_COMPLETION_CHECKS = frozenset({
    "unsupported_content_type", "envelope_invalid", "choices_invalid", "finish_reason_length",
    "finish_reason_content_filter", "finish_reason_tool_calls", "finish_reason_other", "finish_reason_missing",
    "message_invalid", "tool_calls_present", "content_not_text", "content_empty"})
# deepdive_ai.agents.common AgentFailureLocation / AgentValueShape values.
FAILURE_LOCATIONS = frozenset({"whole_reply", "claim_row"})
VALUE_SHAPES = frozenset({"object", "array", "string", "number", "boolean", "null"})
MAX_FAILURE_ROW = 1000


def text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Untrusted text as one literal code span: never parsed as Markdown, HTML, links or mentions.

    Control, format (including bidirectional overrides), private-use and line/paragraph
    separator characters are removed, whitespace is collapsed to single spaces, and the
    span delimiter is longer than any backtick run inside the text.
    """
    if not isinstance(value, str):
        return "—"
    cleaned = "".join(" " if c in "\t\r\n" or unicodedata.category(c) == "Zs" else c for c in value)
    cleaned = " ".join("".join(c for c in cleaned if unicodedata.category(c) not in UNSAFE_CATEGORIES).split())
    if not cleaned:
        return "—"
    if len(cleaned) > limit:
        cleaned = cleaned[:limit - 1].rstrip() + "…"
    fence = "`" * (max((len(run) for run in re.findall(r"`+", cleaned)), default=0) + 1)
    return f"{fence} {cleaned} {fence}"


def code(value: object) -> str:
    return value if isinstance(value, str) and CODE.fullmatch(value) else "invalid"


def url(value: object) -> str:
    """Plain, non-clickable citation URL, or a fixed placeholder."""
    if not isinstance(value, str) or not 0 < len(value) <= MAX_URL_CHARS:
        return "(invalid URL)"
    try:
        parts = urlsplit(value)
    except ValueError:
        return "(invalid URL)"
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return "(invalid URL)"
    return text(value, MAX_URL_CHARS)


def allowed(value: object, values: frozenset[str]) -> str:
    if value is None:
        return "none"
    return value if isinstance(value, str) and value in values else "invalid"


def http_status(value: object) -> str:
    if value is None:
        return "none"
    return str(value) if type(value) is int and 100 <= value <= 599 else "invalid"


def hostname(value: object) -> str:
    """A validated public-looking hostname or IP literal as inert text; never a URL."""
    if not isinstance(value, str) or not 0 < len(value) <= 253:
        return "none" if value is None else "invalid"
    try:
        ipaddress.ip_address(value)
        return text(value, 253)
    except ValueError:
        pass
    labels = value.split(".")
    if len(labels) < 2 or not all(HOST_LABEL.fullmatch(label) for label in labels):
        return "invalid"
    return text(value, 253)


def diagnostic_rows(result: dict) -> list[str]:
    lines = []
    details = result.get("failure_details")
    if isinstance(details, dict):
        lines += ["", "### Failure details", "", "| Field | Value |", "| --- | --- |",
                  f"| Category | {allowed(details.get('category'), FAILURE_CATEGORIES)} |",
                  f"| Stage | {allowed(details.get('stage'), TRANSPORT_STAGES)} |",
                  f"| HTTP status | {http_status(details.get('http_status'))} |"]
        if "mesh_completion_check" in details:
            lines.append(f"| Mesh completion check | "
                         f"{allowed(details.get('mesh_completion_check'), MESH_COMPLETION_CHECKS)} |")
    diagnostics = result.get("diagnostics")
    rows = []
    if isinstance(diagnostics, dict):
        for provider in LIMITS:
            item = diagnostics.get(provider)
            if not isinstance(item, dict):
                continue
            host = hostname(item.get("source_hostname")) if provider == "source" else "&#8212;"
            rows.append(f"| {provider.capitalize()} | {allowed(item.get('category'), FAILURE_CATEGORIES)} | "
                        f"{allowed(item.get('stage'), TRANSPORT_STAGES)} | {http_status(item.get('http_status'))} | "
                        f"{host} |")
    if rows:
        lines += ["", "### Provider diagnostics", "", "| Provider | Category | Stage | HTTP status | Source host |",
                  "| --- | --- | --- | --- | --- |"] + rows
    return lines


def load_json(path: Path | None) -> object:
    try:
        if path is None or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=lambda _: None)
    except (OSError, ValueError, RecursionError):
        return None


def load_audit(path: Path) -> list[str]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return []
        kinds = []
        for line in path.read_text(encoding="utf-8-sig").splitlines()[:1000]:
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(record, dict) and isinstance(record.get("kind"), str):
                kinds.append(code(record["kind"]))
        return kinds
    except OSError:
        return []


def stage_rows(kinds: list[str], status: str) -> list[str]:
    rows = []
    failed_marked = False
    for agent, event in STAGES:
        if event in kinds:
            label = "&#9989; completed"
            count = kinds.count(event)
            if count > 1:
                label += f" ({count} rounds)"
        elif status == "failed" and not failed_marked:
            label, failed_marked = "&#10060; failed or stopped here", True
        else:
            label = "not reached"
        rows.append(f"| {agent} | {label} |")
    return rows


def records(container: dict, field: str) -> list[dict]:
    items = container.get(field)
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def index(items: list[dict], key: str) -> dict[str, dict]:
    return {item[key]: item for item in items if isinstance(item.get(key), str)}


def lookup(table: dict[str, dict], key: object) -> dict:
    return table.get(key, {}) if isinstance(key, str) else {}


def claim_rows(report: dict) -> list[str]:
    bundle = report.get("bundle") if isinstance(report.get("bundle"), dict) else {}
    sources = index(records(bundle, "sources"), "id")
    evidence = index(records(bundle, "evidence"), "id")
    verdicts = index(records(report, "verification"), "claim_id")
    claims = records(report, "claims")
    lines = []
    for index_number, claim in enumerate(claims[:MAX_CLAIMS], 1):
        verdict = lookup(verdicts, claim.get("id"))
        ids = verdict.get("evidence_ids") if isinstance(verdict.get("evidence_ids"), list) else []
        cited = []
        for evidence_id in ids[:MAX_CITATIONS * 4]:
            source = lookup(sources, lookup(evidence, evidence_id).get("source_id"))
            if source:
                provenance = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
                entry = f"{url(source.get('url'))} (retrieved {text(provenance.get('retrieved_at'), 40)})"
                if entry not in cited:
                    cited.append(entry)
        lines.append(f"{index_number}. **{code(verdict.get('status'))}**: {text(claim.get('text'))}")
        for entry in cited[:MAX_CITATIONS]:
            lines.append(f"   - Citation: {entry}")
        if not cited:
            lines.append("   - Citation: none recorded")
    if len(claims) > MAX_CLAIMS:
        lines.append(f"\n{len(claims) - MAX_CLAIMS} further claims not shown.")
    return lines or ["No claims were drafted."]


def build(preflight: object, outcome: object, reports_root: Path) -> str:
    lines = ["## DeepDive AI live research run", "",
             "> Genuine live run through Mesh and Brave. This summary is public. It shows fixed status "
             "codes, counts, claim text and citation URLs only; model and source text is shown as inert text.", ""]
    result = outcome if isinstance(outcome, dict) else preflight if isinstance(preflight, dict) else None
    if result is None:
        lines.append("No run output was recorded. Check the workflow step results.")
        return "\n".join(lines) + "\n"
    run_id = result.get("run_id") if isinstance(result.get("run_id"), str) and RUN_ID.fullmatch(result["run_id"]) else None
    status = code(result.get("status"))
    lines += ["| Field | Value |", "| --- | --- |",
              f"| Mode | {code(result.get('mode'))} |",
              f"| Run ID | {run_id or 'none'} |",
              f"| Status | {status} |",
              f"| Report status | {code(result.get('report_status')) if 'report_status' in result else 'none'} |"]
    for field in ("failure_category", "agent_failure_code", "agent_json_shape"):
        if field in result:
            lines.append(f"| {field.replace('_', ' ').capitalize()} | {code(result.get(field))} |")
    if "agent_failure_location" in result:
        lines.append(f"| Agent failure location | {allowed(result.get('agent_failure_location'), FAILURE_LOCATIONS)} |")
    if "agent_failure_row" in result:
        row = result.get("agent_failure_row")
        lines.append(f"| Agent failure row | {row if type(row) is int and 1 <= row <= MAX_FAILURE_ROW else 'invalid'} |")
    if "agent_value_shape" in result:
        lines.append(f"| Agent value shape | {allowed(result.get('agent_value_shape'), VALUE_SHAPES)} |")
    lines.append(f"| Cost status | {code(result.get('cost_status'))} |")
    blockers = result.get("blockers") if isinstance(result.get("blockers"), list) else []
    if blockers:
        lines += ["", "**Preflight blockers:** " + ", ".join(code(b) for b in blockers[:MAX_BLOCKERS])]
    attempts = result.get("attempts") if isinstance(result.get("attempts"), dict) else {}
    lines += ["", "### Requests", "", "| Provider | Used | Limit |", "| --- | --- | --- |"]
    for provider, limit in LIMITS.items():
        used = attempts.get(provider)
        lines.append(f"| {provider.capitalize()} | {used if type(used) is int and 0 <= used <= 1000 else 0} | {limit} |")
    lines += diagnostic_rows(result)
    if run_id is None:
        lines += ["", "No live execution was recorded, so no agent stages ran."]
        return "\n".join(lines) + "\n"
    folder = reports_root / run_id
    kinds = load_audit(folder / "audit.jsonl")
    lines += ["", "### Agent stages", "", "| Agent | Result |", "| --- | --- |"]
    lines += stage_rows(kinds, status) if kinds else ["| All | audit trail not available |"]
    retries = kinds.count("retry")
    if retries:
        lines.append(f"\nResearch-again rounds: {retries}")
    report = load_json(folder / "report.json")
    lines += ["", "### Claims and citations", ""]
    if isinstance(report, dict) and isinstance(report.get("report"), dict):
        lines += claim_rows(report["report"])
    else:
        lines.append("No final report was produced for this run.")
    summary = "\n".join(lines) + "\n"
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n\nSummary truncated at its size limit.\n"
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a sanitized live-run step summary.")
    parser.add_argument("--summary-file", required=True, type=Path)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--outcome", type=Path)
    parser.add_argument("--reports-root", type=Path, default=Path("reports") / "live")
    args = parser.parse_args(argv)
    summary = build(load_json(args.preflight), load_json(args.outcome), args.reports_root)
    try:
        with args.summary_file.open("a", encoding="utf-8") as stream:
            stream.write(summary)
    except OSError:
        print("Could not write the step summary.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
