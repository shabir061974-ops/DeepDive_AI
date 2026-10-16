"""Exclusive local artifacts; metadata audit writes are flushed before proceeding."""

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .audit import AuditEvent


def encode_record(record: object) -> str:
    def encode(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError("Unsupported artifact value")
    return json.dumps(record, default=encode, ensure_ascii=True, allow_nan=False)


def write_record(path: Path, record: object) -> None:
    payload = encode_record(record)
    with exclusive_stream(path) as stream:
        stream.write(payload + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def exclusive_stream(path: Path) -> TextIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


class JsonlAuditSink:
    """One run, exclusive file, bounded events; storage failures propagate."""

    def __init__(self, path: Path):
        self._stream = exclusive_stream(path)
        self._count = 0

    def emit(self, event: AuditEvent) -> None:
        if not isinstance(event, AuditEvent) or self._count >= 256:
            raise ValueError("Invalid audit event or event budget exhausted")
        line = encode_record(asdict(event))
        if len(line) > 8192:
            raise ValueError("Audit event exceeds storage limit")
        self._stream.write(line + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._count += 1

    def close(self) -> None:
        self._stream.close()
