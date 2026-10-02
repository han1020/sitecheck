"""Shared identity rules for the SiteCheck API contract."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

FIELDS = ("checkType", "institutionCode", "institutionName", "scheduleText", "serviceText")
CHECK_TYPES = ("일반점검", "정기점검")
CELL_FIELDS = (*FIELDS, "reasonText")


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\t\n\v\f\r ")


def norm_identity(value: Any) -> str:
    return unicodedata.normalize("NFC", norm_text(value).casefold())


def identity_hash(item: Mapping[str, Any]) -> str:
    values = [norm_identity(item[field]) for field in FIELDS]
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def identity_from_cells(cells: Sequence[Any]) -> dict[str, str]:
    return {name: norm_text(cells[i] if i < len(cells) else None)
            for i, name in enumerate(CELL_FIELDS)}


def notice_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(norm_identity(item[field]) for field in
                 ("checkType", "institutionCode", "scheduleText"))
