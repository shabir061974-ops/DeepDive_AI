"""Private bounded PDF worker. Receives bytes over stdin; never opens source URLs."""

import base64
import contextlib
import io
import json
import logging
import socket
import sys

from .process_limits import apply_posix_limits


def _walk(root, max_nodes: int, max_depth: int) -> None:
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

    stack = [(root, 0)]
    seen = set()
    nodes = 0
    banned = {"/OpenAction", "/AA", "/A", "/JS", "/JavaScript", "/EmbeddedFiles", "/XFA",
              "/EF", "/Launch", "/RichMediaContent", "/RichMediaSettings"}
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise ValueError
        if isinstance(value, IndirectObject):
            key = ("reference", value.idnum, value.generation)
            if key in seen:
                continue
            seen.add(key)
            value = value.get_object()
        if isinstance(value, (DictionaryObject, ArrayObject)):
            key = ("object", id(value))
            if key in seen:
                continue
            seen.add(key)
        if isinstance(value, DictionaryObject):
            if banned.intersection(value) or value.get("/Type") in ("/Filespec", "/EmbeddedFile"):
                raise ValueError
            if isinstance(value, StreamObject) and "/F" in value:
                raise ValueError
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, ArrayObject):
            stack.extend((item, depth + 1) for item in value)


def extract(payload: dict) -> dict:
    limits = payload["limits"]
    apply_posix_limits(limits["memory_mb"] * 1024 * 1024, limits["cpu_seconds"])
    # No network operations are part of PDF parsing, including embedded links.
    def forbidden(*args, **kwargs):
        raise RuntimeError("PDF network access disabled")

    socket.socket = forbidden
    socket.getaddrinfo = forbidden
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader, __version__
    import pypdf.filters

    pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH = limits["max_stream_bytes"]
    content = base64.b64decode(payload["content"], validate=True)
    if (len(content) > limits["max_input_bytes"] or not content.startswith(b"%PDF-")
            or b"%%EOF" not in content[-1024:]):
        raise ValueError
    reader = PdfReader(io.BytesIO(content), strict=True, root_object_recovery_limit=1000)
    if reader.is_encrypted:
        raise ValueError
    _walk(reader.trailer, limits["max_nodes"], limits["max_depth"])
    if not 1 <= len(reader.pages) <= limits["max_pages"]:
        raise ValueError
    pages = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        stream = page.get_contents()
        if stream is not None and len(stream.get_data()) > limits["max_stream_bytes"]:
            raise ValueError
        count = 0

        def visit(text, cm, tm, font, size):
            nonlocal count
            count += len(text)
            if count > limits["max_page_chars"]:
                raise ValueError

        text = page.extract_text(visitor_text=visit, extraction_mode="plain")
        text.encode("utf-8", errors="strict")
        if len(text) > limits["max_page_chars"]:
            raise ValueError
        total += len(text)
        if total > limits["max_output_chars"]:
            raise ValueError
        if text.strip():
            pages.append({"page_number": number, "text": text})
    if not pages:
        raise ValueError
    return {"method": f"pypdf-{__version__}/plain-v1", "pages": pages}


def main() -> None:
    try:
        # Independent hard IPC limit, even if this private worker is invoked manually.
        raw = sys.stdin.buffer.read(12_000_001)
        if len(raw) > 12_000_000:
            raise ValueError
        payload = json.loads(raw)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = extract(payload)
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(encoded)
    except BaseException:
        sys.stdout.buffer.write(b'{"error":"PDF extraction failed"}')
        sys.exit(1)


if __name__ == "__main__":
    main()
