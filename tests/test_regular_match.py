"""정기점검 baseline 대조: 날짜가 있는 공지는 시각으로, 반복 문구만 있는 공지는 문구로."""
from __future__ import annotations

from src.config_loader import RegularEntry
from src.datetime_parser import extract_window
from src.regular_match import find_regular_by_text, mark_regular_hits
from src.scraper import NoticeHit

NH = RegularEntry(code="KRBK0027", name="농협은행",
                  schedule="매월 셋째 일요일 23:55(전일) ~ 04:00 (단, 월요일이 공휴일인 경우 익영업일)",
                  service="전체 업무", reason="정기 점검")
SB = RegularEntry(code="KRBK0101", name="저축은행중앙회",
                  schedule="매월 셋째 주 일요일 00:00 ~ 06:00", service="전체", reason="정기")


def hit(code: str, schedule: str) -> NoticeHit:
    return NoticeHit(site_code=code, site_name="", category="BK", title="시스템 점검 안내",
                     posted_date="2026-11-10", detail_url="", screenshot_path="",
                     window=extract_window(schedule), schedule_text=schedule)


def test_recurring_phrase_without_date_matches_baseline_text():
    h = hit("KRBK0027", "매월 셋째 일요일 23:55(전일) ~ 04:00 (단, 월요일이 공휴일인 경우 익영업일)")
    assert h.window is None                                  # 날짜가 없어 시각 대조 불가
    assert mark_regular_hits([h], [NH, SB]) == 1
    assert h.is_regular and h.regular_reason == "정기 점검"


def test_text_match_ignores_parenthesis_and_spacing():
    assert find_regular_by_text("KRBK0027", "매월 셋째 일요일 23:55 ~ 04:00", [NH]) is NH
    assert find_regular_by_text("KRBK0027", "매월셋째일요일 23:55~04:00 (익영업일)", [NH]) is NH


def test_text_match_needs_same_site_and_same_phrase():
    assert find_regular_by_text("KRBK0101", NH.schedule, [NH]) is None
    assert find_regular_by_text("KRBK0027", "매월 둘째 일요일 23:55 ~ 04:00", [NH]) is None
    assert find_regular_by_text("KRBK0027", "", [NH]) is None


def test_dated_notice_is_still_matched_by_time():
    third_sunday = hit("KRBK0101", "2026.11.15(일) 00:00 ~ 06:00")     # 2026-11 셋째 일요일
    other = hit("KRBK0101", "2026.11.15(일) 00:00 ~ 07:00")
    assert mark_regular_hits([third_sunday, other], [SB]) == 1
    assert third_sunday.is_regular and not other.is_regular
