"""사이트 점검 수집 결과를 웹에서 확인하는 간단한 대시보드 서버.

표준 라이브러리(http.server)만 사용하므로 추가 의존성이 없다.

기능:
  - 브라우저에서 '지금 수집' 버튼으로 수집 1회 실행 (백그라운드 스레드)
  - 수집 결과(감지된 점검 공지 + 에러 사이트)를 표로 확인
  - 캡처 이미지 썸네일/원문 링크 제공
  - 과거 실행 결과 이력 조회 (output/json/*.json)

실행:
    python serve.py                # http://127.0.0.1:8000
    python serve.py --port 9000
    python serve.py --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from openpyxl import load_workbook

from .carryover import load_previous_general
from .config_loader import load_keywords, load_regular, load_sites
from .excel_writer import (
    write_excel,
    _BORDER as _XL_BORDER,
    _CENTER as _XL_CENTER,
    _DATA_FONT as _XL_DATA_FONT,
    _LEFT_WRAP as _XL_LEFT_WRAP,
    _NEW_FILL as _XL_NEW_FILL,
)
from openpyxl.styles import PatternFill
from .keyword_matcher import is_maintenance
from .scraper import (
    DEFAULT_SERVICE_TEXT, NoticeHit, make_run_id, scrape_all,
    strip_already_collected,
)

logger = logging.getLogger("webserver")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
SCREENSHOT_DIR = OUTPUT_DIR / "screenshots"
EXCEL_DIR = OUTPUT_DIR / "excel"
JSON_DIR = OUTPUT_DIR / "json"

# ---------------------------------------------------------------------------
# 수집 실행 상태 (스레드 공유)
# ---------------------------------------------------------------------------
_run_lock = threading.Lock()
RUN_STATE: Dict[str, Any] = {
    "status": "idle",          # idle | running | done | error
    "run_id": "",
    "started": "",
    "finished": "",
    "count": 0,                # 감지된 점검 공지 수
    "review_count": 0,         # 검토 필요(본문 비어있음) 수
    "error_count": 0,          # 에러 사이트 수
    "error": "",
    "excel": "",
    "progress_done": 0,        # 진행률: 처리 완료한 사이트 수
    "progress_total": 0,       # 진행률: 전체 사이트 수
    "progress_site": "",       # 진행률: 현재 처리 중 사이트명
}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 직렬화
# ---------------------------------------------------------------------------
def _screenshot_url(path: str) -> str:
    """절대 스크린샷 경로 → /screenshots/<상대경로> URL."""
    if not path:
        return ""
    try:
        rel = Path(path).resolve().relative_to(SCREENSHOT_DIR.resolve())
    except (ValueError, OSError):
        return ""
    return "/screenshots/" + "/".join(rel.parts)


def hit_to_dict(h: NoticeHit) -> Dict[str, Any]:
    win = h.window
    return {
        "site_code": h.site_code,
        "site_name": h.site_name,
        "category": h.category,
        "title": h.title,
        "posted_date": h.posted_date,
        "detail_url": h.detail_url,
        "screenshot_url": _screenshot_url(h.screenshot_path),
        "schedule_text": h.schedule_text,
        "service_text": h.service_text,
        "reason_text": h.reason_text,
        "window_start": win.start.isoformat() if win and win.start else "",
        "window_end": win.end.isoformat() if win and win.end else "",
        "error": h.error,
        "needs_review": getattr(h, "needs_review", False),
        "via_ocr": getattr(h, "via_ocr", False),
        # 정기점검 baseline과 일시 일치 — 엑셀 신규 제외, 대시보드 체크 표시
        "is_regular": getattr(h, "is_regular", False),
        "regular_reason": getattr(h, "regular_reason", ""),
        # 의심값 플래그: 업무가 폴백 기본값 = 본문 라벨 추출 실패 가능성
        "service_default": h.service_text == DEFAULT_SERVICE_TEXT,
    }


def _save_run_json(run_id: str, hits: List[NoticeHit],
                   skips: Optional[List[Dict[str, Any]]] = None) -> Path:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    matched = [hit_to_dict(h) for h in hits if h.title and not h.needs_review]
    review = [hit_to_dict(h) for h in hits if h.needs_review]
    errors = [hit_to_dict(h) for h in hits if h.error]
    payload = {
        "run_id": run_id,
        "generated_at": _now_str(),
        "count": len(matched),
        "review_count": len(review),
        "error_count": len(errors),
        "skip_count": len(skips or []),
        "matched": matched,
        "review": review,
        "errors": errors,
        "skipped": skips or [],
    }
    out = JSON_DIR / f"{run_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _list_runs() -> List[Dict[str, Any]]:
    if not JSON_DIR.exists():
        return []
    runs = []
    for p in sorted(JSON_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        runs.append({
            "run_id": d.get("run_id", p.stem),
            "generated_at": d.get("generated_at", ""),
            "count": d.get("count", 0),
            "review_count": d.get("review_count", 0),
            "error_count": d.get("error_count", 0),
            "skip_count": d.get("skip_count", 0),
        })
    return runs


def _load_run(run_id: str) -> Optional[Dict[str, Any]]:
    p = JSON_DIR / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _delete_screenshot_file(screenshot_url: str) -> bool:
    """/screenshots/<상대경로> URL → 실제 파일 삭제. 삭제하면 True."""
    if not screenshot_url or not screenshot_url.startswith("/screenshots/"):
        return False
    parts = [unquote(p) for p in screenshot_url[len("/screenshots/"):].split("/") if p]
    if not parts:
        return False
    target = (SCREENSHOT_DIR / Path(*parts)).resolve()
    try:  # 디렉토리 탈출 방지
        target.relative_to(SCREENSHOT_DIR.resolve())
    except ValueError:
        return False
    if target.is_file():
        try:
            target.unlink()
            return True
        except OSError:
            return False
    return False


# JSON(run) 파일 쓰기 직렬화
_json_lock = threading.Lock()


def delete_matched_hit(run_id: str, index: int) -> Dict[str, Any]:
    """감지 목록에서 한 항목을 삭제하고 해당 스크린샷 파일도 함께 삭제.

    run JSON의 matched[index] 를 제거하고 count 를 갱신한다. 인덱스는 프론트가
    렌더한 matched 순서와 동일(삭제 후 목록을 다시 불러오므로 어긋나지 않음).
    """
    if not run_id:
        return {"ok": False, "error": "실행 ID가 없습니다."}
    p = JSON_DIR / f"{run_id}.json"
    if not p.exists():
        return {"ok": False, "error": "실행 기록을 찾을 수 없습니다."}
    with _run_lock:
        if RUN_STATE.get("status") == "running":
            return {"ok": False, "error": "수집 중에는 삭제할 수 없습니다. 잠시 후 다시 시도하세요."}
    with _json_lock:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"기록 읽기 실패: {e}"}
        matched = data.get("matched") or []
        if index < 0 or index >= len(matched):
            return {"ok": False, "error": "잘못된 항목 번호입니다. 목록을 새로고침하세요."}
        item = matched.pop(index)
        shot_deleted = _delete_screenshot_file(item.get("screenshot_url", ""))
        data["matched"] = matched
        data["count"] = len(matched)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        f"감지 항목 삭제: {run_id} [{index}] {item.get('site_code')} "
        f"{str(item.get('title',''))[:30]} | 스샷삭제={shot_deleted}"
    )
    return {"ok": True, "screenshot_deleted": shot_deleted}


def skip_review_hit(run_id: str, index: int) -> Dict[str, Any]:
    """검토 필요 항목을 스킵 처리: run JSON에서 제거 + 스샷 삭제 +
    영구 스킵 목록(config/review_skips.yaml)에 등록해 이후 수집에서 제외."""
    if not run_id:
        return {"ok": False, "error": "실행 ID가 없습니다."}
    p = JSON_DIR / f"{run_id}.json"
    if not p.exists():
        return {"ok": False, "error": "실행 기록을 찾을 수 없습니다."}
    with _run_lock:
        if RUN_STATE.get("status") == "running":
            return {"ok": False, "error": "수집 중에는 처리할 수 없습니다. 잠시 후 다시 시도하세요."}
    from .review_skips import add_review_skip
    with _json_lock:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"기록 읽기 실패: {e}"}
        review = data.get("review") or []
        if index < 0 or index >= len(review):
            return {"ok": False, "error": "잘못된 항목 번호입니다. 목록을 새로고침하세요."}
        item = review.pop(index)
        added = add_review_skip(
            CONFIG_DIR / "review_skips.yaml",
            item.get("site_code", ""), item.get("title", ""),
            item.get("posted_date", ""),
        )
        shot_deleted = _delete_screenshot_file(item.get("screenshot_url", ""))
        data["review"] = review
        data["review_count"] = len(review)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        f"검토 항목 스킵: {run_id} [{index}] {item.get('site_code')} "
        f"{str(item.get('title',''))[:30]} | 스킵목록 추가={added} | 스샷삭제={shot_deleted}"
    )
    return {"ok": True, "already_listed": not added, "screenshot_deleted": shot_deleted}


# ---------------------------------------------------------------------------
# 키워드 뷰 — keywords.yaml 을 인라인 주석(제외 사유)까지 파싱해 표시
# ---------------------------------------------------------------------------
_KW_SECTIONS = [
    ("include", "포함 키워드", "제목에 이 중 하나라도 있으면 점검 공지로 간주"),
    ("exclude", "제외 키워드 (제목+라벨)", "제목 또는 사유·업무 라벨 값에 이 단어가 있으면 제외 — 외부기관 안내 등"),
    ("exclude_title", "제외 키워드 (제목 전용)", "제목에서만 매칭 — 라벨 값에 정상 등장할 수 있는 단어용"),
    ("exclude_body", "제외 키워드 (본문)", "본문 전체에서 매칭하는 제외 키워드 (오탐 위험 큰 특이 문구만)"),
    ("external_orgs", "외부 조직 명부", "제목에 자기 기관명 없이 이 이름이 있으면 '남의 점검' 안내로 제외"),
    ("institution_aliases", "기관명 변형 명부", "sites.yaml 표기와 다르게 쓰이는 기관명 (외부기관 감지 명부에 합산)"),
]

_KW_HEAD_RE = re.compile(
    r"^(include|exclude_title|exclude_body|exclude|external_orgs|institution_aliases):"
    r"\s*(?:#.*)?$"
)


def parse_keywords_raw() -> Dict[str, Any]:
    """keywords.yaml 을 파싱해 섹션별 [{keyword, note}] 반환 (인라인 # 주석 포함)."""
    path = CONFIG_DIR / "keywords.yaml"
    buckets: Dict[str, List[Dict[str, str]]] = {k: [] for k, _, _ in _KW_SECTIONS}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"sections": [], "error": "keywords.yaml 을 읽을 수 없습니다."}
    cur: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = _KW_HEAD_RE.match(line)
        if head:
            cur = head.group(1)
            continue
        item = re.match(r"^\s*-\s*(.+)$", line)
        if item and cur:
            body = item.group(1)
            keyword, note = body, ""
            if "#" in body:
                keyword, note = body.split("#", 1)
            keyword, note = keyword.strip(), note.strip()
            if keyword:
                buckets[cur].append({"keyword": keyword, "note": note})
    sections = [
        {"key": k, "title": title, "hint": hint, "items": buckets[k]}
        for k, title, hint in _KW_SECTIONS
    ]
    return {"sections": sections, "path": "config/keywords.yaml"}


def regular_baseline() -> Dict[str, Any]:
    """regular_maintenance.yaml 의 정기점검 baseline을 표시용으로 반환."""
    try:
        entries = load_regular(CONFIG_DIR / "regular_maintenance.yaml")
    except Exception as e:  # noqa: BLE001
        return {"items": [], "error": f"regular_maintenance.yaml 읽기 실패: {e}"}
    return {
        "path": "config/regular_maintenance.yaml",
        "items": [
            {"code": r.code, "name": r.name, "schedule": r.schedule,
             "service": r.service, "reason": r.reason}
            for r in entries
        ],
    }


_REGULAR_HEADER = """\
# 정기점검 baseline
#
# 스크래핑으로 신규(일반)점검을 감지하지 못해도, 아래 항목은 엑셀에 항상 포함됩니다.
# 기관에 정기점검 일정이 추가/변경되면 이 파일을 수정하거나
# 웹 대시보드 '정기점검' 탭에서 직접 편집하세요. (다음 수집부터 반영)
"""

# regular_maintenance.yaml 쓰기 직렬화
_regular_lock = threading.Lock()


def update_regular(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """정기점검 baseline 전체를 items 로 교체해 YAML 재작성.

    items = [{code, name, schedule, service, reason}]. 값은 YAML 이스케이프를
    위해 JSON 문자열(= 유효한 YAML 더블쿼트 스칼라)로 기록한다.
    저장 전 yaml 재파싱 + load_regular 검증으로 파일이 깨지는 것을 막는다.
    """
    cleaned: List[Dict[str, str]] = []
    for i, it in enumerate(items or []):
        row = {k: str(it.get(k) or "").strip()
               for k in ("code", "name", "schedule", "service", "reason")}
        if not any(row.values()):
            continue  # 완전 빈 행은 무시
        if not row["code"] or not row["name"] or not row["schedule"]:
            return {"ok": False,
                    "error": f"{i + 1}행: 기관코드·기관명·일시는 비울 수 없습니다."}
        cleaned.append(row)
    if not cleaned:
        return {"ok": False, "error": "저장할 항목이 없습니다. (전체 삭제는 파일을 직접 편집하세요)"}

    lines = [_REGULAR_HEADER, "regular:"]
    for row in cleaned:
        lines.append(f"  - code: {json.dumps(row['code'], ensure_ascii=False)}")
        lines.append(f"    name: {json.dumps(row['name'], ensure_ascii=False)}")
        lines.append(f"    schedule: {json.dumps(row['schedule'], ensure_ascii=False)}")
        lines.append(f"    service: {json.dumps(row['service'], ensure_ascii=False)}")
        lines.append(f"    reason: {json.dumps(row['reason'], ensure_ascii=False)}")
        lines.append("")
    content = "\n".join(lines)

    path = CONFIG_DIR / "regular_maintenance.yaml"
    with _regular_lock:
        try:  # 저장 전 검증: 재파싱 가능해야 함
            import yaml as _yaml
            parsed = _yaml.safe_load(content)
            assert len(parsed.get("regular", [])) == len(cleaned)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"YAML 생성 검증 실패: {e}"}
        path.write_text(content, encoding="utf-8")
        try:  # 실제 로더로도 읽히는지 최종 확인
            load_regular(path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"저장 후 로드 실패: {e}"}
    logger.info(f"정기점검 baseline 저장: {len(cleaned)}건")
    return {"ok": True, "saved": len(cleaned)}


# ---------------------------------------------------------------------------
# 엑셀뷰 — 생성된 엑셀 파일을 그대로 읽어 표로 렌더
# ---------------------------------------------------------------------------
_EXCEL_PREFIX = "[사이트점검]_"

# '점검' 시트 레이아웃 (excel_writer.py 와 동일)
_XL_HEADER_ROW = 2
_XL_DATA_START_COL = 2   # B열
_XL_NCOLS = 6            # 구분/기관코드/기관명/일시/업무/사유

# 엑셀 파일 쓰기 직렬화 (동시 수정/수집 충돌 방지)
_excel_lock = threading.Lock()


def _excel_path(filename: str) -> Optional[Path]:
    """검증된 엑셀 파일 경로 반환 (잘못된 이름/경로면 None)."""
    if not filename.startswith(_EXCEL_PREFIX) or not filename.endswith(".xlsx"):
        return None
    if "/" in filename or "\\" in filename:
        return None
    path = (EXCEL_DIR / filename).resolve()
    try:
        path.relative_to(EXCEL_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _list_excels() -> List[Dict[str, Any]]:
    if not EXCEL_DIR.exists():
        return []
    # '[사이트점검]_' 의 대괄호가 glob 문자클래스로 해석되므로 iterdir 로 필터링
    paths = [
        p for p in EXCEL_DIR.iterdir()
        if p.is_file() and p.name.startswith(_EXCEL_PREFIX)
        and p.name.endswith(".xlsx") and not p.name.startswith("~$")
    ]
    items = []
    for p in sorted(paths, key=lambda x: x.name, reverse=True):
        items.append({"filename": p.name, "date": p.stem.replace(_EXCEL_PREFIX, "")})
    return items


def _is_yellow(cell) -> bool:
    """신규 강조(노란색 FFFF00) 셀인지."""
    f = getattr(cell, "fill", None)
    if not f or getattr(f, "patternType", None) != "solid":
        return False
    rgb = getattr(getattr(f, "fgColor", None), "rgb", None)
    return isinstance(rgb, str) and rgb.upper().endswith("FFFF00")


def _read_excel(filename: str) -> Optional[Dict[str, Any]]:
    """엑셀 '점검' 시트를 {columns, rows[{excel_row,cells,is_new}], meta} 로 변환."""
    path = _excel_path(filename)
    if path is None:
        return None

    wb = load_workbook(path, data_only=True)
    ws = wb["점검"] if "점검" in wb.sheetnames else wb.worksheets[0]

    columns = [
        (ws.cell(row=_XL_HEADER_ROW, column=_XL_DATA_START_COL + i).value or "")
        for i in range(_XL_NCOLS)
    ]

    rows: List[Dict[str, Any]] = []
    for r in range(_XL_HEADER_ROW + 1, ws.max_row + 1):
        cells = []
        is_new = False
        nonempty = False
        for i in range(_XL_NCOLS):
            cell = ws.cell(row=r, column=_XL_DATA_START_COL + i)
            val = "" if cell.value is None else str(cell.value)
            if val.strip():
                nonempty = True
            if _is_yellow(cell):
                is_new = True
            cells.append(val)
        if not nonempty:
            continue
        rows.append({"excel_row": r, "cells": cells, "is_new": is_new})

    # 실행정보 시트(있으면) 메타 추출
    meta: Dict[str, str] = {}
    if "실행정보" in wb.sheetnames:
        m = wb["실행정보"]
        for r in range(1, 7):
            k = m.cell(row=r, column=1).value
            v = m.cell(row=r, column=2).value
            if k is not None:
                meta[str(k)] = "" if v is None else str(v)

    wb.close()
    return {"filename": filename, "columns": columns, "rows": rows, "meta": meta}


def update_excel_rows(filename: str, edits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """엑셀 셀 값 수정. edits=[{excel_row:int, cells:[6 values]}]. 스타일은 보존."""
    path = _excel_path(filename)
    if path is None:
        return {"ok": False, "error": "파일을 찾을 수 없습니다."}
    with _run_lock:
        if RUN_STATE.get("status") == "running":
            return {"ok": False, "error": "수집 중에는 편집할 수 없습니다. 잠시 후 다시 시도하세요."}
    with _excel_lock:
        wb = load_workbook(path)  # 스타일 보존 위해 data_only=False
        ws = wb["점검"] if "점검" in wb.sheetnames else wb.worksheets[0]
        max_row = ws.max_row
        n = 0
        for e in edits:
            r = int(e.get("excel_row", 0))
            cells = e.get("cells") or []
            if r <= _XL_HEADER_ROW or r > max_row:
                continue
            for i in range(min(_XL_NCOLS, len(cells))):
                ws.cell(row=r, column=_XL_DATA_START_COL + i).value = cells[i]
            n += 1
        wb.save(path)
        wb.close()
    logger.info(f"엑셀 편집 저장: {filename} ({n}행)")
    return {"ok": True, "updated": n}


def delete_excel_row(filename: str, excel_row: int) -> Dict[str, Any]:
    """엑셀에서 한 행 삭제 (이후 행은 위로 당겨짐)."""
    path = _excel_path(filename)
    if path is None:
        return {"ok": False, "error": "파일을 찾을 수 없습니다."}
    with _run_lock:
        if RUN_STATE.get("status") == "running":
            return {"ok": False, "error": "수집 중에는 삭제할 수 없습니다. 잠시 후 다시 시도하세요."}
    with _excel_lock:
        wb = load_workbook(path)
        ws = wb["점검"] if "점검" in wb.sheetnames else wb.worksheets[0]
        if excel_row <= _XL_HEADER_ROW or excel_row > ws.max_row:
            wb.close()
            return {"ok": False, "error": "잘못된 행 번호입니다."}
        ws.delete_rows(excel_row, 1)
        wb.save(path)
        wb.close()
    logger.info(f"엑셀 행 삭제: {filename} (row {excel_row})")
    return {"ok": True}


def append_excel_row(filename: str, cells: List[Any]) -> Dict[str, Any]:
    """엑셀 '점검' 시트 맨 위(헤더 바로 아래)에 한 행 추가. 검토 탭 '공지추가' 버튼용.

    cells = [구분, 기관코드, 기관명, 일시, 업무, 사유] (부족하면 빈칸 보충).
    검토에서 넣은 행은 신규 감지분과 동일하게 노란색으로 표시한다.
    """
    path = _excel_path(filename)
    if path is None:
        return {"ok": False, "error": "엑셀 파일을 찾을 수 없습니다. 먼저 수집을 실행하세요."}
    with _run_lock:
        if RUN_STATE.get("status") == "running":
            return {"ok": False, "error": "수집 중에는 추가할 수 없습니다. 잠시 후 다시 시도하세요."}

    vals = [("" if c is None else str(c)) for c in (cells or [])][:_XL_NCOLS]
    vals += [""] * (_XL_NCOLS - len(vals))

    with _excel_lock:
        wb = load_workbook(path)  # 스타일 보존
        ws = wb["점검"] if "점검" in wb.sheetnames else wb.worksheets[0]
        # 중복 방지: 같은 (기관코드, 사유) 행이 이미 있으면 추가하지 않음
        code_col = _XL_DATA_START_COL + 1   # C열: 기관코드
        reason_col = _XL_DATA_START_COL + 5  # G열: 사유
        for r in range(_XL_HEADER_ROW + 1, ws.max_row + 1):
            ex_code = ws.cell(row=r, column=code_col).value
            ex_reason = ws.cell(row=r, column=reason_col).value
            if (str(ex_code or "").strip() == vals[1].strip()
                    and str(ex_reason or "").strip() == vals[5].strip()
                    and vals[1].strip()):
                wb.close()
                logger.info(f"엑셀 공지추가 중복 스킵: {filename} | {vals[1]} {vals[5][:30]}")
                return {"ok": True, "duplicate": True, "excel_row": r}
        # 헤더 바로 아래(맨 위)에 삽입 — 기존 행은 아래로 밀림(값·스타일 보존됨)
        new_row = _XL_HEADER_ROW + 1
        ws.insert_rows(new_row)
        for i in range(_XL_NCOLS):
            c = ws.cell(row=new_row, column=_XL_DATA_START_COL + i, value=vals[i])
            c.font = _XL_DATA_FONT
            c.border = _XL_BORDER
            c.alignment = _XL_CENTER if i < 3 else _XL_LEFT_WRAP
            c.fill = _XL_NEW_FILL
        ws.row_dimensions[new_row].height = 32
        wb.save(path)
        wb.close()
    logger.info(f"엑셀 공지추가(맨 위): {filename} row {new_row} | {vals}")
    return {"ok": True, "excel_row": new_row}


def sort_excel_rows(filename: str) -> Dict[str, Any]:
    """'점검' 시트의 '일반점검' 행끼리만 (일시 → 기관코드) 오름차순 정렬.

    정기점검 등 그 외 행은 원래 순서 그대로 뒤에 유지. 셀 값과 배경색을 보존한다.
    일시는 'YYYY.MM.DD…' 형식이라 문자열 비교로 시간순 정렬되며, 빈 일시는 맨 앞.
    """
    path = _excel_path(filename)
    if path is None:
        return {"ok": False, "error": "엑셀 파일을 찾을 수 없습니다."}
    with _run_lock:
        if RUN_STATE.get("status") == "running":
            return {"ok": False, "error": "수집 중에는 정렬할 수 없습니다. 잠시 후 다시 시도하세요."}

    with _excel_lock:
        wb = load_workbook(path)  # 스타일 보존
        ws = wb["점검"] if "점검" in wb.sheetnames else wb.worksheets[0]
        start = _XL_HEADER_ROW + 1
        end = ws.max_row

        captured: List[tuple] = []  # (vals[6], fill_rgb)
        for r in range(start, end + 1):
            vals, nonempty = [], False
            for i in range(_XL_NCOLS):
                v = ws.cell(row=r, column=_XL_DATA_START_COL + i).value
                v = "" if v is None else str(v)
                if v.strip():
                    nonempty = True
                vals.append(v)
            if not nonempty:
                continue
            f = ws.cell(row=r, column=_XL_DATA_START_COL).fill
            rgb = (getattr(getattr(f, "fgColor", None), "rgb", None)
                   if getattr(f, "patternType", None) == "solid" else None)
            captured.append((vals, rgb))

        # 일반점검만 (일시 시작시각 → 기관코드) 정렬. 그 외(정기점검 등)는 원순서로 뒤에.
        # 일시는 '시작 ~ 종료' 형식이라 '~' 앞(시작시각)만 1차 키로 써 같은 시작시각이면
        # 기관코드로 정렬되게 한다. 빈 일시는 맨 앞.
        general = [x for x in captured if x[0][0].strip() == "일반점검"]
        others = [x for x in captured if x[0][0].strip() != "일반점검"]
        general.sort(key=lambda x: (x[0][3].split("~")[0].strip(), x[0][1].strip()))
        ordered = general + others

        for idx, (vals, rgb) in enumerate(ordered):
            r = start + idx
            for i in range(_XL_NCOLS):
                c = ws.cell(row=r, column=_XL_DATA_START_COL + i, value=vals[i])
                c.font = _XL_DATA_FONT
                c.border = _XL_BORDER
                c.alignment = _XL_CENTER if i < 3 else _XL_LEFT_WRAP
                c.fill = PatternFill("solid", fgColor=rgb) if rgb else PatternFill(fill_type=None)
            ws.row_dimensions[r].height = 32

        # 정렬로 압축되어 남은 꼬리 행 정리
        for r in range(start + len(ordered), end + 1):
            for i in range(_XL_NCOLS):
                c = ws.cell(row=r, column=_XL_DATA_START_COL + i)
                c.value = None
                c.fill = PatternFill(fill_type=None)

        wb.save(path)
        wb.close()
    logger.info(f"엑셀 정렬(일반점검 {len(general)}건): {filename}")
    return {"ok": True, "sorted": len(general)}


# ---------------------------------------------------------------------------
# 수집 실행
# ---------------------------------------------------------------------------
async def _collect() -> None:
    run_id = make_run_id()
    run_date = run_id.split("_", 1)[0]
    today_compact = run_date.replace("-", "")

    with _run_lock:
        RUN_STATE.update(status="running", run_id=run_id, started=_now_str(),
                         finished="", count=0, review_count=0, error_count=0,
                         error="", excel="",
                         progress_done=0, progress_total=0, progress_site="")

    sites = load_sites(CONFIG_DIR / "sites.yaml")
    keywords = load_keywords(CONFIG_DIR / "keywords.yaml")
    regular = load_regular(CONFIG_DIR / "regular_maintenance.yaml")

    # 이전 엑셀에서 carryover (오늘 엑셀 제외 + 현행 exclude 키워드 재적용)
    carried = load_previous_general(EXCEL_DIR, exclude_date=today_compact)
    carried = [c for c in carried if is_maintenance(c.reason, keywords)]

    def _on_progress(done: int, total: int, site_name: str) -> None:
        with _run_lock:
            RUN_STATE.update(progress_done=done, progress_total=total,
                             progress_site=site_name)

    shot_dir = SCREENSHOT_DIR / run_date
    skips: List[Dict[str, Any]] = []
    hits = await scrape_all(
        sites=sites, keywords=keywords,
        screenshot_root=shot_dir, run_id=run_id, headless=True,
        skips=skips, progress_cb=_on_progress,
    )

    # 정기점검 baseline과 일시가 일치하는 감지 건 표시
    # (엑셀 신규에선 제외 — baseline 행이 이미 있음. 대시보드엔 체크로 구분)
    from .regular_match import find_regular_match, mark_regular_hits
    mark_regular_hits(hits, regular)
    # carryover(이전 엑셀의 일반점검 행)도 baseline과 일치하면 제거
    from .datetime_parser import extract_window
    def _carry_is_regular(c) -> bool:
        w = extract_window(c.schedule) if c.schedule else None
        return bool(w and find_regular_match(c.code, w.start, w.end, regular))
    carried = [c for c in carried if not _carry_is_regular(c)]

    EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXCEL_DIR / f"[사이트점검]_{today_compact}.xlsx"
    # 엑셀은 전체 hits(carryover 재감지 포함)로 작성 — 텍스트 갱신을 위해
    write_excel(hits, regular, out_path, run_id, carried=carried)

    # 검토 필요 탭에서 스킵 처리한 공지는 다시 올리지 않음 (스킵 탭에 기록)
    from .review_skips import filter_review_hits, load_review_skips
    hits = filter_review_hits(
        hits, load_review_skips(CONFIG_DIR / "review_skips.yaml"), skips_log=skips)

    # 이미 수집된(carryover) 재감지 항목은 감지목록/JSON에서 제외 + 스샷 삭제
    hits = strip_already_collected(hits, carried)
    matched = [h for h in hits if h.title and not h.needs_review]
    review = [h for h in hits if h.needs_review]
    errors = [h for h in hits if h.error]
    _save_run_json(run_id, hits, skips)

    with _run_lock:
        RUN_STATE.update(status="done", finished=_now_str(),
                         count=len(matched), review_count=len(review),
                         error_count=len(errors), excel=out_path.name)
    logger.info(
        f"수집 완료: {run_id} | 감지 {len(matched)}건 | "
        f"검토 필요 {len(review)}건 | 에러 {len(errors)}곳"
    )


def collect_once_sync() -> None:
    """수집 1회를 동기로 실행 (엑셀 + JSON 저장). 스케줄러/CLI 진입점용.

    예외는 그대로 전파하여 호출 측(systemd 등)이 실패를 감지하게 한다.
    """
    asyncio.run(_collect())


def _run_in_thread() -> None:
    try:
        asyncio.run(_collect())
    except Exception as e:  # noqa: BLE001
        logger.exception("수집 실패")
        with _run_lock:
            RUN_STATE.update(status="error", finished=_now_str(), error=str(e))


def start_scrape() -> Dict[str, Any]:
    with _run_lock:
        if RUN_STATE["status"] == "running":
            return {"started": False, "reason": "이미 수집 중입니다."}
        RUN_STATE.update(status="running", started=_now_str(), error="")
    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()
    return {"started": True}


# ---------------------------------------------------------------------------
# HTTP 핸들러
# ---------------------------------------------------------------------------
_INDEX_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>사이트 점검 수집기</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
         margin: 0; background: #f4f5f7; color: #1f2330; }
  header { background: #1f2937; color: #fff; padding: 16px 24px; display: flex;
           align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 18px; margin: 0; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 20px 24px 60px; }
  .toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  button { background: #2563eb; color: #fff; border: 0; padding: 9px 16px; border-radius: 6px;
           font-size: 14px; cursor: pointer; }
  button:disabled { background: #9ca3af; cursor: default; }
  select { padding: 8px 10px; border-radius: 6px; border: 1px solid #cbd2dc; font-size: 14px; }
  .status { font-size: 13px; color: #4b5563; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px;
         vertical-align: middle; }
  .dot.idle { background: #9ca3af; } .dot.running { background: #f59e0b; animation: blink 1s infinite; }
  .dot.done { background: #16a34a; } .dot.error { background: #dc2626; }
  @keyframes blink { 50% { opacity: .3; } }
  .cards { display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px 18px; min-width: 130px; }
  .card .n { font-size: 26px; font-weight: 700; } .card .l { font-size: 12px; color: #6b7280; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden;
          box-shadow: 0 1px 2px rgba(0,0,0,.05); }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #eef0f3; font-size: 13px;
           vertical-align: top; }
  th { background: #f8fafc; color: #475569; font-weight: 600; white-space: nowrap; }
  tr:hover td { background: #fafbfc; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #eef2ff; color: #4338ca; }
  a { color: #2563eb; text-decoration: none; } a:hover { text-decoration: underline; }
  img.thumb { width: 90px; height: 60px; object-fit: cover; border: 1px solid #e5e7eb; border-radius: 4px; cursor: pointer; }
  .muted { color: #9ca3af; }
  .err-box { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; border-radius: 8px;
             padding: 10px 14px; margin-bottom: 16px; font-size: 13px; }
  h2 { font-size: 15px; margin: 22px 0 10px; }
  .tabs { display: flex; gap: 6px; margin: 4px 0 16px; }
  .tab { background: #e5e7eb; color: #374151; border: 0; padding: 8px 16px; border-radius: 6px;
         font-size: 13px; cursor: pointer; }
  .tab.active { background: #1f2937; color: #fff; }
  .badge { display: inline-block; min-width: 16px; padding: 0 5px; margin-left: 4px; border-radius: 999px;
           background: #f59e0b; color: #fff; font-size: 11px; line-height: 16px; text-align: center; }
  tr.is-new td { background: #fffbcc !important; }
  tr.ocr-row td { background: #eff6ff; }
  .ocr-badge { display: inline-block; padding: 1px 6px; border-radius: 999px; font-size: 10px;
               background: #dbeafe; color: #1d4ed8; font-weight: 600; margin-right: 5px;
               vertical-align: middle; cursor: help; }
  tr.regular-row td { background: #f0fdf4; }
  .reg-badge { display: inline-block; padding: 1px 6px; border-radius: 999px; font-size: 10px;
               background: #dcfce7; color: #15803d; font-weight: 600; margin-right: 5px;
               vertical-align: middle; cursor: help; }
  td.warn-cell { background: #fff1f0 !important; }
  .warn-mark { color: #d97706; font-weight: 700; cursor: help; margin-right: 4px; }
  .skip-why { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px;
              background: #fef3c7; color: #92400e; }
  .skip-btn { padding: 4px 10px; border: 1px solid #d1d5db; border-radius: 6px; background: #f9fafb;
              color: #6b7280; font-size: 12px; cursor: pointer; margin-left: 4px; }
  .skip-btn:hover { background: #f3f4f6; color: #374151; }
  .skip-why.external { background: #e0e7ff; color: #3730a3; }
  .skip-why.past { background: #f1f5f9; color: #64748b; }
  .skip-why.parse { background: #fee2e2; color: #b91c1c; }
  td.title-cell { max-width: 260px; }
  .legend { font-size: 12px; color: #6b7280; margin: 8px 0 10px; }
  .legend .sw { display: inline-block; width: 12px; height: 12px; background: #fffbcc;
                border: 1px solid #e5d98a; border-radius: 2px; vertical-align: middle; margin-right: 4px; }
  td.ctr { text-align: center; white-space: nowrap; }
  .meta-line { font-size: 12px; color: #6b7280; margin-bottom: 10px; }
  td.edit { outline: none; cursor: text; }
  td.edit:focus { background: #eef6ff !important; box-shadow: inset 0 0 0 2px #2563eb; }
  tr.dirty td { background: #fff7ed !important; }
  .del-btn { background: #fee2e2; color: #b91c1c; border: 0; border-radius: 5px;
             padding: 4px 9px; cursor: pointer; font-size: 13px; }
  .del-btn:hover { background: #fecaca; }
  .add-btn { background: #16a34a; color: #fff; border: 0; border-radius: 5px;
             padding: 5px 12px; cursor: pointer; font-size: 13px; white-space: nowrap; }
  .add-btn:hover { background: #15803d; }
  .add-btn:disabled { background: #9ca3af; cursor: default; }
  .add-btn.added { background: #dcfce7; color: #166534; }
  td.delcell { text-align: center; width: 44px; }
  td.ordcell { text-align: center; width: 76px; white-space: nowrap; }
  .drag-handle { cursor: grab; color: #9ca3af; margin-right: 4px; user-select: none; }
  .drag-handle:hover { color: #4b5563; }
  .ord-btn { background: #e5e7eb; color: #374151; border: 0; border-radius: 5px;
             padding: 3px 6px; cursor: pointer; font-size: 11px; }
  .ord-btn:hover { background: #d1d5db; }
  tr.dragging { opacity: .45; }
  tr.drag-over-top td { box-shadow: inset 0 3px 0 #2563eb; }
  tr.drag-over-bottom td { box-shadow: inset 0 -3px 0 #2563eb; }
  #overlay { position: fixed; inset: 0; background: rgba(0,0,0,.8); display: none; align-items: center;
             justify-content: center; z-index: 50; }
  #overlay img { max-width: 92%; max-height: 92%; border-radius: 6px; }
  code { background: #eef2f7; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .kw-sec { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px 18px; margin-bottom: 14px; }
  .kw-sec h3 { font-size: 14px; margin: 0 0 3px; }
  .kw-sec .hint { font-size: 12px; color: #6b7280; margin-bottom: 10px; }
  .kw-sec .cnt { font-size: 12px; color: #9ca3af; font-weight: 400; }
  .kw-list { display: flex; flex-wrap: wrap; gap: 7px; }
  .kw-chip { display: inline-flex; align-items: center; background: #f1f5f9; border: 1px solid #e2e8f0;
             border-radius: 999px; padding: 3px 11px; font-size: 12.5px; color: #334155; }
  .kw-sec.exclude .kw-chip { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
  .kw-chip .note { color: #94a3b8; margin-left: 7px; font-size: 11px; max-width: 340px; overflow: hidden;
                   text-overflow: ellipsis; white-space: nowrap; }
  .kw-sec.exclude .kw-chip .note { color: #c07a7a; }
</style>
</head>
<body>
<header>
  <h1>🛠️ 사이트 점검 공지 수집기</h1>
  <span class="status" id="statusLine"></span>
</header>
<div class="wrap">
  <div class="toolbar">
    <button id="scrapeBtn" onclick="startScrape()">지금 수집</button>
    <label class="status">이력:
      <select id="runSelect" onchange="loadRun(this.value)"></select>
    </label>
    <span class="status" id="genAt"></span>
    <a id="excelLink" class="status" href="#" style="display:none">엑셀 다운로드</a>
  </div>

  <div class="cards">
    <div class="card"><div class="n" id="cMatched">0</div><div class="l">감지된 점검 공지</div></div>
    <div class="card"><div class="n" id="cReview">0</div><div class="l">검토 필요</div></div>
    <div class="card"><div class="n" id="cSkipped">0</div><div class="l">스킵된 공지</div></div>
    <div class="card"><div class="n" id="cErrors">0</div><div class="l">에러 사이트</div></div>
  </div>

  <div id="errBox" class="err-box" style="display:none"></div>

  <div class="tabs">
    <button class="tab active" id="tabMatched" onclick="switchView('matched')">감지 목록</button>
    <button class="tab" id="tabReview" onclick="switchView('review')">검토 필요 <span id="reviewBadge" class="badge" style="display:none">0</span></button>
    <button class="tab" id="tabSkipped" onclick="switchView('skipped')">스킵</button>
    <button class="tab" id="tabExcel" onclick="switchView('excel')">엑셀뷰</button>
    <button class="tab" id="tabRegular" onclick="switchView('regular')">정기점검</button>
    <button class="tab" id="tabKeywords" onclick="switchView('keywords')">제외 키워드</button>
  </div>

  <!-- 감지 목록 -->
  <div id="viewMatched">
    <h2>감지된 점검 공지</h2>
    <table>
      <thead><tr>
        <th>분류</th><th>기관코드</th><th>기관명</th><th>제목</th><th>점검 일시</th>
        <th>업무</th><th>사유</th><th>원문</th><th>캡처</th><th>삭제</th>
      </tr></thead>
      <tbody id="rows"><tr><td colspan="10" class="muted">데이터가 없습니다. ‘지금 수집’을 눌러 실행하세요.</td></tr></tbody>
    </table>
    <div class="legend" style="margin-top:8px">
      🗑 삭제 시 해당 항목과 <b>캡처 스크린샷 파일</b>이 함께 삭제됩니다. (엑셀은 별도) &nbsp;·&nbsp;
      <span class="warn-cell" style="padding:1px 6px; border-radius:4px">⚠ 표시</span> = 파싱 의심값 (업무 기본값 폴백 / 종료 시각 없음) — 원문·캡처로 확인 권장 &nbsp;·&nbsp;
      <span class="ocr-badge">OCR</span><span style="background:#eff6ff; padding:1px 6px; border-radius:4px">파란 행</span> = 이미지 공지를 OCR로 읽어 자동 감지 — 오탈자 가능성, 캡처로 확인 권장 &nbsp;·&nbsp;
      <span class="reg-badge">✓ 정기점검</span><span style="background:#f0fdf4; padding:1px 6px; border-radius:4px">초록 행</span> = 정기점검 baseline과 일시 일치 — 엑셀 신규 목록엔 미포함
    </div>
  </div>

  <!-- 스킵된 공지 -->
  <div id="viewSkipped" style="display:none">
    <h2>스킵된 공지 — 점검 키워드에 걸렸으나 제외된 항목</h2>
    <div class="legend">
      외부기관 안내·제외 키워드·지난 점검·일시 파싱 실패로 감지 목록에서 빠진 공지입니다.
      <b>잘못 제외된 공지가 없는지</b> 여기서 확인하세요. (일시 파싱 실패는 놓친 점검일 수 있습니다)
    </div>
    <div class="toolbar" style="margin-bottom:10px">
      <label class="status">사유:
        <select id="skipFilter" onchange="renderSkips()"><option value="">(전체)</option></select>
      </label>
      <label class="status">분류:
        <select id="skipCatFilter" onchange="onSkipCatChange()"><option value="">(전체)</option></select>
      </label>
      <label class="status">기관:
        <select id="skipSiteFilter" onchange="renderSkips()"><option value="">(전체)</option></select>
      </label>
      <button class="tab" style="padding:6px 12px" onclick="resetSkipFilters()">필터 초기화</button>
      <span class="status" id="skipCount"></span>
    </div>
    <table>
      <thead><tr>
        <th>분류</th><th>기관코드</th><th>기관명</th><th>제목</th>
        <th>스킵 사유</th><th>등록일</th><th>원문</th>
      </tr></thead>
      <tbody id="skipRows"><tr><td colspan="7" class="muted">스킵된 공지가 없습니다. (이전 실행 기록에는 스킵 데이터가 없을 수 있습니다)</td></tr></tbody>
    </table>
  </div>

  <!-- 정기점검 baseline -->
  <div id="viewRegular" style="display:none">
    <h2>정기점검 baseline <span class="status" id="regularPath"></span></h2>
    <div class="toolbar">
      <button id="regularSaveBtn" onclick="saveRegular()" disabled>변경사항 저장</button>
      <button class="add-btn" onclick="addRegularRow()">행 추가</button>
      <span class="status" id="regularState"></span>
    </div>
    <div class="legend">
      수집 결과와 무관하게 엑셀에 항상 포함되는 정기점검 일정입니다.
      셀을 클릭해 편집한 뒤 <b>변경사항 저장</b>을 누르면 <code>config/regular_maintenance.yaml</code>에
      바로 반영됩니다 (엑셀에는 다음 수집부터). 기관코드·기관명·일시는 필수입니다.
      <b>⠿ 핸들을 드래그</b>하거나 <b>▲▼</b> 버튼으로 행 순서를 바꿀 수 있고,
      저장하면 그 순서 그대로 파일·엑셀에 반영됩니다.
    </div>
    <table>
      <thead><tr>
        <th>순서</th><th>기관코드</th><th>기관명</th><th>일시</th><th>업무</th><th>사유</th><th>삭제</th>
      </tr></thead>
      <tbody id="regularRows"><tr><td colspan="7" class="muted">불러오는 중…</td></tr></tbody>
    </table>
  </div>

  <!-- 검토 필요 -->
  <div id="viewReview" style="display:none">
    <h2>검토 필요 — 제목은 점검 공지이나 본문이 비어(이미지 공지 등) 자동 판별 불가</h2>
    <div class="legend">
      아래 항목은 <b>엑셀에 자동 반영되지 않습니다.</b> 캡처를 열어 확인한 뒤,
      실제 점검이면 엑셀뷰에서 직접 행을 추가/편집하세요.
      점검이 아니거나 이미 지난 건은 <b>스킵</b> — 목록에서 지워지고 앞으로의 수집에서도 다시 올라오지 않습니다.
    </div>
    <table>
      <thead><tr>
        <th>분류</th><th>기관코드</th><th>기관명</th><th>제목</th>
        <th>등록일</th><th>원문</th><th>캡처</th><th>처리</th>
      </tr></thead>
      <tbody id="reviewRows"><tr><td colspan="8" class="muted">검토할 항목이 없습니다.</td></tr></tbody>
    </table>
  </div>

  <!-- 엑셀뷰 -->
  <div id="viewExcel" style="display:none">
    <div class="toolbar">
      <label class="status">엑셀 파일:
        <select id="excelSelect" onchange="loadExcel(this.value)"></select>
      </label>
      <button id="saveBtn" onclick="saveExcel()" disabled>변경사항 저장</button>
      <button id="sortBtn" onclick="sortExcel()">일시·기관코드 정렬</button>
      <span class="status" id="editState"></span>
      <a id="excelDl" class="status" href="#" style="display:none">다운로드</a>
    </div>
    <div class="legend">
      <span class="sw"></span> 노란색 = 신규 감지 항목 &nbsp;·&nbsp;
      셀을 클릭해 직접 편집한 뒤 <b>변경사항 저장</b>을 누르면 엑셀에 반영됩니다 &nbsp;·&nbsp;
      행 끝 🗑 으로 삭제
    </div>
    <div class="meta-line" id="excelMeta"></div>
    <table>
      <thead><tr id="excelHead"></tr></thead>
      <tbody id="excelRows"><tr><td class="muted">엑셀 파일을 선택하세요.</td></tr></tbody>
    </table>
  </div>

  <!-- 제외 키워드 뷰 -->
  <div id="viewKeywords" style="display:none">
    <h2>키워드 설정 <span class="status" id="kwPath"></span></h2>
    <div class="legend">
      수집 시 제목/본문에 아래 키워드가 매칭되는지로 점검 공지를 판별합니다.
      수정하려면 <code>config/keywords.yaml</code> 파일을 편집한 뒤 다시 수집하세요.
    </div>
    <div id="kwSections"></div>
  </div>
</div>

<div id="overlay" onclick="this.style.display='none'"><img id="overlayImg" src=""/></div>

<script>
let polling = null;

function esc(s){ return (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function refreshStatus(){
  const s = await (await fetch('/api/status')).json();
  const line = document.getElementById('statusLine');
  const btn = document.getElementById('scrapeBtn');
  let label = {idle:'대기', running:'수집 중…', done:'완료', error:'오류'}[s.status] || s.status;
  if(s.status === 'running' && s.progress_total > 0){
    label = `수집 중… ${s.progress_done}/${s.progress_total} 사이트` +
      (s.progress_site ? ` · 현재: ${esc(s.progress_site)}` : '');
  }
  line.innerHTML = `<span class="dot ${s.status}"></span>${label}` +
    (s.finished ? ` · 마지막 실행 ${esc(s.finished)}` : (s.started ? ` · 시작 ${esc(s.started)}` : '')) +
    (s.error ? ` · <span style="color:#fca5a5">${esc(s.error)}</span>` : '');
  btn.disabled = (s.status === 'running');
  btn.textContent = (s.status === 'running')
    ? (s.progress_total ? `수집 중… ${s.progress_done}/${s.progress_total}` : '수집 중…')
    : '지금 수집';
  return s;
}

async function startScrape(){
  const r = await (await fetch('/api/scrape', {method:'POST'})).json();
  if(!r.started && r.reason){ alert(r.reason); return; }
  if(polling) clearInterval(polling);
  polling = setInterval(async () => {
    const s = await refreshStatus();
    if(s.status === 'done' || s.status === 'error'){
      clearInterval(polling); polling = null;
      await loadRuns();
      if(s.run_id) loadRun(s.run_id);
    }
  }, 2000);
  refreshStatus();
}

async function loadRuns(){
  const runs = await (await fetch('/api/runs')).json();
  const sel = document.getElementById('runSelect');
  sel.innerHTML = '';
  if(runs.length === 0){ sel.innerHTML = '<option value="">(없음)</option>'; return; }
  for(const r of runs){
    const o = document.createElement('option');
    o.value = r.run_id;
    const rev = r.review_count ? ` · 검토 ${r.review_count}` : '';
    o.textContent = `${r.run_id}  (감지 ${r.count}${rev} · 에러 ${r.error_count})`;
    sel.appendChild(o);
  }
}

async function loadRun(runId){
  if(!runId) return;
  const d = await (await fetch('/api/run?id=' + encodeURIComponent(runId))).json();
  curRunId = d.run_id || runId;
  document.getElementById('cMatched').textContent = d.count || 0;
  const reviewList = d.review || [];
  document.getElementById('cReview').textContent = reviewList.length;
  document.getElementById('cErrors').textContent = d.error_count || 0;
  curSkipped = d.skipped || [];
  document.getElementById('cSkipped').textContent = curSkipped.length;
  buildSkipFilter();
  renderSkips();
  const badge = document.getElementById('reviewBadge');
  if(reviewList.length){ badge.style.display='inline-block'; badge.textContent = reviewList.length; }
  else { badge.style.display='none'; }
  document.getElementById('genAt').textContent = d.generated_at ? ('생성: ' + d.generated_at) : '';
  const sel = document.getElementById('runSelect');
  if(sel.value !== runId) sel.value = runId;

  // 엑셀 링크
  const exLink = document.getElementById('excelLink');
  const compact = (d.run_id||'').split('_')[0].replaceAll('-','');
  if(compact){ exLink.style.display='inline'; exLink.href = '/excel/' + encodeURIComponent('[사이트점검]_'+compact+'.xlsx'); }

  // 에러 박스
  const errBox = document.getElementById('errBox');
  if(d.errors && d.errors.length){
    errBox.style.display = 'block';
    errBox.innerHTML = '⚠ 에러 사이트: ' + d.errors.map(e => `${esc(e.site_name||e.site_code)} (${esc(e.error)})`).join(', ');
  } else { errBox.style.display = 'none'; }

  // 본문
  const tb = document.getElementById('rows');
  const rows = d.matched || [];
  if(rows.length === 0){
    tb.innerHTML = '<tr><td colspan="10" class="muted">감지된 점검 공지가 없습니다.</td></tr>';
  } else {
    tb.innerHTML = rows.map((h, i) => {
      // 의심값 플래그: 종료 시각 없음 / 업무 기본값 폴백
      const noEnd = !h.window_end;
      const svcDefault = !!h.service_default;
      const schedCell = noEnd
        ? `<td class="warn-cell"><span class="warn-mark" title="종료 시각을 파싱하지 못했습니다">⚠</span>${esc(h.schedule_text)}</td>`
        : `<td>${esc(h.schedule_text)}</td>`;
      const svcCell = svcDefault
        ? `<td class="warn-cell"><span class="warn-mark" title="본문에서 업무 라벨을 못 찾아 기본값으로 폴백했습니다">⚠</span>${esc(h.service_text)}</td>`
        : `<td>${esc(h.service_text)}</td>`;
      const ocrBadge = h.via_ocr
        ? `<span class="ocr-badge" title="이미지 공지를 OCR로 읽어 자동 감지한 항목입니다. 일시·업무에 오탈자가 있을 수 있으니 캡처로 확인하세요">OCR</span>`
        : '';
      const regBadge = h.is_regular
        ? `<span class="reg-badge" title="정기점검 baseline과 일시가 일치합니다${h.regular_reason ? ' (' + esc(h.regular_reason) + ')' : ''}. 엑셀 신규 목록에는 넣지 않습니다">✓ 정기점검</span>`
        : '';
      const rowCls = h.is_regular ? ' class="regular-row"' : (h.via_ocr ? ' class="ocr-row"' : '');
      return `
      <tr${rowCls}>
        <td><span class="tag">${esc(h.category)}</span></td>
        <td>${esc(h.site_code)}</td>
        <td>${esc(h.site_name)}</td>
        <td class="title-cell">${regBadge}${ocrBadge}${esc(h.title)}</td>
        ${schedCell}
        ${svcCell}
        <td>${esc(h.reason_text || h.title)}</td>
        <td>${h.detail_url ? `<a href="${esc(h.detail_url)}" target="_blank">열기</a>` : '<span class="muted">-</span>'}</td>
        <td>${h.screenshot_url ? `<img class="thumb" src="${esc(h.screenshot_url)}" onclick="zoom('${esc(h.screenshot_url)}')"/>` : '<span class="muted">-</span>'}</td>
        <td class="delcell"><button class="del-btn" title="항목+스샷 삭제" onclick="deleteMatched(${i}, this)">🗑</button></td>
      </tr>`;
    }).join('');
  }

  // 검토 필요 목록
  curReview = reviewList;
  curExcelName = compact ? ('[사이트점검]_' + compact + '.xlsx') : '';
  const rtb = document.getElementById('reviewRows');
  if(reviewList.length === 0){
    rtb.innerHTML = '<tr><td colspan="8" class="muted">검토할 항목이 없습니다.</td></tr>';
  } else {
    rtb.innerHTML = reviewList.map((h, i) => `
      <tr>
        <td><span class="tag">${esc(h.category)}</span></td>
        <td>${esc(h.site_code)}</td>
        <td>${esc(h.site_name)}</td>
        <td>${esc(h.title)}</td>
        <td>${esc(h.posted_date)}</td>
        <td>${h.detail_url ? `<a href="${esc(h.detail_url)}" target="_blank">열기</a>` : '<span class="muted">-</span>'}</td>
        <td>${h.screenshot_url ? `<img class="thumb" src="${esc(h.screenshot_url)}" onclick="zoom('${esc(h.screenshot_url)}')"/>` : '<span class="muted">-</span>'}</td>
        <td class="ctr">
          <button class="add-btn" onclick="addToExcel(${i}, this)">공지추가</button>
          <button class="skip-btn" title="이 공지를 검토 목록에서 지우고, 앞으로의 수집에서도 다시 올리지 않습니다" onclick="skipReview(${i}, this)">스킵</button>
        </td>
      </tr>`).join('');
  }
}

let curReview = [];
let curExcelName = '';
let curRunId = '';
let curSkipped = [];

// ---- 스킵 목록 ----
function skipWhyClass(why){
  if(why.startsWith('외부 기관')) return 'external';
  if(why.startsWith('이미 지난')) return 'past';
  if(why.includes('파싱 실패') || why.includes('일시 미상')) return 'parse';
  return '';
}

function rebuildSelect(id, entries, prev){
  // entries = [{value, label, count}] — 옵션에 건수 표기, 이전 선택 유지
  const sel = document.getElementById(id);
  sel.innerHTML = '<option value="">(전체)</option>' +
    entries.map(e => `<option value="${esc(e.value)}">${esc(e.label)} (${e.count})</option>`).join('');
  if(prev && entries.some(e => e.value === prev)) sel.value = prev;
}

function countBy(list, keyFn){
  const m = new Map();
  for(const s of list){ const k = keyFn(s); m.set(k, (m.get(k)||0)+1); }
  return m;
}

function buildSkipFilter(){
  const prevWhy = document.getElementById('skipFilter').value;
  const prevCat = document.getElementById('skipCatFilter').value;
  const prevSite = document.getElementById('skipSiteFilter').value;

  // 사유 앞부분(괄호 전)으로 그룹핑
  const whyMap = countBy(curSkipped, s => (s.why||'').split('(')[0].trim());
  rebuildSelect('skipFilter',
    [...whyMap.entries()].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0]))
      .map(([k,n]) => ({value:k, label:k, count:n})), prevWhy);

  // 분류 (은행/증권/카드 …)
  const catMap = countBy(curSkipped, s => s.category || '');
  rebuildSelect('skipCatFilter',
    [...catMap.entries()].sort((a,b) => a[0].localeCompare(b[0]))
      .map(([k,n]) => ({value:k, label:k, count:n})), prevCat);

  buildSkipSiteFilter(prevSite);
}

// 기관 옵션은 선택된 '분류'에 속한 기관만 보여준다 (분류 변경 시 재구성)
function buildSkipSiteFilter(prevSite){
  const cat = document.getElementById('skipCatFilter').value;
  const base = cat ? curSkipped.filter(s => (s.category||'') === cat) : curSkipped;
  const siteMap = new Map();
  for(const s of base){
    const k = s.site_code || '';
    if(!siteMap.has(k)) siteMap.set(k, {name: s.site_name || '', count: 0});
    siteMap.get(k).count++;
  }
  // 이전 선택이 새 옵션에 없으면 자동으로 (전체)로 리셋됨
  rebuildSelect('skipSiteFilter',
    [...siteMap.entries()].sort((a,b) => a[0].localeCompare(b[0]))
      .map(([k,v]) => ({value:k, label:`${k} ${v.name}`, count:v.count})),
    prevSite ?? document.getElementById('skipSiteFilter').value);
}

function onSkipCatChange(){
  buildSkipSiteFilter();
  renderSkips();
}

function resetSkipFilters(){
  for(const id of ['skipFilter','skipCatFilter','skipSiteFilter'])
    document.getElementById(id).value = '';
  buildSkipSiteFilter();
  renderSkips();
}

function renderSkips(){
  const why = document.getElementById('skipFilter').value;
  const cat = document.getElementById('skipCatFilter').value;
  const site = document.getElementById('skipSiteFilter').value;
  const list = curSkipped.filter(s =>
    (!why || (s.why||'').split('(')[0].trim() === why) &&
    (!cat || (s.category||'') === cat) &&
    (!site || (s.site_code||'') === site));
  document.getElementById('skipCount').textContent =
    curSkipped.length ? `${list.length}/${curSkipped.length}건` : '';
  const tb = document.getElementById('skipRows');
  if(list.length === 0){
    tb.innerHTML = '<tr><td colspan="7" class="muted">스킵된 공지가 없습니다. (이전 실행 기록에는 스킵 데이터가 없을 수 있습니다)</td></tr>';
    return;
  }
  tb.innerHTML = list.map(s => `
    <tr>
      <td><span class="tag">${esc(s.category)}</span></td>
      <td>${esc(s.site_code)}</td>
      <td>${esc(s.site_name)}</td>
      <td class="title-cell">${esc(s.title)}</td>
      <td><span class="skip-why ${skipWhyClass(s.why||'')}">${esc(s.why)}</span></td>
      <td>${esc(s.posted_date) || '<span class="muted">-</span>'}</td>
      <td>${s.detail_url ? `<a href="${esc(s.detail_url)}" target="_blank">열기</a>` : '<span class="muted">-</span>'}</td>
    </tr>`).join('');
}

// ---- 정기점검 baseline (편집 가능) ----
const REGULAR_COLS = ['code','name','schedule','service','reason'];

function setRegularState(msg, color){
  const el = document.getElementById('regularState');
  el.textContent = msg || ''; el.style.color = color || '#6b7280';
}

function regularRowHtml(r){
  const tds = REGULAR_COLS.map((k,i) =>
    `<td class="edit ${i<2?'ctr':''}" contenteditable="true" data-col="${k}">${esc(r[k]||'')}</td>`).join('');
  const ord = `<td class="ordcell">` +
    `<span class="drag-handle" title="드래그해서 순서 변경">⠿</span>` +
    `<button class="ord-btn" title="위로" onclick="moveRegularRow(this,-1)">▲</button>` +
    `<button class="ord-btn" title="아래로" onclick="moveRegularRow(this,1)">▼</button></td>`;
  return `<tr>${ord}${tds}<td class="delcell"><button class="del-btn" title="이 행 삭제" onclick="deleteRegularRow(this)">🗑</button></td></tr>`;
}

function moveRegularRow(btn, dir){
  const tr = btn.closest('tr');
  const sib = dir < 0 ? tr.previousElementSibling : tr.nextElementSibling;
  if(!sib || !sib.querySelector('td.edit')) return;  // 끝이거나 안내 행이면 무시
  if(dir < 0) tr.parentNode.insertBefore(tr, sib);
  else tr.parentNode.insertBefore(sib, tr);
  tr.classList.add('dirty');
  markRegularDirty();
}

function clearRegularDragMarks(){
  document.querySelectorAll('#regularRows tr').forEach(t =>
    t.classList.remove('drag-over-top', 'drag-over-bottom'));
}

let _regularDndReady = false;
function initRegularDnD(){
  // tbody 는 유지되고 행만 갈아끼우므로 위임 리스너를 1회만 등록
  if(_regularDndReady) return;
  _regularDndReady = true;
  const tb = document.getElementById('regularRows');
  let dragRow = null;
  // contenteditable 셀의 텍스트 드래그와 충돌하지 않도록 핸들에서 시작할 때만 행을 draggable 로
  tb.addEventListener('mousedown', e => {
    const h = e.target.closest('.drag-handle');
    if(h) h.closest('tr').draggable = true;
  });
  tb.addEventListener('dragstart', e => {
    const tr = e.target.closest('tr');
    if(!tr || !tr.draggable){ e.preventDefault(); return; }
    dragRow = tr;
    tr.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try{ e.dataTransfer.setData('text/plain', ''); }catch(_){ }
  });
  tb.addEventListener('dragover', e => {
    if(!dragRow) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const tr = e.target.closest('tr');
    clearRegularDragMarks();
    if(!tr || tr === dragRow || !tr.querySelector('td.edit')) return;
    const rect = tr.getBoundingClientRect();
    const before = (e.clientY - rect.top) < rect.height / 2;
    tr.classList.add(before ? 'drag-over-top' : 'drag-over-bottom');
  });
  tb.addEventListener('drop', e => {
    if(!dragRow) return;
    e.preventDefault();
    const tr = e.target.closest('tr');
    if(tr && tr !== dragRow && tr.querySelector('td.edit')){
      const rect = tr.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      tr.parentNode.insertBefore(dragRow, before ? tr : tr.nextElementSibling);
      dragRow.classList.add('dirty');
      markRegularDirty();
    }
  });
  tb.addEventListener('dragend', () => {
    clearRegularDragMarks();
    if(dragRow){
      dragRow.classList.remove('dragging');
      dragRow.draggable = false;
      dragRow = null;
    }
  });
}

function wireRegularEdits(){
  const tb = document.getElementById('regularRows');
  tb.querySelectorAll('td.edit').forEach(td => {
    td.addEventListener('input', () => {
      td.closest('tr').classList.add('dirty');
      markRegularDirty();
    });
  });
}

function markRegularDirty(){
  document.getElementById('regularSaveBtn').disabled = false;
  setRegularState('저장되지 않은 변경사항이 있습니다.', '#b45309');
}

async function loadRegular(){
  const d = await (await fetch('/api/regular')).json();
  document.getElementById('regularPath').textContent = d.path ? `(${d.path})` : '';
  const tb = document.getElementById('regularRows');
  initRegularDnD();
  if(d.error){ tb.innerHTML = `<tr><td colspan="7" class="muted">${esc(d.error)}</td></tr>`; return; }
  const items = d.items || [];
  tb.innerHTML = items.length
    ? items.map(regularRowHtml).join('')
    : '<tr><td colspan="7" class="muted">정기점검 항목이 없습니다. ‘행 추가’로 시작하세요.</td></tr>';
  wireRegularEdits();
  document.getElementById('regularSaveBtn').disabled = true;
  setRegularState('');
}

function addRegularRow(){
  const tb = document.getElementById('regularRows');
  // 빈 안내 행 제거
  if(tb.querySelector('td.muted')) tb.innerHTML = '';
  tb.insertAdjacentHTML('beforeend',
    regularRowHtml({code:'', name:'', schedule:'', service:'', reason:''}));
  const tr = tb.lastElementChild;
  tr.classList.add('dirty');
  wireRegularEdits();
  markRegularDirty();
  tr.querySelector('td.edit').focus();
}

function deleteRegularRow(btn){
  if(!confirm('이 정기점검 행을 삭제할까요?\\n(변경사항 저장을 눌러야 파일에 반영됩니다)')) return;
  btn.closest('tr').remove();
  markRegularDirty();
}

async function saveRegular(){
  const rows = [...document.querySelectorAll('#regularRows tr')].filter(tr => tr.querySelector('td.edit'));
  const items = rows.map(tr => {
    const o = {};
    tr.querySelectorAll('td.edit').forEach(td => { o[td.dataset.col] = cellText(td); });
    return o;
  });
  setRegularState('저장 중…');
  const r = await fetch('/api/regular/update', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({items}),
  });
  const res = await r.json();
  if(res.ok){
    setRegularState(`저장 완료 (${res.saved}건) — 다음 수집부터 엑셀에 반영`, '#16a34a');
    loadRegular();   // 파일에서 다시 읽어 동기화
  } else {
    setRegularState('저장 실패: ' + (res.error || ''), '#dc2626');
  }
}

async function deleteMatched(i, btn){
  if(!confirm('이 감지 항목과 캡처 스크린샷을 삭제할까요?\\n(엑셀에는 영향 없음)')) return;
  btn.disabled = true;
  const r = await fetch('/api/matched/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({run_id: curRunId, index: i}),
  });
  const res = await r.json();
  if(res.ok){
    await loadRuns();
    loadRun(curRunId);
  } else {
    btn.disabled = false;
    alert('삭제 실패: ' + (res.error || ''));
  }
}

async function skipReview(i, btn){
  const h = curReview[i];
  if(!h){ return; }
  if(!confirm(`이 공지를 스킵할까요?\\n\\n[${h.site_code}] ${h.site_name}\\n${h.title}\\n\\n검토 목록에서 지워지고, 앞으로의 수집에서도 다시 올라오지 않습니다.`)) return;
  btn.disabled = true;
  const r = await fetch('/api/review/skip', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({run_id: curRunId, index: i}),
  });
  const res = await r.json();
  if(res.ok){
    await loadRuns();
    loadRun(curRunId);
  } else {
    btn.disabled = false;
    alert('스킵 실패: ' + (res.error || ''));
  }
}

async function addToExcel(i, btn){
  const h = curReview[i];
  if(!h){ return; }
  if(!curExcelName){ alert('대상 엑셀을 찾을 수 없습니다.'); return; }
  if(!confirm(`이 공지를 엑셀에 추가할까요?\\n\\n[${h.site_code}] ${h.site_name}\\n${h.title}`)) return;
  btn.disabled = true; btn.textContent = '추가 중…';
  // 구분(일반점검) / 기관코드 / 기관명 / 일시(빈칸) / 업무(빈칸) / 사유(제목)
  const cells = ['일반점검', h.site_code || '', h.site_name || '', '', '', h.title || ''];
  const r = await fetch('/api/excel/append', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({file: curExcelName, cells}),
  });
  const res = await r.json();
  if(res.ok){
    btn.textContent = res.duplicate ? '이미 추가됨' : '추가됨 ✓';
    btn.classList.add('added');
  } else {
    btn.disabled = false; btn.textContent = '공지추가';
    alert('추가 실패: ' + (res.error || ''));
  }
}

function zoom(src){
  document.getElementById('overlayImg').src = src;
  document.getElementById('overlay').style.display = 'flex';
}

// ---- 뷰 전환 ----
function switchView(name){
  const views = {matched:'viewMatched', review:'viewReview', skipped:'viewSkipped',
                 excel:'viewExcel', regular:'viewRegular', keywords:'viewKeywords'};
  const tabs = {matched:'tabMatched', review:'tabReview', skipped:'tabSkipped',
                excel:'tabExcel', regular:'tabRegular', keywords:'tabKeywords'};
  for(const [k, id] of Object.entries(views))
    document.getElementById(id).style.display = (name === k) ? 'block' : 'none';
  for(const [k, id] of Object.entries(tabs))
    document.getElementById(id).classList.toggle('active', name === k);
  if(name === 'excel') loadExcels();
  if(name === 'regular') loadRegular();
  if(name === 'keywords') loadKeywords();
}

async function loadKeywords(){
  const d = await (await fetch('/api/keywords')).json();
  document.getElementById('kwPath').textContent = d.path ? `(${d.path})` : '';
  const wrap = document.getElementById('kwSections');
  if(d.error){ wrap.innerHTML = `<div class="err-box">${esc(d.error)}</div>`; return; }
  wrap.innerHTML = (d.sections || []).map(sec => {
    const chips = (sec.items || []).map(it =>
      `<span class="kw-chip">${esc(it.keyword)}${it.note ? `<span class="note" title="${esc(it.note)}">${esc(it.note)}</span>` : ''}</span>`
    ).join('');
    const body = (sec.items && sec.items.length)
      ? `<div class="kw-list">${chips}</div>`
      : '<div class="hint">(없음)</div>';
    return `<div class="kw-sec ${sec.key.startsWith('exclude') ? 'exclude' : ''}">
      <h3>${esc(sec.title)} <span class="cnt">${(sec.items||[]).length}개</span></h3>
      <div class="hint">${esc(sec.hint)}</div>
      ${body}
    </div>`;
  }).join('');
}

// ---- 엑셀뷰 ----
async function loadExcels(){
  const list = await (await fetch('/api/excels')).json();
  const sel = document.getElementById('excelSelect');
  const prev = sel.value;
  sel.innerHTML = '';
  if(list.length === 0){
    sel.innerHTML = '<option value="">(없음)</option>';
    document.getElementById('excelRows').innerHTML = '<tr><td class="muted">생성된 엑셀이 없습니다.</td></tr>';
    return;
  }
  for(const it of list){
    const o = document.createElement('option');
    o.value = it.filename;
    o.textContent = `${it.date}  (${it.filename})`;
    sel.appendChild(o);
  }
  sel.value = (prev && list.some(x => x.filename === prev)) ? prev : list[0].filename;
  loadExcel(sel.value);
}

let curExcelFile = '';

function setEditState(msg, color){
  const el = document.getElementById('editState');
  el.textContent = msg || ''; el.style.color = color || '#6b7280';
}

async function loadExcel(file){
  if(!file) return;
  curExcelFile = file;
  const d = await (await fetch('/api/excel?file=' + encodeURIComponent(file))).json();
  const dl = document.getElementById('excelDl');
  dl.style.display = 'inline'; dl.href = '/excel/' + encodeURIComponent(file);

  const meta = d.meta || {};
  document.getElementById('excelMeta').textContent =
    Object.keys(meta).length ? Object.entries(meta).map(([k,v]) => `${k}: ${v}`).join('  ·  ') : '';

  document.getElementById('excelHead').innerHTML =
    (d.columns || []).map(c => `<th>${esc(c)}</th>`).join('') + '<th>삭제</th>';

  const tb = document.getElementById('excelRows');
  const rows = d.rows || [];
  if(rows.length === 0){
    tb.innerHTML = `<tr><td colspan="${(d.columns||[]).length+1}" class="muted">데이터가 없습니다.</td></tr>`;
    document.getElementById('saveBtn').disabled = true;
    setEditState('');
    return;
  }
  tb.innerHTML = rows.map(r => {
    const tds = r.cells.map((v,i) =>
      `<td class="edit ${i<3?'ctr':''}" contenteditable="true" data-col="${i}">${esc(v).replace(/\\n/g,'<br>')}</td>`).join('');
    const del = `<td class="delcell"><button class="del-btn" title="이 행 삭제"
                  onclick="deleteRow(${r.excel_row}, this)">🗑</button></td>`;
    return `<tr class="${r.is_new?'is-new':''}" data-row="${r.excel_row}">${tds}${del}</tr>`;
  }).join('');

  // 편집 감지 → dirty 표시
  tb.querySelectorAll('td.edit').forEach(td => {
    td.addEventListener('input', () => {
      td.closest('tr').classList.add('dirty');
      document.getElementById('saveBtn').disabled = false;
      setEditState('저장되지 않은 변경사항이 있습니다.', '#b45309');
    });
  });
  document.getElementById('saveBtn').disabled = true;
  setEditState('');
}

function cellText(td){
  return td.innerText.replace(/\\u00a0/g, ' ').replace(/\\r/g, '').trimEnd();
}

async function saveExcel(){
  const dirty = [...document.querySelectorAll('#excelRows tr.dirty')];
  if(dirty.length === 0){ setEditState('변경사항이 없습니다.'); return; }
  const edits = dirty.map(tr => ({
    excel_row: parseInt(tr.dataset.row, 10),
    cells: [...tr.querySelectorAll('td.edit')].map(cellText),
  }));
  setEditState('저장 중…');
  const r = await fetch('/api/excel/update', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({file: curExcelFile, edits}),
  });
  const res = await r.json();
  if(res.ok){
    setEditState(`저장 완료 (${res.updated}행)`, '#16a34a');
    loadExcel(curExcelFile);   // 엑셀에서 다시 읽어 동기화
  } else {
    setEditState('저장 실패: ' + (res.error || ''), '#dc2626');
  }
}

async function sortExcel(){
  if(!curExcelFile){ setEditState('먼저 엑셀 파일을 선택하세요.'); return; }
  if(!confirm('일반점검 행을 일시 → 기관코드 순으로 정렬할까요?\\n(정기점검은 그대로 유지됩니다)')) return;
  setEditState('정렬 중…');
  const r = await fetch('/api/excel/sort', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({file: curExcelFile}),
  });
  const res = await r.json();
  if(res.ok){
    setEditState(`정렬 완료 (일반점검 ${res.sorted}건)`, '#16a34a');
    loadExcel(curExcelFile);
  } else {
    setEditState('정렬 실패: ' + (res.error || ''), '#dc2626');
  }
}

async function deleteRow(excelRow, btn){
  if(!confirm('이 항목을 엑셀에서 삭제할까요?')) return;
  btn.disabled = true;
  const r = await fetch('/api/excel/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({file: curExcelFile, excel_row: excelRow}),
  });
  const res = await r.json();
  if(res.ok){
    setEditState('삭제되었습니다.', '#16a34a');
    loadExcel(curExcelFile);
  } else {
    btn.disabled = false;
    setEditState('삭제 실패: ' + (res.error || ''), '#dc2626');
  }
}

(async function init(){
  await refreshStatus();
  await loadRuns();
  const sel = document.getElementById('runSelect');
  if(sel.value) loadRun(sel.value);
  // 페이지 진입 시 진행 중이면 폴링 시작
  const s = await refreshStatus();
  if(s.status === 'running' && !polling){
    polling = setInterval(async () => {
      const st = await refreshStatus();
      if(st.status === 'done' || st.status === 'error'){
        clearInterval(polling); polling = null; await loadRuns();
        if(st.run_id) loadRun(st.run_id);
      }
    }, 2000);
  }
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "SiteCheckWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 조용히
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # --- helpers ---
    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, base: Path, rel_parts: List[str], content_type: str) -> None:
        # 디렉토리 탈출 방지
        target = (base / Path(*rel_parts)).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            self.send_error(403); return
        if not target.is_file():
            self.send_error(404); return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- routes ---
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(_INDEX_HTML); return
        if path == "/api/status":
            with _run_lock:
                self._send_json(dict(RUN_STATE)); return
        if path == "/api/runs":
            self._send_json(_list_runs()); return
        if path == "/api/run":
            qs = parse_qs(parsed.query)
            rid = (qs.get("id") or [""])[0]
            data = _load_run(rid)
            if data is None:
                self._send_json({"error": "not found"}, 404); return
            self._send_json(data); return
        if path == "/api/keywords":
            self._send_json(parse_keywords_raw()); return
        if path == "/api/regular":
            self._send_json(regular_baseline()); return
        if path == "/api/excels":
            self._send_json(_list_excels()); return
        if path == "/api/excel":
            qs = parse_qs(parsed.query)
            fname = (qs.get("file") or [""])[0]
            data = _read_excel(fname)
            if data is None:
                self._send_json({"error": "not found"}, 404); return
            self._send_json(data); return
        if path.startswith("/screenshots/"):
            parts = [unquote(p) for p in path[len("/screenshots/"):].split("/") if p]
            self._send_file(SCREENSHOT_DIR, parts, "image/png"); return
        if path.startswith("/excel/"):
            parts = [unquote(p) for p in path[len("/excel/"):].split("/") if p]
            self._send_file(
                EXCEL_DIR, parts,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ); return
        self.send_error(404)

    def _read_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/scrape":
            self._send_json(start_scrape()); return
        if parsed.path == "/api/matched/delete":
            body = self._read_body()
            # 주의: `or -1` 폴백은 index=0(falsy)까지 -1로 만든다 — 명시적 None 체크
            idx = body.get("index")
            res = delete_matched_hit(body.get("run_id", ""), int(idx) if idx is not None else -1)
            self._send_json(res, 200 if res.get("ok") else 400); return
        if parsed.path == "/api/review/skip":
            body = self._read_body()
            idx = body.get("index")
            res = skip_review_hit(body.get("run_id", ""), int(idx) if idx is not None else -1)
            self._send_json(res, 200 if res.get("ok") else 400); return
        if parsed.path == "/api/excel/update":
            body = self._read_body()
            res = update_excel_rows(body.get("file", ""), body.get("edits") or [])
            self._send_json(res, 200 if res.get("ok") else 400); return
        if parsed.path == "/api/excel/delete":
            body = self._read_body()
            res = delete_excel_row(body.get("file", ""), int(body.get("excel_row", 0) or 0))
            self._send_json(res, 200 if res.get("ok") else 400); return
        if parsed.path == "/api/excel/append":
            body = self._read_body()
            res = append_excel_row(body.get("file", ""), body.get("cells") or [])
            self._send_json(res, 200 if res.get("ok") else 400); return
        if parsed.path == "/api/excel/sort":
            body = self._read_body()
            res = sort_excel_rows(body.get("file", ""))
            self._send_json(res, 200 if res.get("ok") else 400); return
        if parsed.path == "/api/regular/update":
            body = self._read_body()
            res = update_regular(body.get("items") or [])
            self._send_json(res, 200 if res.get("ok") else 400); return
        self.send_error(404)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"웹 대시보드 실행: http://{host}:{port}  (Ctrl+C 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("종료합니다.")
    finally:
        httpd.server_close()
