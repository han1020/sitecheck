"""Durable local LINK/HOLD decisions and unfinished API/file operations."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .notice_identity import FIELDS, identity_hash, norm_identity


class NoticeState:
    def __init__(self, path: Path, environment: str) -> None:
        self.path = path
        self.environment = environment
        self.lock_path = path.with_suffix(".lock")

    @contextmanager
    def _locked(self, exclusive: bool) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schemaVersion": 1, "environment": self.environment,
                    "decisions": [], "pendingOperations": []}
        with self.path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("environment") != self.environment or data.get("schemaVersion") != 1:
            raise ValueError("Notice state environment/schema mismatch")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        fd, name = tempfile.mkstemp(prefix=".notice-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _change(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self._locked(True):
            data = self._read()
            result = fn(data)
            self._write(data)
            return result

    def decision_for(self, item: dict[str, Any]) -> dict[str, Any] | None:
        key = identity_hash(item)
        with self._locked(False):
            for decision in self._read()["decisions"]:
                if decision["inputIdentityHash"] == key and all(
                    norm_identity(decision["inputIdentity"][field]) == norm_identity(item[field])
                    for field in FIELDS
                ):
                    return decision.copy()
        return None

    def decide(self, item: dict[str, Any], action: str, item_id: str | None = None) -> None:
        if action not in {"LINK", "NEW_ALLOW", "HOLD"}:
            raise ValueError("Invalid decision")
        if action == "LINK" and not item_id:
            raise ValueError("LINK requires item ID")
        entry = {"inputIdentityHash": identity_hash(item),
                 "inputIdentity": {field: item[field] for field in FIELDS},
                 "action": action}
        if action == "LINK":
            entry["itemId"] = str(item_id)

        def update(data: dict[str, Any]) -> None:
            for old in data["decisions"]:
                if old["inputIdentityHash"] == entry["inputIdentityHash"] and any(
                    norm_identity(old["inputIdentity"][field]) != norm_identity(item[field])
                    for field in FIELDS
                ):
                    raise ValueError("Notice identity hash collision in local state")
            data["decisions"] = [old for old in data["decisions"]
                                 if old["inputIdentityHash"] != entry["inputIdentityHash"]]
            data["decisions"].append(entry)
        self._change(update)

    def begin(self, operation: dict[str, Any]) -> str:
        op_id = uuid.uuid4().hex
        def update(data: dict[str, Any]) -> None:
            data["pendingOperations"].append({"operationId": op_id, **operation})
        self._change(update)
        return op_id

    def finish(self, op_id: str) -> None:
        def update(data: dict[str, Any]) -> None:
            data["pendingOperations"] = [op for op in data["pendingOperations"]
                                         if op["operationId"] != op_id]
        self._change(update)

    def pending(self) -> list[dict[str, Any]]:
        with self._locked(False):
            return list(self._read()["pendingOperations"])
