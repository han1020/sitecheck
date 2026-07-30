"""'검토 필요' 항목의 영구 스킵 목록 (config/review_skips.yaml).

대시보드 '검토 필요' 탭에서 스킵 버튼을 누르면 (기관코드, 제목) 키가 이
파일에 저장되고, 이후 수집에서 같은 공지가 다시 needs_review 로 잡혀도
검토 목록에 올리지 않는다 (스킵 탭에 사유와 함께 기록).

제목에 보통 날짜가 들어가므로("...(7/18)") 같은 기관이 새 점검을 같은
제목으로 내는 경우는 드물다 — 새 공지는 정상적으로 다시 검토에 뜬다.
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


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip()


def load_review_skips(path: Path) -> List[dict]:
    """스킵 목록 로드. 파일 없으면 빈 리스트."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"review_skips 읽기 실패({path}): {e}")
        return []
    items = data.get("skips") or []
    return [i for i in items if isinstance(i, dict) and i.get("site_code") and i.get("title")]


def add_review_skip(path: Path, site_code: str, title: str,
                    posted_date: str = "") -> bool:
    """스킵 목록에 추가. 이미 있으면 False."""
    title = _norm_title(title)
    if not site_code or not title:
        return False
    with _file_lock:
        items = load_review_skips(path)
        if any(i["site_code"] == site_code and _norm_title(i["title"]) == title
               for i in items):
            return False
        items.append({
            "site_code": site_code,
            "title": title,
            "posted_date": str(posted_date or ""),
            "skipped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        lines = ["# '검토 필요' 영구 스킵 목록 — 대시보드 스킵 버튼으로 추가됨", "skips:"]
        for i in items:
            lines.append(f"  - site_code: {i['site_code']}")
            lines.append(f"    title: {_yaml_str(i['title'])}")
            lines.append(f"    posted_date: {_yaml_str(str(i.get('posted_date', '')))}")
            lines.append(f"    skipped_at: {_yaml_str(str(i.get('skipped_at', '')))}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _yaml_str(s: str) -> str:
    import json
    return json.dumps(s, ensure_ascii=False)


def is_review_skipped(skips: List[dict], site_code: str, title: str) -> bool:
    t = _norm_title(title)
    return any(i["site_code"] == site_code and _norm_title(i["title"]) == t
               for i in skips)


def filter_review_hits(hits, skips: List[dict], skips_log=None,
                       delete_shots: bool = True) -> list:
    """스킵 목록에 있는 needs_review hit 제거.

    제거된 건은 skips_log(수집 런의 '스킵' 탭 기록)에 남기고, 이미 찍힌
    스크린샷 파일은 지워 고아 파일을 남기지 않는다.
    """
    if not skips:
        return list(hits)
    kept = []
    for h in hits:
        if h.needs_review and is_review_skipped(skips, h.site_code, h.title):
            logger.info(f"[{h.site_code}] 검토 스킵 목록에 있어 제외: {h.title}")
            if skips_log is not None:
                skips_log.append({
                    "site_code": h.site_code,
                    "site_name": h.site_name,
                    "category": h.category,
                    "title": h.title,
                    "why": "검토 필요 탭에서 스킵 처리한 공지",
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
