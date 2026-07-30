"""감지된 공지를 정기점검 baseline(regular_maintenance.yaml)과 대조.

baseline의 schedule 문구("매월 첫째 주 토요일 23:00 ~ 02:00" 등)를 반복
패턴으로 파싱해, 감지 건의 점검 시작/종료 시각과 비교한다. 일치하면
'이미 baseline에 있는 정기점검'이므로 엑셀 신규 목록에서 제외하고
대시보드 감지목록에는 체크 표시로 구분한다.

지원 패턴:
  - "매주 <요일>요일 HH:MM ~ HH:MM"
  - "매월 <첫째|둘째|...>[ 주][, <서수> 주 ...] <요일>요일 HH:MM ~ HH:MM"
  - "홀수월/짝수월 <서수> <요일>요일 HH:MM ~ HH:MM"
괄호 주석("(4시간)" 등)은 무시. '전일'처럼 시작일이 어긋나는 표현이나
파싱 불가능한 문구는 보수적으로 '매칭 안 됨' 처리한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .config_loader import RegularEntry

logger = logging.getLogger(__name__)

_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_ORDINALS = {"첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5}

_SCHEDULE_RE = re.compile(
    r"^(매주|매월|홀수월|짝수월)\s*(.*?)([월화수목금토일])요일\s*"
    r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})$"
)


@dataclass
class RecurrencePattern:
    weekday: int                 # 0=월 ~ 6=일
    start_hm: tuple[int, int]
    end_hm: tuple[int, int]
    weeks: frozenset[int]        # 몇째 주 (비어있으면 매주)
    month_parity: Optional[int]  # 1=홀수월, 0=짝수월, None=매월


def parse_schedule(text: str) -> Optional[RecurrencePattern]:
    """baseline schedule 문구 → 반복 패턴. 파싱 불가면 None (매칭 포기)."""
    if not text or "전일" in text:
        return None
    s = re.sub(r"\([^)]*\)", " ", text)     # 괄호 주석 제거
    s = re.sub(r"\s+", " ", s).strip()
    m = _SCHEDULE_RE.match(s)
    if not m:
        return None
    freq, ordinal_part, wd, sh, sm, eh, em = m.groups()

    weeks = frozenset(_ORDINALS[o] for o in re.findall(
        "|".join(_ORDINALS), ordinal_part))
    if freq == "매주":
        if weeks:
            return None
        month_parity = None
    else:
        # 매월/홀수월/짝수월은 몇째 주 지정이 있어야 특정 날짜와 대조 가능
        if not weeks:
            return None
        month_parity = {"홀수월": 1, "짝수월": 0}.get(freq)

    return RecurrencePattern(
        weekday=_WEEKDAYS[wd],
        start_hm=(int(sh), int(sm)),
        end_hm=(int(eh), int(em)),
        weeks=weeks,
        month_parity=month_parity,
    )


def window_matches(p: RecurrencePattern,
                   start: Optional[datetime],
                   end: Optional[datetime]) -> bool:
    """감지 건의 점검 시작/종료 시각이 반복 패턴의 한 회차인지."""
    if start is None or end is None:
        return False
    if start.weekday() != p.weekday:
        return False
    if (start.hour, start.minute) != p.start_hm:
        return False
    if (end.hour, end.minute) != p.end_hm:
        return False
    if p.weeks and ((start.day - 1) // 7 + 1) not in p.weeks:
        return False
    if p.month_parity is not None and start.month % 2 != p.month_parity:
        return False
    return True


def find_regular_match(site_code: str,
                       start: Optional[datetime],
                       end: Optional[datetime],
                       regular: List[RegularEntry]) -> Optional[RegularEntry]:
    """감지 건과 일치하는 baseline 항목을 반환 (없으면 None)."""
    for r in regular:
        if r.code != site_code:
            continue
        p = parse_schedule(r.schedule)
        if p and window_matches(p, start, end):
            return r
    return None


def mark_regular_hits(hits, regular: List[RegularEntry]) -> int:
    """정기점검 baseline과 일치하는 hit에 is_regular/regular_reason을 표시."""
    n = 0
    for h in hits:
        if not h.title or h.needs_review or not h.window:
            continue
        r = find_regular_match(h.site_code, h.window.start, h.window.end, regular)
        if r:
            h.is_regular = True
            h.regular_reason = r.reason or r.schedule
            n += 1
            logger.info(
                f"[{h.site_code}] 정기점검 baseline 일치({r.schedule}) → "
                f"엑셀 신규 제외: {h.title}"
            )
    return n
