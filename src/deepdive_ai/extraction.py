"""Bounded HTML/PDF text artifacts with explicit transformation provenance."""

import asyncio
import base64
import codecs
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

from .models import Evidence, ExtractionProvenance
from .process_limits import WindowsJob
from .retrieval import RetrievedDocument


class ExtractionError(RuntimeError):
    """Sanitized extraction failure; no document text or subprocess diagnostics."""


@dataclass(frozen=True)
class ExtractionSettings:
    max_input_bytes: int = 2_000_000
    max_output_chars: int = 100_000
    max_nodes: int = 50_000
    max_depth: int = 128
    max_pages: int = 20
    max_page_chars: int = 20_000
    max_stream_bytes: int = 8_000_000
    memory_mb: int = 256
    cpu_seconds: int = 5
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "timeout_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
                    raise ValueError("Extraction timeout must be finite and at most 60 seconds")
            elif type(value) is not int or value < 1:
                raise ValueError("Extraction limits must be positive integers")
        if self.max_input_bytes > 8_000_000 or self.max_output_chars > 1_000_000:
            raise ValueError("Extraction IPC limits exceeded")
        if not 64 <= self.memory_mb <= 1024 or self.cpu_seconds > 30:
            raise ValueError("PDF worker resource limits are invalid")
        if self.max_depth > 256 or self.max_pages > 100:
            raise ValueError("Document structure limits are invalid")


@dataclass(frozen=True)
class TextArtifact:
    text: str
    original_content_sha256: str
    method: str
    transformations: tuple[str, ...]
    page_number: int | None = None

    def evidence(self, evidence_id: str, source_id: str, task_id: str, start: int, end: int) -> Evidence:
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(self.text):
            raise ExtractionError("Invalid extracted-text span")
        provenance = ExtractionProvenance(self.original_content_sha256,
            hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            hashlib.sha256(self.text[start:end].encode("utf-8")).hexdigest(), self.method,
            self.transformations, start, end, self.page_number)
        prefix = f"PDF page {self.page_number}; " if self.page_number is not None else "HTML; "
        locator = f"{prefix}extracted text chars {start}:{end}; artifact sha256={provenance.extracted_text_sha256}; {self.method}"
        return Evidence(evidence_id, source_id, task_id, self.text[start:end], locator, provenance)


class ContentExtractor(Protocol):
    async def extract(self, document: RetrievedDocument) -> tuple[TextArtifact, ...]:
        ...


def _encoding(name: str) -> str:
    try:
        canonical = codecs.lookup(name.strip()).name
    except (LookupError, AttributeError):
        raise ExtractionError("Source encoding is unsupported") from None
    if canonical not in {"utf-8", "utf-8-sig", "cp1252", "iso8859-1", "utf-16", "utf-16-le", "utf-16-be"}:
        raise ExtractionError("Source encoding is unsupported")
    return canonical


def _decode_html(document: RetrievedDocument) -> tuple[str, str]:
    content = document.content
    declarations = []
    if document.charset:
        declarations.append(_encoding(document.charset))
    bom = None
    if content.startswith(codecs.BOM_UTF8):
        bom = "utf-8-sig"
    elif content.startswith(codecs.BOM_UTF16_LE):
        bom = "utf-16-le"
    elif content.startswith(codecs.BOM_UTF16_BE):
        bom = "utf-16-be"
    if bom:
        declarations.append(bom)
    # HTML metadata only; arbitrary body text containing 'charset=' is not a declaration.
    for tag in re.findall(br"<meta\b[^>]{0,1024}>", content[:4096], flags=re.I):
        match = re.search(br"charset\s*=\s*[\"']?\s*([a-zA-Z0-9_-]+)", tag, flags=re.I)
        if match:
            declarations.append(_encoding(match[1].decode("ascii")))
    compatible = {name.replace("utf-8-sig", "utf-8") for name in declarations}
    if "utf-16" in compatible and bom in {"utf-16-le", "utf-16-be"}:
        compatible.remove("utf-16")
    if len(compatible) > 1:
        raise ExtractionError("Source encoding declarations conflict")
    encoding = bom or (declarations[0] if declarations else "utf-8")
    if encoding.startswith("utf-16") and not bom:
        raise ExtractionError("UTF-16 source requires a byte-order mark")
    try:
        decoded = content.decode(encoding, errors="strict").lstrip("\ufeff")
    except UnicodeError:
        raise ExtractionError("Source bytes do not match their declared encoding") from None
    if any(ord(c) < 32 and c not in "\t\r\n" for c in decoded):
        raise ExtractionError("HTML contains unsupported control characters")
    return decoded, encoding


class _ReadableHTML(HTMLParser):
    DROP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "template", "svg", "canvas", "iframe", "object", "head"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    BLOCK = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li", "tr", "td", "th", "table", "blockquote", "pre", "br", "hr"}
    BOILERPLATE = {"navigation", "navbar", "sidebar", "cookie", "cookies", "cookie-banner", "cookie-consent", "advert", "advertisement", "ads", "social", "share", "menu", "breadcrumb", "breadcrumbs"}
    OPTIONAL = {"html", "body", "p", "li", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "option", "colgroup"}

    def __init__(self, settings: ExtractionSettings):
        super().__init__(convert_charrefs=True)
        self.settings = settings
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[tuple[str, bool]] = []
        self.nodes = 0
        self.characters = 0
        self.tags = 0

    def _node(self) -> None:
        self.nodes += 1
        if self.nodes > self.settings.max_nodes:
            raise ExtractionError("HTML node limit exceeded")

    def _append(self, text: str) -> None:
        if any(blocked for _, blocked in self.stack):
            return
        self.characters += len(text)
        if self.characters > self.settings.max_output_chars:
            raise ExtractionError("HTML output limit exceeded")
        selected = any(tag in {"main", "article"} for tag, _ in self.stack)
        self.parts.append((text, selected))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._node()
        self.tags += 1
        if tag in self.OPTIONAL and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        values = {key: value or "" for key, value in attrs}
        tokens = set(re.split(r"[\s_]+", values.get("class", "").lower())) | {values.get("id", "").lower()}
        blocked = (tag in self.DROP or bool(tokens & self.BOILERPLATE) or "hidden" in values
                   or values.get("aria-hidden", "").lower() == "true"
                   or values.get("role", "").lower() in {"navigation", "banner", "contentinfo", "complementary"}
                   or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", values.get("style", ""), re.I)))
        if tag in self.BLOCK:
            self._append("\n")
        if tag not in self.VOID:
            self.stack.append((tag, blocked))
            if len(self.stack) > self.settings.max_depth:
                raise ExtractionError("HTML nesting limit exceeded")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self._node()
        indexes = [i for i, entry in enumerate(self.stack) if entry[0] == tag]
        if not indexes:
            if tag not in self.VOID:
                raise ExtractionError("HTML has unmatched closing tags")
            return
        index = indexes[-1]
        if any(entry[0] not in self.OPTIONAL for entry in self.stack[index + 1:]):
            raise ExtractionError("HTML has malformed nesting")
        del self.stack[index:]
        if tag in self.BLOCK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        self._node()
        self._append(data)

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() != "doctype html":
            raise ExtractionError("HTML declarations are unsupported")

    def unknown_decl(self, data: str) -> None:
        raise ExtractionError("HTML declarations are unsupported")

    def result(self) -> str:
        if not self.tags:
            raise ExtractionError("HTML has no parsed document elements")
        if any(tag not in self.OPTIONAL or blocked for tag, blocked in self.stack):
            raise ExtractionError("HTML has unterminated elements")
        selected = any(flag and part.strip() for part, flag in self.parts)
        parts = [part for part, flag in self.parts if flag or not selected]
        # Preserve block boundaries, while decoding entities and normalizing spacing.
        lines = [" ".join(line.split()) for line in "".join(parts).splitlines()]
        result = "\n".join(line for line in lines if line).strip()
        if not result:
            raise ExtractionError("HTML contains no meaningful static text")
        return result


class BoundedContentExtractor:
    def __init__(self, settings: ExtractionSettings = ExtractionSettings()):
        self.settings = settings
        self._pdf_gate = asyncio.Semaphore(1)

    async def extract(self, document: RetrievedDocument) -> tuple[TextArtifact, ...]:
        if len(document.content) > self.settings.max_input_bytes:
            raise ExtractionError("Document exceeds extraction byte limit")
        digest = hashlib.sha256(document.content).hexdigest()
        if digest != document.source.provenance.content_sha256:
            raise ExtractionError("Original source digest does not match extraction input")
        if document.media_type in {"text/html", "application/xhtml+xml"}:
            decoded, encoding = _decode_html(document)
            if re.search(r"<!\s*ENTITY\b|<!\s*DOCTYPE\s+[^>]*(?:SYSTEM|PUBLIC|\[)", decoded, re.I):
                raise ExtractionError("External HTML declarations are unsupported")
            parser = _ReadableHTML(self.settings)
            try:
                parser.feed(decoded)
                parser.close()
                extracted = parser.result()
            except ExtractionError:
                raise
            except Exception:
                raise ExtractionError("HTML extraction failed") from None
            return (TextArtifact(extracted, digest, "stdlib-htmlparser/readable-v1", (
                f"decode:{encoding}:strict", "remove-scripts-navigation-hidden-boilerplate",
                "prefer-main-or-article", "decode-html-entities", "normalize-block-whitespace")),)
        if document.media_type != "application/pdf":
            raise ExtractionError("Document type is unsupported by the content extractor")
        if not document.content.startswith(b"%PDF-") or b"%%EOF" not in document.content[-1024:]:
            raise ExtractionError("PDF signature or trailer is malformed")
        async with self._pdf_gate:
            result = await self._pdf(document.content)
        return tuple(TextArtifact(row["text"], digest, result["method"],
            ("pdf-text-operators-to-unicode", "plain-reading-order", "no-ocr-no-original-byte-map"), row["page_number"])
            for row in result["pages"])

    async def _pdf(self, content: bytes) -> dict:
        payload = json.dumps({"content": base64.b64encode(content).decode("ascii"),
                              "limits": asdict(self.settings)}).encode("utf-8")
        # Explicit allowlist excludes all Mesh/search credentials and inherited Python hooks.
        environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        process = None
        job = None
        try:
            process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "deepdive_ai.pdf_worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parents[2], env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if os.name == "nt":
                job = WindowsJob(int(process._handle), self.settings.memory_mb * 1024 * 1024, self.settings.cpu_seconds)
            raw, _ = await asyncio.to_thread(process.communicate, input=payload, timeout=self.settings.timeout_seconds)
            if process.returncode != 0 or len(raw) > self.settings.max_output_chars * 12 + 65536:
                raise ExtractionError("PDF worker rejected the document or exceeded resource limits")
            result = json.loads(raw)
            if (not isinstance(result, dict) or set(result) != {"method", "pages"}
                    or not isinstance(result["method"], str) or not result["method"].startswith("pypdf-")
                    or not isinstance(result["pages"], list) or not 1 <= len(result["pages"]) <= self.settings.max_pages):
                raise ExtractionError("PDF worker returned invalid extraction data")
            pages = result["pages"]
            seen = set()
            total = 0
            for row in pages:
                if (not isinstance(row, dict) or set(row) != {"page_number", "text"}
                        or type(row["page_number"]) is not int or not 1 <= row["page_number"] <= self.settings.max_pages
                        or row["page_number"] in seen or not isinstance(row["text"], str)
                        or not row["text"].strip() or len(row["text"]) > self.settings.max_page_chars):
                    raise ExtractionError("PDF worker returned invalid page text")
                row["text"].encode("utf-8")
                total += len(row["text"])
                seen.add(row["page_number"])
            if total > self.settings.max_output_chars:
                raise ExtractionError("PDF output limit exceeded")
            return result
        except asyncio.CancelledError:
            raise
        except ExtractionError:
            raise
        except Exception:
            raise ExtractionError("PDF extraction failed or resource limits unavailable") from None
        finally:
            if job is not None:
                job.close()  # Kill the entire job before closing pipes, including a venv child.
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    await asyncio.to_thread(process.wait, timeout=2)
                finally:
                    for pipe in (process.stdin, process.stdout):
                        if pipe is not None:
                            pipe.close()
