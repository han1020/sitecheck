"""Synchronize final Excel rows with the persistence API without overwriting edits."""
from __future__ import annotations

import os
import tempfile
from datetime import timezone, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .datetime_parser import extract_window
from .notice_api import NoticeApi, NoticeApiError
from .notice_identity import (CELL_FIELDS, CHECK_TYPES, identity_from_cells,
                              identity_hash, norm_identity, notice_key)
from .notice_state import NoticeState

KST = timezone(timedelta(hours=9))
ID_COL = 8  # Hidden H column; visible business data remains B:G.
LIMITS = (20, 10, 100, 200, 200, 200)


def configured_sync() -> tuple[NoticeApi, NoticeState] | None:
    api = NoticeApi.from_env()
    if api is None:
        return None
    env = os.environ.get("SITECHECK_API_ENV", "")
    if not env or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in env):
        raise ValueError("SITECHECK_API_ENV is required and must use letters, digits, _ or -")
    root = Path(__file__).resolve().parent.parent
    return api, NoticeState(root / "output" / "state" / env / "notice_state.json", env)


def item_from_cells(cells: list[Any]) -> dict[str, Any]:
    item = identity_from_cells(cells)
    if item["checkType"] not in CHECK_TYPES:
        raise ValueError("구분은 일반점검 또는 정기점검이어야 합니다.")
    if not all(item[name] for name in ("institutionCode", "institutionName", "scheduleText")):
        raise ValueError("기관코드·기관명·일시는 필수입니다.")
    if any(len(item[name]) > limit for name, limit in zip(CELL_FIELDS, LIMITS)):
        raise ValueError("엑셀 값이 DB 컬럼 길이를 초과했습니다.")
    win = extract_window(item["scheduleText"])
    start = win.start if win else None
    end = win.end if win else None
    if start and end and start > end:
        raise ValueError("시작 시각이 종료 시각보다 늦습니다.")
    for dt in (start, end):
        if dt and dt.microsecond:
            raise ValueError("점검 시각의 소수 초는 저장할 수 없습니다.")
    item["windowStart"] = start.replace(tzinfo=KST).isoformat() if start else None
    item["windowEnd"] = end.replace(tzinfo=KST).isoformat() if end else None
    return item


def _sheet(wb):
    return wb["점검"] if "점검" in wb.sheetnames else wb.worksheets[0]


def _cells(ws, row: int) -> list[str]:
    return ["" if ws.cell(row, col).value is None else str(ws.cell(row, col).value)
            for col in range(2, 8)]


def _write_item(ws, row: int, item: dict[str, Any]) -> None:
    for col, field in enumerate(CELL_FIELDS, 2):
        ws.cell(row, col).value = item[field]
    ws.cell(row, ID_COL).value = str(item["itemId"])
    ws.column_dimensions["H"].hidden = True


def save_atomic(wb, path: Path) -> None:
    fd, name = tempfile.mkstemp(prefix=".sitecheck-", suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(name)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def resolve_item(api: NoticeApi, state: NoticeState, item: dict[str, Any],
                 *, action: str | None = None, linked_id: str | None = None) -> dict[str, Any]:
    decision = state.decision_for(item)
    if action is None and decision:
        action = decision["action"]
        linked_id = decision.get("itemId")
    if action == "HOLD":
        return {"status": "HOLD", "candidates": api.candidates(item)}

    exact = api.lookup([{**{k: item[k] for k in CELL_FIELDS[:5]}, "clientRow": 1}])[0]
    if exact["status"] == "NOT_FOUND" and action not in ("NEW_ALLOW", "LINK"):
        candidates = api.candidates(item)
        if candidates:
            state.decide(item, "HOLD")
            return {"status": "NEEDS_DECISION", "candidates": candidates}
    request = dict(item)
    if action == "LINK":
        if not linked_id:
            raise ValueError("LINK 대상 ID가 없습니다.")
        request["linkedItemId"] = str(linked_id)
    response = api.create(request)
    if response["status"] == "LINKED_EXISTING" and not decision:
        state.decide(item, "LINK", str(response["item"]["itemId"]))
    return response


def sync_workbook(path: Path, api: NoticeApi, state: NoticeState) -> dict[str, Any]:
    try:
        api.lookup([])   # 연결·인증 사전 점검: 실패하면 행마다 재시도하며 매달리지 않고 바로 끝낸다
    except NoticeApiError as exc:
        return {"saved": 0, "suppressed": 0, "held": [],
                "errors": [{"excelRow": 0, "error": f"API 연결 실패: {exc}"}],
                "pendingOperations": len(state.pending())}
    wb = load_workbook(path)
    ws = _sheet(wb)
    result: dict[str, Any] = {"saved": 0, "suppressed": 0, "held": [], "errors": []}
    suppressed_rows: list[int] = []
    try:
        for row in range(3, ws.max_row + 1):
            cells = _cells(ws, row)
            if not any(cells):
                continue
            try:
                item = item_from_cells(cells)
                existing_id = row_item_id(ws, row)
                decision = state.decision_for(item)
                resolved = resolve_item(api, state, item,
                                        action="LINK" if existing_id and not decision else None,
                                        linked_id=existing_id if existing_id and not decision else None)
                status = resolved["status"]
                if status in ("HOLD", "NEEDS_DECISION"):
                    result["held"].append({"excelRow": row, "status": status,
                                           "candidates": resolved.get("candidates", [])})
                    continue
                if status == "SUPPRESSED_DELETED":
                    suppressed_rows.append(row)
                    result["suppressed"] += 1
                    continue
                stored = resolved["item"]
                if identity_hash(item) != identity_hash(stored):
                    state.decide(item, "LINK", str(stored["itemId"]))
                _write_item(ws, row, stored)
                result["saved"] += 1
            except (ValueError, NoticeApiError, KeyError) as exc:
                result["errors"].append({"excelRow": row, "error": str(exc)})
        for row in reversed(suppressed_rows):
            ws.delete_rows(row, 1)
        if result["saved"] or suppressed_rows:
            save_atomic(wb, path)
    finally:
        wb.close()
    result["pendingOperations"] = len(state.pending())
    return result


def row_item_id(ws, row: int) -> str | None:
    value = ws.cell(row, ID_COL).value
    return str(value) if value else None


def same_identity(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return identity_hash(a) == identity_hash(b) and all(
        norm_identity(a[field]) == norm_identity(b[field]) for field in CELL_FIELDS[:5])


def append_row(path: Path, cells: list[Any], api: NoticeApi, state: NoticeState,
               *, action: str | None = None, linked_id: str | None = None) -> dict[str, Any]:
    item = item_from_cells(cells)
    resolved = resolve_item(api, state, item, action=action, linked_id=linked_id)
    if resolved["status"] in ("HOLD", "NEEDS_DECISION"):
        return {"ok": False, "needs_decision": True,
                "candidates": resolved.get("candidates", [])}
    if resolved["status"] == "SUPPRESSED_DELETED":
        return {"ok": False, "error": "삭제된 공지는 다시 등록할 수 없습니다."}
    stored = resolved["item"]
    if action in ("LINK", "NEW_ALLOW"):
        state.decide(item, action, linked_id)
    elif identity_hash(item) != identity_hash(stored):
        state.decide(item, "LINK", str(stored["itemId"]))
    wb = load_workbook(path)
    try:
        ws = _sheet(wb)
        for row in range(3, ws.max_row + 1):
            existing_cells = _cells(ws, row)
            if row_item_id(ws, row) == str(stored["itemId"]) or (
                any(existing_cells) and same_identity(identity_from_cells(existing_cells), item)
            ):
                _write_item(ws, row, stored)
                save_atomic(wb, path)
                return {"ok": True, "duplicate": True, "excel_row": row,
                        "item_id": str(stored["itemId"])}
        ws.insert_rows(3)
        _write_item(ws, 3, stored)
        thin = Side(style="thin", color="000000")
        border = Border(top=thin, bottom=thin, left=thin, right=thin)
        for col in range(2, 8):
            cell = ws.cell(3, col)
            cell.font = Font(name="Calibri", size=11)
            cell.border = border
            cell.alignment = Alignment(horizontal="center" if col < 5 else "left",
                                       vertical="center", wrap_text=True)
            cell.fill = PatternFill("solid", fgColor="FFFF00")
        ws.row_dimensions[3].height = 32
        save_atomic(wb, path)
    finally:
        wb.close()
    return {"ok": True, "excel_row": 3, "item_id": str(stored["itemId"]),
            "status": resolved["status"]}


def update_row(path: Path, row: int, cells: list[Any], api: NoticeApi,
               state: NoticeState, *, allow_schedule_create: bool = False) -> dict[str, Any]:
    if len(cells) != 6:
        raise ValueError("엑셀의 6개 값을 모두 전달해야 합니다.")
    new_item = item_from_cells(cells)
    wb = load_workbook(path)
    try:
        ws = _sheet(wb)
        if row < 3 or row > ws.max_row:
            raise ValueError("잘못된 행 번호입니다.")
        item_id = row_item_id(ws, row)
        if not item_id:
            raise ValueError("DB 항목 ID가 없습니다. 먼저 해당 엑셀을 API와 동기화하세요.")
        old_item = identity_from_cells(_cells(ws, row))
        changed_schedule = norm_identity(old_item["scheduleText"]) != norm_identity(new_item["scheduleText"])
        if changed_schedule and not allow_schedule_create:
            return {"ok": False, "schedule_change": True,
                    "error": "일시가 달라지면 새 공지로 등록합니다. 기존 공지는 유지됩니다."}
        if changed_schedule:
            operation = {"kind": "CREATE_AFTER_SCHEDULE_CHANGE", "file": path.name,
                         "row": row, "oldItemId": item_id, "payload": new_item}
        else:
            changes = {field: new_item[field] for field in CELL_FIELDS
                       if old_item[field] != new_item[field]}
            if "scheduleText" in changes or "checkType" in changes:
                changes.update(windowStart=new_item["windowStart"],
                               windowEnd=new_item["windowEnd"])
            if not changes:
                return {"ok": True, "updated": 0, "item_id": item_id}
            operation = {"kind": "PATCH", "file": path.name, "row": row,
                         "itemId": item_id, "payload": changes}
        op_id = state.begin(operation)
        try:
            response = api.create(new_item) if changed_schedule else api.patch(item_id, changes)
        except NoticeApiError as exc:
            if exc.status and exc.status < 500:
                state.finish(op_id)
            raise
        if response["status"] == "SUPPRESSED_DELETED":
            state.finish(op_id)
            raise ValueError("삭제된 공지는 다시 등록할 수 없습니다.")
        stored = response["item"]
        if identity_hash(old_item) != identity_hash(stored) and not changed_schedule:
            if notice_key(old_item) == notice_key(stored):
                state.decide(old_item, "LINK", str(stored["itemId"]))
            else:
                state.decide(old_item, "HOLD")
        _write_item(ws, row, stored)
        save_atomic(wb, path)
        state.finish(op_id)
        return {"ok": True, "updated": 1, "item_id": str(stored["itemId"]),
                "new_notice": changed_schedule}
    finally:
        wb.close()


def delete_row(path: Path, row: int, api: NoticeApi, state: NoticeState) -> dict[str, Any]:
    wb = load_workbook(path)
    try:
        ws = _sheet(wb)
        if row < 3 or row > ws.max_row:
            raise ValueError("잘못된 행 번호입니다.")
        item_id = row_item_id(ws, row)
        if not item_id:
            raise ValueError("DB 항목 ID가 없습니다. 먼저 해당 엑셀을 API와 동기화하세요.")
        op_id = state.begin({"kind": "DELETE", "file": path.name, "row": row,
                             "itemId": item_id})
        try:
            api.delete(item_id)
        except NoticeApiError as exc:
            if exc.status and exc.status < 500:
                state.finish(op_id)
            raise
        removed = 0
        for current in range(ws.max_row, 2, -1):
            if row_item_id(ws, current) == item_id:
                ws.delete_rows(current, 1)
                removed += 1
        save_atomic(wb, path)
        state.finish(op_id)
        return {"ok": True, "deleted": removed, "item_id": item_id}
    finally:
        wb.close()


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Sync one SiteCheck workbook with the API")
    parser.add_argument("--file", type=Path, help="Workbook to sync; default is the latest")
    args = parser.parse_args()
    sync = configured_sync()
    if sync is None:
        parser.error("SITECHECK_API_BASE_URL is required")
    excel_dir = Path(__file__).resolve().parent.parent / "output" / "excel"
    files = sorted(excel_dir.glob("[[]사이트점검[]]_*.xlsx"), reverse=True)
    path = args.file or (files[0] if files else None)
    if path is None or not path.is_file():
        parser.error("No workbook found")
    result = sync_workbook(path, *sync)
    print(json.dumps({"file": str(path), **result}, ensure_ascii=False, indent=2))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
