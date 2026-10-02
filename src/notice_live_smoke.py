"""Read-only probe and opt-in write smoke test against a deployed SiteCheck API."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .config_loader import load_regular
from .notice_api import NoticeApi, NoticeApiError
from .notice_sync import configured_sync

KST = timezone(timedelta(hours=9))


def _regular_lookup(api: NoticeApi) -> None:
    path = Path(__file__).resolve().parent.parent / "config" / "regular_maintenance.yaml"
    rows = load_regular(path)
    request = [
        {"clientRow": index, "checkType": "정기점검", "institutionCode": row.code,
         "institutionName": row.name, "scheduleText": row.schedule,
         "serviceText": row.service}
        for index, row in enumerate(rows, 1)
    ]
    results = api.lookup(request)
    if len(results) != len(rows):
        raise RuntimeError("Regular lookup returned a different number of rows")
    counts: dict[str, int] = {}
    for result in results:
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
    print(f"Regular YAML lookup ({len(rows)} rows): {counts}")
    if counts.get("FOUND") != len(rows):
        raise RuntimeError("Some regular YAML notices are missing from the API")


def _candidate_probe(api: NoticeApi) -> None:
    row = load_regular(Path(__file__).resolve().parent.parent /
                       "config" / "regular_maintenance.yaml")[0]
    try:
        candidates = api.candidates({"checkType": "정기점검",
                                     "institutionCode": row.code,
                                     "scheduleText": row.schedule})
    except NoticeApiError as exc:
        if exc.status in (404, 405):
            print(f"Candidate GET: unavailable (HTTP {exc.status}); new-key auto sync remains blocked")
            return
        raise
    print(f"Candidate GET: OK ({len(candidates)} candidates)")


def _write_probe(api: NoticeApi) -> None:
    token = uuid4().hex.upper()
    start = (datetime.now(KST) + timedelta(days=1)).replace(
        hour=1, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    item = {
        "checkType": "일반점검",
        "institutionCode": "TST" + token[:7],
        "institutionName": "SiteCheck API 연동 시험",
        "scheduleText": start.strftime("%Y.%m.%d") + " 01:00 ~ 02:00",
        "serviceText": "API 연동 시험 업무",
        "reasonText": "연동 시험 " + token,
        "windowStart": start.isoformat(timespec="seconds"),
        "windowEnd": end.isoformat(timespec="seconds"),
    }
    lookup = {key: item[key] for key in (
        "checkType", "institutionCode", "institutionName", "scheduleText", "serviceText")}
    before = api.lookup([{"clientRow": 1, **lookup}])[0]
    if before["status"] != "NOT_FOUND":
        raise RuntimeError(f"New test identity is not free: {before['status']}")

    created_id: str | None = None
    deleted = False
    try:
        created = api.create(item)
        if created["status"] != "INSERTED":
            raise RuntimeError(f"Expected INSERTED, got {created['status']}")
        created_id = str(created["item"]["itemId"])
        print(f"POST create: INSERTED itemId={created_id}")

        imported = api.import_items([{"clientRow": 1, **item}])[0]
        if imported["status"] != "EXISTING" or str(imported["item"]["itemId"]) != created_id:
            raise RuntimeError(f"Import dedup failed: {imported['status']}")
        print("POST import same identity: EXISTING")

        changed_reason = "담당자 수정 확인 " + token
        patched = api.patch(created_id, {"reasonText": changed_reason})
        if patched["status"] != "UPDATED" or patched["item"]["reasonText"] != changed_reason:
            raise RuntimeError(f"PATCH result mismatch: {patched['status']}")
        print("PATCH reason: UPDATED")

        preserved = api.create(item)
        if (preserved["status"] != "REASON_PRESERVED" or
                preserved["item"]["reasonText"] != changed_reason):
            raise RuntimeError(f"Reason preservation failed: {preserved['status']}")
        print("POST old reason again: REASON_PRESERVED")

        removed = api.delete(created_id)
        if removed["status"] != "DELETED":
            raise RuntimeError(f"DELETE result mismatch: {removed['status']}")
        deleted = True
        print("DELETE: DELETED")

        after = api.lookup([{"clientRow": 1, **lookup}])[0]
        if after["status"] != "DELETED" or str(after["item"]["itemId"]) != created_id:
            raise RuntimeError(f"Deleted lookup mismatch: {after['status']}")
        suppressed = api.create(item)
        if (suppressed["status"] != "SUPPRESSED_DELETED" or
                str(suppressed["item"]["itemId"]) != created_id):
            raise RuntimeError(f"Deleted recreation mismatch: {suppressed['status']}")
        print("Deleted lookup/recreation: DELETED / SUPPRESSED_DELETED")
    finally:
        if created_id and not deleted:
            try:
                api.delete(created_id)
                print(f"Cleanup: soft-deleted itemId={created_id}")
            except NoticeApiError as exc:
                print(f"Cleanup failed for itemId={created_id}: {exc}")
    if created_id:
        print(f"Test row itemId={created_id} remains soft-deleted in the local DB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the local SiteCheck storage API")
    parser.add_argument("--write", action="store_true",
                        help="Create, patch and soft-delete one disposable test notice")
    args = parser.parse_args()
    configured = configured_sync()
    if configured is None:
        parser.error("SITECHECK_API_BASE_URL is required")
    api, state = configured
    if args.write and state.environment not in ("test", "local"):
        parser.error("--write is allowed only with SITECHECK_API_ENV=test or local")
    if api.lookup([]) != []:
        raise RuntimeError("Empty lookup contract mismatch")
    print("Connection / empty lookup: OK")
    _regular_lookup(api)
    _candidate_probe(api)
    if args.write:
        _write_probe(api)


if __name__ == "__main__":
    main()
