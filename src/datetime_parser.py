"""
공지 본문에서 점검 일시(시작/종료)를 추출.

한국 금융권 공지에서 자주 보이는 패턴:
  - 2026년 5월 19일(화) 23:00 ~ 5월 20일(수) 02:00
  - 2026.05.19(화) 23:00 ~ 2026.05.20(수) 02:00
  - 2026-05-19 23:00 ~ 2026-05-20 02:00
  - 5/19 23:00 ~ 5/20 02:00
  - 2026.5.19 23:00부터 익일 02:00까지
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional, Tuple

from dateutil import parser as du_parser


@dataclass
class MaintenanceWindow:
    start: Optional[datetime]
    end: Optional[datetime]
    raw: str

    def start_str(self) -> str:
        return self.start.strftime("%Y-%m-%d %H:%M") if self.start else ""

    def end_str(self) -> str:
        return self.end.strftime("%Y-%m-%d %H:%M") if self.end else ""


# 날짜와 시간 사이에 끼는 옵션 단어들 (새벽/오전/오후/정오 등)
_TIME_PREFIX = r"(?:새벽|아침|오전|낮|오후|저녁|밤|정오)?\s*"

_DASH = r"[~∼\-‐–—]"

# 시각 토큰: HH:MM / HH시MM분 / HH시(분 없음)  — 24시 표기도 허용
# 주의: '시' 뒤 숫자는 반드시 '분'이 붙을 때만 분으로 인정.
#   (예: '18시 2. 점검업무' 에서 다음 항목번호 '2'를 분(02)으로 오인하지 않기 위함)
_TIME = r"(?:\d{1,2}\s*:\s*\d{1,2}|\d{1,2}\s*시\s*\d{1,2}\s*분|\d{1,2}\s*시)"
# 종료가 '시간만'인지 판별 (전체 일치)
_TIME_ONLY_RE = re.compile(r"\d{1,2}\s*:\s*\d{1,2}|\d{1,2}\s*시\s*\d{1,2}\s*분|\d{1,2}\s*시")

# 날짜와 시각 사이: 일 뒤 마침표·쉼표 / (요일) / 풀 요일단어(토요일) / 새벽·오전 등
# (예: '2026년 07월 05일, 01:00', '2026.05.10 (일), 01:00' 처럼 쉼표가 끼는 경우 포함)
_MID = (
    r"\s*[.,]?\s*(?:\([^)]*\))?\s*[.,]?\s*"
    r"(?:[월화수목금토일]요일)?\s*" + _TIME_PREFIX
)

# 날짜 토큰들
# 연도(4 또는 2자리) + 월 + 일 : 2026.05.19 / 2026년 5월 19일 / 26.04.24 / 2026-05-19
_DATE_Y = r"(?:\d{4}|\d{2})\s*[년./\-]\s*\d{1,2}\s*[월./\-]\s*\d{1,2}\s*일?"
# 연도 없는 한국어 : 5월 19일
_DATE_KO = r"\d{1,2}\s*월\s*\d{1,2}\s*일"
# 연도 없는 슬래시/점 : 5/19 / 04.25
_DATE_MD = r"\d{1,2}\s*[/.]\s*\d{1,2}\s*일?"
# 어떤 날짜 토큰이든 (우선순위: 연도有 → 한국어 → 월/일)
_ANY_DATE = r"(?:" + _DATE_Y + r"|" + _DATE_KO + r"|" + _DATE_MD + r")"
# 종료 전용: 일(日)만 있는 형식 (예: '7월 19일 23:55 ~ 20일 04:00'의 '20일')
_DATE_D = r"\d{1,2}\s*일"

# 시작(날짜+시각) ~ 종료(날짜+시각 또는 시각만)
_RANGE_PATTERNS = [
    re.compile(
        r"(?P<s>" + _ANY_DATE + _MID + _TIME + r")\s*" + _DASH + r"\s*"
        # 종료가 날짜 없이 '오후12시'처럼 시간 접두어만 붙는 경우도 잡는다
        r"(?P<e>(?:(?:" + _ANY_DATE + r"|" + _DATE_D + r")" + _MID + r")?"
        + _TIME_PREFIX + _TIME + r")"
    ),
]

# 종료 토큰이 '일(日)+시각'만인지 판별 (예: '20일(월) 04:00' — 월은 시작 일자에서 상속)
_DAY_ONLY_END_RE = re.compile(
    r"^(\d{1,2})\s*일" + _MID + r"(" + _TIME + r")$"
)

# 시작 시각만 있는 경우 (~부터 / ~익일 N시까지)
_START_ONLY = re.compile(r"(?P<s>" + _ANY_DATE + _MID + _TIME + r")")

# 날짜와 시간대가 떨어져 있는 표 형식 폴백용 (예: 부산은행
# '중단일자 : 2026. 8. 9(일)' + 표 안 '00:30 ~ 08:00').
# 날짜는 오탐 방지를 위해 연도有/한국어 형식만 (5/19 같은 느슨한 형식 제외).
_DETACHED_DATE_RE = re.compile(r"(?:" + _DATE_Y + r"|" + _DATE_KO + r")")
_TIME_RANGE_RE = re.compile(
    _TIME_PREFIX + r"(" + _TIME + r")\s*" + _DASH + r"\s*"
    + _TIME_PREFIX + r"(" + _TIME + r")"
)


def _to_dt(s: str, base_year: Optional[int] = None) -> Optional[datetime]:
    """다양한 한국어/슬래시/점 날짜+시각 문자열을 datetime으로.

    명시적으로 (연도?, 월, 일, 시, 분?) 토큰을 뽑아 조립한다.
    - 연도 2자리(예: 26)는 2000년대로 보정
    - 'N시'처럼 분이 없으면 0분
    - 24:MM / 24시는 다음날 00:MM 로 환산
    """
    if not s:
        return None

    t = re.sub(r"\([^)]*\)", " ", s)                    # (요일) 제거
    t = re.sub(r"[월화수목금토일]요일", " ", t)           # 토요일 등 풀 요일단어 제거
    t = re.sub(r"(?:새벽|아침|오전|낮|오후|저녁|밤|정오)", " ", t)

    # 시각을 먼저 찾고, 날짜는 '시각 앞부분'에서만 찾는다.
    # (그래야 'MM.DD HH:MM'에서 시각의 시(時) 숫자를 일(日)로 오인하지 않음)
    tm = re.search(r"(\d{1,2})\s*[:시]\s*(\d{1,2})|(\d{1,2})\s*시", t)
    date_region = t[: tm.start()] if tm else t
    dm = re.search(
        r"(?:(\d{4}|\d{2})\s*[년./\-]\s*)?(\d{1,2})\s*[월./\-]\s*(\d{1,2})\s*일?",
        date_region,
    )
    if not dm or not tm:
        # 예외적 형식은 dateutil 로 폴백
        try:
            return du_parser.parse(re.sub(r"\s+", " ", t).strip(), fuzzy=True)
        except (ValueError, OverflowError):
            return None

    year_s, month_s, day_s = dm.group(1), dm.group(2), dm.group(3)
    if year_s is None:
        year = base_year or datetime.now().year
    else:
        year = int(year_s)
        if year < 100:
            year += 2000
    month, day = int(month_s), int(day_s)

    if tm.group(1) is not None:
        hour, minute = int(tm.group(1)), int(tm.group(2))
    else:
        hour, minute = int(tm.group(3)), 0

    add_days = 0
    if hour >= 24:
        hour -= 24
        add_days = 1

    try:
        return datetime(year, month, day, hour, minute) + timedelta(days=add_days)
    except ValueError:
        return None


def extract_window(text: str) -> Optional[MaintenanceWindow]:
    """본문 텍스트에서 점검 시작/종료 시각 추출."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)

    for pat in _RANGE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        start = _to_dt(m.group("s"))
        e_raw = m.group("e").strip()
        # 종료 토큰 앞의 시간 접두어(오전/오후/새벽 등)는 떼고 시각만 본다
        e_raw = re.sub(r"^(?:새벽|아침|오전|낮|오후|저녁|밤|정오)\s*", "", e_raw).strip()
        # 종료가 '시간만'(HH:MM / N시)이면 시작 일자에 시간만 결합
        if start and _TIME_ONLY_RE.fullmatch(e_raw):
            nums = re.findall(r"\d+", e_raw)
            hour = int(nums[0])
            minute = int(nums[1]) if len(nums) > 1 else 0
            extra = 0
            if hour >= 24:
                hour -= 24
                extra = 1
            try:
                end = start.replace(hour=hour, minute=minute,
                                    second=0, microsecond=0) + timedelta(days=extra)
                if end < start:
                    end = end + timedelta(days=1)
            except (ValueError, IndexError):
                end = None
        elif start and (dm := _DAY_ONLY_END_RE.match(e_raw)):
            # 종료가 '일(日)+시각'만 (예: '20일(월) 04:00') → 월/연도는 시작에서 상속
            day = int(dm.group(1))
            nums = re.findall(r"\d+", dm.group(2))
            hour = int(nums[0])
            minute = int(nums[1]) if len(nums) > 1 else 0
            extra = 0
            if hour >= 24:
                hour -= 24
                extra = 1
            try:
                end = start.replace(day=day, hour=hour, minute=minute,
                                    second=0, microsecond=0) + timedelta(days=extra)
                if end < start:  # 월을 넘어가는 경우 (예: 1/31 23:00 ~ 1일 04:00)
                    end = ((start.replace(day=1) + timedelta(days=32))
                           .replace(day=day, hour=hour, minute=minute,
                                    second=0, microsecond=0) + timedelta(days=extra))
            except ValueError:
                end = None
        else:
            end = _to_dt(e_raw, base_year=start.year if start else None)
            if start and end and end < start:
                end = end + timedelta(days=1)
        return MaintenanceWindow(start=start, end=end, raw=m.group(0))

    m = _START_ONLY.search(text)
    if m:
        start = _to_dt(m.group("s"))
        return MaintenanceWindow(start=start, end=None, raw=m.group(0))

    # 마지막 폴백: 날짜와 시간대가 떨어져 있는 표 형식
    # (예: 부산은행 '중단일자 : 2026. 8. 9(일)' + 표 안 '00:30 ~ 08:00')
    dm = _DETACHED_DATE_RE.search(text)
    tm = _TIME_RANGE_RE.search(text)
    if dm and tm:
        start = _to_dt(f"{dm.group(0)} {tm.group(1)}")
        if start:
            nums = re.findall(r"\d+", tm.group(2))
            hour = int(nums[0])
            minute = int(nums[1]) if len(nums) > 1 else 0
            extra = 0
            if hour >= 24:
                hour -= 24
                extra = 1
            try:
                end = start.replace(hour=hour, minute=minute,
                                    second=0, microsecond=0) + timedelta(days=extra)
                if end < start:
                    end = end + timedelta(days=1)
            except ValueError:
                end = None
            return MaintenanceWindow(start=start, end=end,
                                     raw=f"{dm.group(0)} {tm.group(0)}")

    return None


def parse_posted_date(s: str) -> Optional[date]:
    """공지 게시일 문자열을 date로 파싱.

    지원: YYYYMMDD / YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YYYY년 MM월 DD일
    """
    if not s:
        return None
    s = s.strip()
    # YYYYMMDD
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # 기타 구분자
    try:
        dt = du_parser.parse(s, fuzzy=True)
        return dt.date()
    except (ValueError, OverflowError):
        return None


def current_week_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    """오늘 기준 그 주(월~일)의 시작/종료 날짜 반환.

    예: today=2026-05-19(화) → (2026-05-18, 2026-05-24)
    """
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def is_in_current_week(d: Optional[date], today: Optional[date] = None) -> bool:
    """date 가 오늘이 속한 주(월~일) 범위에 있는지."""
    if d is None:
        return False
    start, end = current_week_bounds(today)
    return start <= d <= end


_KO_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def format_schedule(window: Optional[MaintenanceWindow]) -> str:
    """MaintenanceWindow → 'YYYY.MM.DD(요일) HH:MM ~ HH:MM' (또는 종료가 다른 날이면 종료에도 날짜 포함)."""
    if window is None or window.start is None:
        return ""
    s = window.start
    start_str = (
        f"{s.year:04d}.{s.month:02d}.{s.day:02d}({_KO_WEEKDAYS[s.weekday()]}) "
        f"{s.hour:02d}:{s.minute:02d}"
    )
    if window.end is None:
        return start_str
    e = window.end
    if e.date() == s.date():
        end_str = f"{e.hour:02d}:{e.minute:02d}"
    else:
        end_str = (
            f"{e.year:04d}.{e.month:02d}.{e.day:02d}({_KO_WEEKDAYS[e.weekday()]}) "
            f"{e.hour:02d}:{e.minute:02d}"
        )
    return f"{start_str} ~ {end_str}"
