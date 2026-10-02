"""Step 1 — 정규화·식별 해시 (docs/sitecheck-api-spec.md §4, 2026-09-28 개정판).

구현 대상: src/notice_identity.py
  FIELDS, CHECK_TYPES, norm_text(), norm_identity(), identity_hash(), identity_from_cells(), notice_key()

두 가지 정규화가 있다:
  norm_text     : 저장용. NFC → 개행 통일 → 앞뒤 공백 제거. 대소문자는 그대로(원문 보존).
  norm_identity : 비교용. norm_text 뒤에 casefold, 다시 NFC. 해시·후보·LINK·결정 비교에만 쓴다.
"""
import unicodedata

from src.notice_identity import (CHECK_TYPES, FIELDS, identity_from_cells,
                                 identity_hash, norm_identity, norm_text,
                                 notice_key)

# 명세 §4의 예시 공지와 그 해시. 이 값이 맞으면 API 서버와 같은 해시를 계산하는 것이다.
SPEC_ITEM = {
    "checkType": "일반점검",
    "institutionCode": "KRBK0101",
    "institutionName": "저축은행중앙회",
    "scheduleText": "2026.09.19(토) 00:00 ~ 06:00",
    "serviceText": "통합금융정보시스템 전체 업무",
    "reasonText": "코어뱅킹 긴급 시스템 작업",
    "windowStart": "2026-09-19T00:00:00+09:00",
    "windowEnd": "2026-09-19T06:00:00+09:00",
}
SPEC_HASH = "7e519b200e8e52706022d94c18fe6fa7cad3b76137192ae2f1d324f50ad6d531"
# serviceText 가 'A사이트 점검' 이든 'a사이트 점검' 이든 같은 해시 (명세 §4)
SPEC_HASH_A_SITE = "ec65df74401d7ade40072621bbd7b12c75541d00da714f8ad98c01308f3a5d60"


# --- 상수 -------------------------------------------------------------------

def test_fields_order_matches_spec():
    # 해시는 이 5개를 이 순서로 JSON 배열에 넣어 계산한다 (사유·시각 제외)
    assert FIELDS == ("checkType", "institutionCode", "institutionName",
                      "scheduleText", "serviceText")


def test_check_types():
    assert set(CHECK_TYPES) == {"일반점검", "정기점검"}


# --- norm_text (저장용) ------------------------------------------------------

def test_norm_text_none_and_empty():
    assert norm_text(None) == ""
    assert norm_text("") == ""


def test_norm_text_nfc():
    nfd = unicodedata.normalize("NFD", "저축은행중앙회")   # macOS 파일명·일부 입력은 NFD
    assert nfd != "저축은행중앙회"                          # 바이트가 다름을 확인
    assert norm_text(nfd) == "저축은행중앙회"


def test_norm_text_unifies_newlines():
    assert norm_text("첫줄\r\n둘째줄\r셋째줄") == "첫줄\n둘째줄\n셋째줄"


def test_norm_text_strips_only_edges():
    # 앞뒤의 탭·개행·수직탭·폼피드·CR·공백만 제거. 내부 공백은 그대로.
    assert norm_text(" \t\n\v\f\r일반점검 \n") == "일반점검"
    assert norm_text("전체  업무") == "전체  업무"        # 내부 이중 공백 유지
    assert norm_text("00:00 ~ 06:00") != norm_text("00:00~06:00")   # 표기 차이는 다른 일시


def test_norm_text_keeps_case():
    # 저장·전송·되쓰기 값은 원문 대소문자를 보존한다
    assert norm_text(" KRBK0101 ") == "KRBK0101"
    assert norm_text("A사이트 점검") == "A사이트 점검"


# --- norm_identity (비교용) -------------------------------------------------

def test_norm_identity_casefolds_after_norm_text():
    assert norm_identity(" A사이트 점검\r\n") == "a사이트 점검"
    assert norm_identity("KRBK0101") == "krbk0101"
    assert norm_identity(None) == ""


def test_norm_identity_is_nfc_after_casefold():
    # casefold 결과를 다시 NFC 로 정리한다 (명세: "casefold를 적용하고 다시 NFC로 정리")
    v = norm_identity(unicodedata.normalize("NFD", "É사이트"))
    assert v == unicodedata.normalize("NFC", v) == "é사이트"


# --- identity_hash -------------------------------------------------------------

def test_identity_hash_matches_spec_vector():
    assert identity_hash(SPEC_ITEM) == SPEC_HASH


def test_reason_and_windows_do_not_affect_hash():
    changed = dict(SPEC_ITEM, reasonText="담당자가 고친 사유",
                   windowStart=None, windowEnd="2026-09-19T07:00:00+09:00")
    assert identity_hash(changed) == SPEC_HASH


def test_letter_case_does_not_affect_hash():
    # 영문 대소문자만 다르면 같은 공지 (명세 §4 두 번째 벡터)
    upper = dict(SPEC_ITEM, serviceText="A사이트 점검")
    lower = dict(SPEC_ITEM, serviceText="a사이트 점검")
    assert identity_hash(upper) == identity_hash(lower) == SPEC_HASH_A_SITE
    assert identity_hash(dict(SPEC_ITEM, institutionCode="krbk0101")) == SPEC_HASH


def test_schedule_text_change_changes_hash():
    changed = dict(SPEC_ITEM, scheduleText="2026.09.19(토) 00:00 ~ 07:00")
    assert identity_hash(changed) != SPEC_HASH


def test_hash_normalizes_values_before_hashing():
    # 앞뒤 공백·NFD 가 섞여 들어와도 같은 공지
    messy = dict(SPEC_ITEM,
                 checkType=" 일반점검\n",
                 institutionName=unicodedata.normalize("NFD", "저축은행중앙회"),
                 scheduleText="2026.09.19(토) 00:00 ~ 06:00\r\n")
    assert identity_hash(messy) == SPEC_HASH


def test_hash_only_needs_the_five_fields():
    five = {k: SPEC_ITEM[k] for k in FIELDS}
    assert identity_hash(five) == SPEC_HASH


# --- identity_from_cells --------------------------------------------------------

def test_identity_from_cells_maps_b_to_g_in_order():
    cells = ["일반점검 ", " KRBK0101", "저축은행중앙회",
             "2026.09.19(토) 00:00 ~ 06:00\n", "통합금융정보시스템 전체 업무",
             "코어뱅킹 긴급 시스템 작업"]
    ident = identity_from_cells(cells)
    assert ident == {
        "checkType": "일반점검",
        "institutionCode": "KRBK0101",          # 원문 대소문자 그대로 (casefold 는 해시 안에서만)
        "institutionName": "저축은행중앙회",
        "scheduleText": "2026.09.19(토) 00:00 ~ 06:00",
        "serviceText": "통합금융정보시스템 전체 업무",
        "reasonText": "코어뱅킹 긴급 시스템 작업",
    }
    assert identity_hash(ident) == SPEC_HASH


def test_identity_from_cells_pads_missing_and_none():
    # openpyxl 은 빈 셀을 None 으로 준다. 6칸 미만이면 빈 문자열로 채운다.
    ident = identity_from_cells(["정기점검", "KRBK0102", None])
    assert ident["institutionName"] == ""
    assert ident["scheduleText"] == ""
    assert ident["serviceText"] == ""
    assert ident["reasonText"] == ""
    assert len(ident) == 6


# --- notice_key ------------------------------------------------------------------

def test_notice_key_is_type_code_schedule_casefolded():
    # LINK 허용 범위: 식별 정규화한 구분·기관코드·일시가 같은 공지끼리만 연결할 수 있다
    assert notice_key(SPEC_ITEM) == ("일반점검", "krbk0101", "2026.09.19(토) 00:00 ~ 06:00")


def test_notice_key_normalizes():
    raw = dict(SPEC_ITEM, checkType=" 일반점검", institutionCode="krbk0101",
               scheduleText="2026.09.19(토) 00:00 ~ 06:00 ")
    assert notice_key(raw) == notice_key(SPEC_ITEM)
    other = dict(SPEC_ITEM, serviceText="다른 업무")   # 업무가 달라도 같은 공지 후보 범위
    assert notice_key(other) == notice_key(SPEC_ITEM)
