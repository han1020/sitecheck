"""감지 목록에서 삭제한 항목의 영구 목록 (config/matched_deletes.yaml).

대시보드 '감지 목록' 탭에서 🗑 버튼을 누르면 (기관코드, 제목, 일시) 키가 이
파일에 저장되고, 같은 시각에 해당 엑셀 행도 삭제된다. 이후 수집에서 같은
공지가 다시 감지되어도 엑셀·감지 목록에 되살리지 않는다 (스킵 탭에 기록).

일시(schedule)까지 키에 포함하므로, 같은 제목으로 매달 올라오는 공지라도
새 점검 일시가 다르면 정상적으로 다시 감지된다.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger(__name__)

_file_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def load_matched_deletes(path: Path) -> List[dict]:
    """삭제 목록 로드. 파일 없으면 빈 리스트."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"matched_deletes 읽기 실패({path}): {e}")
        return []
    items = data.get("deletes") or []
    return [i for i in items if isinstance(i, dict) and i.get("site_code") and i.get("title")]


def add_matched_delete(path: Path, site_code: str, title: str,
                       schedule: str = "") -> bool:
    """삭제 목록에 추가. 이미 있으면 False."""
    title = _norm(title)
    schedule = _norm(schedule)
    if not site_code or not title:
        return False
    with _file_lock:
        items = load_matched_deletes(path)
        if any(i["site_code"] == site_code and _norm(i["title"]) == title
               and _norm(i.get("schedule", "")) == schedule
               for i in items):
            return False
        items.append({
            "site_code": site_code,
            "title": title,
            "schedule": schedule,
            "deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        lines = ["# 감지 목록 영구 삭제 목록 — 대시보드 🗑 버튼으로 추가됨", "deletes:"]
        for i in items:
            lines.append(f"  - site_code: {i['site_code']}")
            lines.append(f"    title: {_yaml_str(i['title'])}")
            lines.append(f"    schedule: {_yaml_str(str(i.get('schedule', '')))}")
            lines.append(f"    deleted_at: {_yaml_str(str(i.get('deleted_at', '')))}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _yaml_str(s: str) -> str:
    import json
    return json.dumps(s, ensure_ascii=False)


def is_matched_deleted(deletes: List[dict], site_code: str, title: str,
                       schedule: str = "") -> bool:
    """(기관코드, 제목)이 같고, 목록의 일시가 있으면 일시까지 같아야 매칭."""
    t = _norm(title)
    sched = _norm(schedule)
    for i in deletes:
        if i["site_code"] != site_code or _norm(i["title"]) != t:
            continue
        entry_sched = _norm(i.get("schedule", ""))
        if not entry_sched or entry_sched == sched:
            return True
    return False


def filter_deleted_hits(hits, deletes: List[dict], skips_log=None,
                        delete_shots: bool = True) -> list:
    """삭제 목록에 있는 감지 hit 제거 (needs_review 항목은 대상 아님).

    제거된 건은 skips_log(수집 런의 '스킵' 탭 기록)에 남기고, 이미 찍힌
    스크린샷 파일은 지워 고아 파일을 남기지 않는다.
    """
    if not deletes:
        return list(hits)
    kept = []
    for h in hits:
        if (h.title and not getattr(h, "needs_review", False)
                and is_matched_deleted(deletes, h.site_code, h.title,
                                       h.schedule_text or "")):
            logger.info(f"[{h.site_code}] 감지 삭제 목록에 있어 제외: {h.title}")
            if skips_log is not None:
                skips_log.append({
                    "site_code": h.site_code,
                    "site_name": h.site_name,
                    "category": h.category,
                    "title": h.title,
                    "why": "감지 목록에서 삭제 처리한 공지",
                    "detail_url": h.detail_url,
                    "posted_date": h.posted_date,
                })
            if delete_shots and h.screenshot_path:
                try:
                    p = Path(h.screenshot_path)
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
            continue
        kept.append(h)
    return kept


def filter_deleted_carried(carried, deletes: List[dict]) -> list:
    """이전 엑셀에서 carryover된 행 중 삭제 목록과 (기관코드, 일시)가 일치하는
    행 제거. carryover 행에는 제목이 없으므로 일시가 기록된 항목만 대조한다."""
    if not deletes:
        return list(carried)
    keys = {(i["site_code"], _norm(i.get("schedule", "")))
            for i in deletes if _norm(i.get("schedule", ""))}
    kept = []
    for c in carried:
        if (c.code, _norm(c.schedule)) in keys:
            logger.info(f"[{c.code}] 감지 삭제 목록에 있어 carryover 제외: {c.schedule}")
            continue
        kept.append(c)
    return kept
