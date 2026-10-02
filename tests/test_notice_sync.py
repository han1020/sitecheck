from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from openpyxl import load_workbook

from src.notice_api import NoticeApiError
from src.notice_identity import CELL_FIELDS, identity_hash, notice_key, norm_identity
from src.notice_state import NoticeState
from src.notice_sync import (append_row, delete_row, item_from_cells, sync_workbook,
                             update_row)


CELLS = ["일반점검", "KRBK0101", "저축은행중앙회",
         "2026.09.19(토) 00:00 ~ 06:00", "통합금융정보시스템 전체 업무", "수집 사유"]


class FakeApi:
    def __init__(self):
        self.items = {}
        self.next_id = 1

    def lookup(self, items):
        results = []
        for item in items:
            found = next((v for v in self.items.values()
                          if identity_hash(v) == identity_hash(item)), None)
            results.append({"status": "DELETED" if found and found["deletedAt"] else
                            "FOUND" if found else "NOT_FOUND", "item": deepcopy(found)})
        return results

    def candidates(self, item):
        return [deepcopy(v) for v in self.items.values() if notice_key(v) == notice_key(item)]

    def create(self, item):
        linked = item.get("linkedItemId")
        exact = self.lookup([item])[0]["item"]
        if linked:
            stored = self.items[linked]
            assert notice_key(stored) == notice_key(item)
            status = "LINKED_EXISTING"
        elif exact:
            stored = exact
            status = "EXISTING" if norm_identity(stored["reasonText"]) == norm_identity(item["reasonText"]) else "REASON_PRESERVED"
        else:
            item_id = str(self.next_id)
            self.next_id += 1
            stored = {key: item[key] for key in CELL_FIELDS}
            stored.update({"itemId": item_id, "windowStart": item["windowStart"],
                           "windowEnd": item["windowEnd"], "deletedAt": None})
            self.items[item_id] = stored
            status = "INSERTED"
        if stored["deletedAt"]:
            status = "SUPPRESSED_DELETED"
        return {"status": status, "item": deepcopy(stored)}

    def patch(self, item_id, changes):
        self.items[item_id].update(changes)
        return {"status": "UPDATED", "item": deepcopy(self.items[item_id])}

    def delete(self, item_id):
        self.items[item_id]["deletedAt"] = "2026-09-20T00:00:00+09:00"
        return {"status": "DELETED", "item": deepcopy(self.items[item_id])}


def state(tmp_path: Path) -> NoticeState:
    return NoticeState(tmp_path / "state" / "test" / "notice_state.json", "test")


def sheet_rows(path: Path):
    wb = load_workbook(path)
    try:
        ws = wb["점검"]
        return [[ws.cell(r, c).value for c in range(2, 9)] for r in range(3, ws.max_row + 1)]
    finally:
        wb.close()


def test_repeated_collection_keeps_one_row_and_preserves_reason(make_xlsx, tmp_path):
    api, local = FakeApi(), state(tmp_path)
    first = make_xlsx(carried=[CELLS], name="[사이트점검]_20260919.xlsx")
    assert sync_workbook(first, api, local)["saved"] == 1
    api.items["1"]["reasonText"] = "담당자 수정 사유"
    second = make_xlsx(carried=[CELLS], name="[사이트점검]_20260920.xlsx")
    assert sync_workbook(second, api, local)["saved"] == 1
    assert len(api.items) == 1
    assert sheet_rows(second)[0][5:] == ["담당자 수정 사유", "1"]


def test_candidate_needs_explicit_link(make_xlsx, tmp_path):
    api, local = FakeApi(), state(tmp_path)
    first = make_xlsx(carried=[CELLS])
    sync_workbook(first, api, local)
    changed = [*CELLS]
    changed[4] = "다르게 수집된 업무"
    second = make_xlsx(carried=[changed], name="[사이트점검]_20260924.xlsx")
    result = sync_workbook(second, api, local)
    assert result["saved"] == 0 and result["held"][0]["candidates"][0]["itemId"] == "1"
    assert local.decision_for(item_from_cells(changed))["action"] == "HOLD"
    local.decide(item_from_cells(changed), "LINK", "1")
    assert sync_workbook(second, api, local)["saved"] == 1
    assert sheet_rows(second)[0][4] == CELLS[4]
    assert len(api.items) == 1


def test_patch_keeps_old_input_link_and_future_collection(make_xlsx, tmp_path):
    api, local = FakeApi(), state(tmp_path)
    first = make_xlsx(carried=[CELLS])
    sync_workbook(first, api, local)
    edited = [*CELLS]
    edited[4] = "담당자가 고친 업무"
    assert update_row(first, 3, edited, api, local)["updated"] == 1
    assert local.decision_for(item_from_cells(CELLS))["itemId"] == "1"
    second = make_xlsx(carried=[CELLS], name="[사이트점검]_20260924.xlsx")
    assert sync_workbook(second, api, local)["saved"] == 1
    assert sheet_rows(second)[0][4] == edited[4]


def test_schedule_change_requires_confirmation_and_creates_new(make_xlsx, tmp_path):
    api, local = FakeApi(), state(tmp_path)
    path = make_xlsx(carried=[CELLS])
    sync_workbook(path, api, local)
    edited = [*CELLS]
    edited[3] = "2026.09.19(토) 00:00 ~ 07:00"
    assert update_row(path, 3, edited, api, local)["schedule_change"]
    assert len(api.items) == 1
    assert update_row(path, 3, edited, api, local, allow_schedule_create=True)["new_notice"]
    assert len(api.items) == 2
    assert sheet_rows(path)[0][6] == "2"


def test_deleted_item_is_suppressed_on_next_collection(make_xlsx, tmp_path):
    api, local = FakeApi(), state(tmp_path)
    first = make_xlsx(carried=[CELLS])
    sync_workbook(first, api, local)
    assert delete_row(first, 3, api, local)["deleted"] == 1
    assert sheet_rows(first) == []
    second = make_xlsx(carried=[CELLS], name="[사이트점검]_20260924.xlsx")
    assert sync_workbook(second, api, local)["suppressed"] == 1
    assert sheet_rows(second) == []
    assert len(api.items) == 1


def test_review_append_uses_identity_not_code_and_reason(make_xlsx, tmp_path):
    api, local = FakeApi(), state(tmp_path)
    path = make_xlsx()
    first = append_row(path, CELLS, api, local)
    assert first["ok"] and not first.get("duplicate")
    second = [*CELLS]
    second[3] = "2026.09.20(일) 00:00 ~ 06:00"
    assert append_row(path, second, api, local)["ok"]
    assert len(api.items) == 2
    assert append_row(path, CELLS, api, local)["duplicate"]
    assert len(sheet_rows(path)) == 2


def test_regular_schedule_without_date_has_null_windows(make_xlsx, tmp_path):
    regular = ["정기점검", "KRBK0101", "저축은행중앙회",
               "매월 셋째 주 일요일 02:00 ~ 04:00", "인터넷뱅킹", "정기 시스템 점검"]
    api, local = FakeApi(), state(tmp_path)
    path = make_xlsx(regular=[regular])
    assert sync_workbook(path, api, local)["saved"] == 1
    assert api.items["1"]["windowStart"] is None
    assert api.items["1"]["windowEnd"] is None


def test_web_edit_delete_and_sort_keep_hidden_id(make_xlsx, tmp_path, monkeypatch):
    from src import webserver

    api, local = FakeApi(), state(tmp_path)
    later = [*CELLS]
    later[3] = "2026.09.20(일) 00:00 ~ 06:00"
    path = make_xlsx(carried=[later, CELLS])
    sync_workbook(path, api, local)
    monkeypatch.setattr(webserver, "EXCEL_DIR", tmp_path)
    monkeypatch.setattr(webserver, "configured_sync", lambda: (api, local))
    row_to_id = {row[3]: row[6] for row in sheet_rows(path)}
    assert webserver.sort_excel_rows(path.name)["ok"]
    assert {row[3]: row[6] for row in sheet_rows(path)} == row_to_id
    first_id = sheet_rows(path)[0][6]
    edited = [str(v) for v in sheet_rows(path)[0][:6]]
    edited[5] = "웹에서 수정"
    assert webserver.update_excel_rows(path.name, [{"excel_row": 3, "cells": edited}])["ok"]
    assert api.items[first_id]["reasonText"] == "웹에서 수정"
    assert webserver.delete_excel_row(path.name, 3)["ok"]
    assert api.items[first_id]["deletedAt"]
    assert len(sheet_rows(path)) == 1


def test_web_review_append_requires_decision_for_candidate(make_xlsx, tmp_path, monkeypatch):
    from src import webserver

    api, local = FakeApi(), state(tmp_path)
    path = make_xlsx(carried=[CELLS])
    sync_workbook(path, api, local)
    monkeypatch.setattr(webserver, "EXCEL_DIR", tmp_path)
    monkeypatch.setattr(webserver, "configured_sync", lambda: (api, local))
    changed = [*CELLS]
    changed[4] = "검토 추가 업무"
    response = webserver.append_excel_row(path.name, changed)
    assert response["needs_decision"] and len(api.items) == 1
    linked = webserver.append_excel_row(path.name, changed, action="LINK", linked_id="1")
    assert linked["duplicate"]
    assert len(api.items) == 1


class DownApi(FakeApi):
    """API 서버가 죽어 있는 상황: 모든 호출이 연결 오류."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def lookup(self, items):
        self.calls += 1
        raise NoticeApiError("API connection failed: connection refused")

    def candidates(self, item):
        raise NoticeApiError("API connection failed: connection refused")

    def create(self, item):
        raise NoticeApiError("API connection failed: connection refused")


def test_sync_workbook_stops_early_when_api_unreachable(make_xlsx, tmp_path):
    api, local = DownApi(), state(tmp_path)
    later = [*CELLS]
    later[3] = "2026.09.20(일) 00:00 ~ 06:00"
    path = make_xlsx(carried=[CELLS, later])
    result = sync_workbook(path, api, local)
    assert result["saved"] == 0 and len(result["errors"]) == 1
    assert result["errors"][0]["excelRow"] == 0
    assert "API 연결 실패" in result["errors"][0]["error"]
    assert api.calls == 1                                   # 사전 점검 1회뿐, 행별 재시도 없음
    assert all(row[6] is None for row in sheet_rows(path))  # 워크북은 건드리지 않음


def test_collect_keeps_excel_and_run_json_when_api_down(tmp_path, monkeypatch):
    from src import webserver
    from src.scraper import NoticeHit

    api, local = DownApi(), state(tmp_path)
    (tmp_path / "excel").mkdir()
    monkeypatch.setattr(webserver, "EXCEL_DIR", tmp_path / "excel")
    monkeypatch.setattr(webserver, "JSON_DIR", tmp_path / "json")
    monkeypatch.setattr(webserver, "SCREENSHOT_DIR", tmp_path / "shots")
    monkeypatch.setattr(webserver, "configured_sync", lambda: (api, local))

    async def fake_scrape_all(**kwargs):
        return [NoticeHit(site_code=CELLS[1], site_name=CELLS[2], category="BK",
                          title="시스템 점검 안내", posted_date="2026-09-01", detail_url="",
                          screenshot_path="", window=None, schedule_text=CELLS[3],
                          service_text=CELLS[4], reason_text=CELLS[5])]
    monkeypatch.setattr(webserver, "scrape_all", fake_scrape_all)

    webserver._run_in_thread()                               # 웹 '지금 수집'이 부르는 함수
    assert webserver.RUN_STATE["status"] == "done"
    assert "DB 동기화 오류 1건" == webserver.RUN_STATE["error"]
    assert list((tmp_path / "excel").glob("*.xlsx"))          # 엑셀은 생성
    assert list((tmp_path / "json").glob("*.json"))           # 감지목록 이력도 남음
