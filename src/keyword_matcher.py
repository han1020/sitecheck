from __future__ import annotations

import re
from typing import Optional

from .config_loader import KeywordConfig


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def maintenance_verdict(
    title: str,
    keywords: KeywordConfig,
    reason_text: Optional[str] = None,
    service_text: Optional[str] = None,
    body_text: Optional[str] = None,
) -> tuple[bool, str]:
    """점검 공지 여부 판별 + 제외된 경우 그 이유.

    반환: (점검 공지 여부, 제외 사유)
      - (True, "")               : 점검 공지
      - (False, "제외 키워드 …") : include 매칭됐으나 exclude 류에 걸림 — 스킵 목록에 노출할 가치가 있다
      - (False, "")              : include 키워드 자체가 없음 (일반 공지 — 기록 불필요)

    - force_include_service 키워드: 업무(service) 라벨 값에 있으면 include/exclude
        검사와 무관하게 무조건 True (예: '인터넷뱅킹' — 핵심 채널 영향 공지는 놓치지 않는다)
    - include 키워드: 제목에 하나라도 포함되어야 True
    - exclude_title 키워드: 제목에만 매칭. 라벨 값에 정상적으로 등장할 수 있는
        단어용 (예: 'CD공동망'은 광주은행 업무 값 '전자금융공동망, CD공동망 …'에
        들어가는데, exclude에 두면 그 점검 공지가 통째로 빠진다).
    - exclude 키워드: 제목 또는 사유(reason)·업무(service) 라벨 값에 포함되면 False
        (영향받는 서비스 목록 등에 우연히 들어간 단어로 잘못 차단되지 않도록
         라벨 값만 검사하고 본문 전체는 보지 않는다. 예: 하나증권 '스탁론'은
         제목·사유가 아니라 업무 라벨에만 등장하므로 업무 라벨도 함께 검사한다.)
    - exclude_body 키워드: 본문 전체에서 매칭되면 False
        (라벨에 안 잡히는 케이스용. 예: 미래에셋 '검색·콘텐츠만 일시정지'는
         '투자정보 콘텐츠'가 본문에만 등장. 오탐 위험이 커서 충분히 특이한
         문구만 exclude_body에 둔다.)
    """
    t = normalize(title)
    if not t:
        return False, ""

    has_include = any(normalize(kw) in t for kw in keywords.include)

    r = normalize(reason_text) if reason_text else ""
    s = normalize(service_text) if service_text else ""

    # 업무 라벨에 force_include_service 키워드(예: 인터넷뱅킹)가 있으면
    # 어떤 제외 검사에도 걸리지 않고 무조건 점검 공지로 판단한다.
    # 핵심 채널이 영향 업무에 명시된 공지는 놓치면 안 된다는 운영 요구.
    if s and any(normalize(kw) in s for kw in keywords.force_include_service):
        return True, ""

    for ex in keywords.exclude_title:
        if normalize(ex) in t:
            return False, (f"제외 키워드 '{ex}' (제목 전용)" if has_include else "")

    for ex in keywords.exclude:
        ex_n = normalize(ex)
        if ex_n in t:
            return False, (f"제외 키워드 '{ex}' (제목)" if has_include else "")
        if r and ex_n in r:
            return False, (f"제외 키워드 '{ex}' (사유 라벨)" if has_include else "")
        if s and ex_n in s:
            return False, (f"제외 키워드 '{ex}' (업무 라벨)" if has_include else "")

    b = normalize(body_text) if body_text else ""
    if b:
        for ex in keywords.exclude_body:
            if normalize(ex) in b:
                return False, (f"제외 키워드 '{ex}' (본문)" if has_include else "")

    return (True, "") if has_include else (False, "")


def carryover_excluded(
    reason: str, keywords: KeywordConfig, service: Optional[str] = None,
) -> Optional[str]:
    """이전 엑셀에서 이어오는(carryover) 행을 현행 exclude 규칙으로 다시 걸러야 하는지.

    엑셀 행에는 제목이 없고 사유·업무 컬럼만 있으며, 사유는 사용자가 손으로 고친
    값('네트워크 신규 장비 구성' 등)일 수 있어 include 키워드가 없는 게 정상이다.
    그래서 is_maintenance() 처럼 include 유무로 판단하면 안 되고, exclude 류 키워드가
    실제로 매칭될 때만 제거한다. (2026-08-21: 저축은행중앙회 행이 include 미스로
    탈락 → 재감지되어 노란색 신규로 되살아나며 수정분을 잃던 문제)

    반환: 걸린 키워드 설명 문자열, 안 걸리면 None.
    """
    r = normalize(reason)
    s = normalize(service) if service else ""
    if not r and not s:
        return None
    # 사유 컬럼은 원래 제목(또는 제목에서 정제한 값)일 수 있으므로 제목 전용 키워드도 본다
    for ex in keywords.exclude_title:
        if r and normalize(ex) in r:
            return f"제외 키워드 '{ex}' (제목 전용, 사유 컬럼)"
    for ex in keywords.exclude:
        ex_n = normalize(ex)
        if r and ex_n in r:
            return f"제외 키워드 '{ex}' (사유 컬럼)"
        if s and ex_n in s:
            return f"제외 키워드 '{ex}' (업무 컬럼)"
    return None


def title_forces_review(title: str, keywords: KeywordConfig) -> bool:
    """제목에 강제 검토 마커([중요] 등)가 있는지 (keywords.yaml force_review_title).

    마커가 있는 공지는 어떤 필터에 걸려도 조용히 버리지 않고 '검토 필요'로 보낸다.
    """
    t = normalize(title)
    if not t:
        return False
    return any(normalize(k) in t for k in keywords.force_review_title if k)


def is_maintenance(
    title: str,
    keywords: KeywordConfig,
    reason_text: Optional[str] = None,
    service_text: Optional[str] = None,
    body_text: Optional[str] = None,
) -> bool:
    """점검 공지 여부만 필요할 때의 래퍼. 상세 사유는 maintenance_verdict() 참고."""
    ok, _ = maintenance_verdict(title, keywords, reason_text, service_text, body_text)
    return ok


_SUFFIXES = ["은행", "카드", "증권", "투자증권", "금융", "보험"]

# 명부에 넣을 기관명의 최소 길이. 'BC'·'신협'처럼 짧은 이름은 다른 단어에
# 우연히 포함돼 오탐을 낸다.
_MIN_ROSTER_LEN = 3


def own_core(site_name: str) -> str:
    """기관명에서 접미사를 뗀 핵심어. 예: 'NH저축은행' → 'nh저축', '농협은행' → '농협'."""
    for sfx in _SUFFIXES:
        if site_name.endswith(sfx) and len(site_name) > len(sfx):
            return normalize(site_name[: -len(sfx)])
    return ""


def own_name_variants(site_name: str, aliases: Optional[list[str]] = None) -> set[str]:
    """자기 기관으로 인정할 표기들 (기관명 + 별칭 + 접미사 제거한 핵심어).

    예: 'KEB하나은행' → {'keb하나은행', 'keb하나'}, 'NH저축은행' → {'nh저축은행', 'nh저축'}
    """
    candidates = [site_name]
    if aliases:
        candidates.extend(aliases)
    core = own_core(site_name)
    if core:
        candidates.append(core)

    return {n for n in (normalize(c) for c in candidates) if n}


def is_own_institution_notice(
    body_text: Optional[str],
    site_name: str,
    aliases: Optional[list[str]] = None,
) -> bool:
    """본문이 '자기 기관 자신의 점검'에 관한 것인지 판단.

    True  : 본문에 자기 기관명/별칭이 명시적으로 등장 (자기 점검 가능성)
    False : 본문이 비어있거나 자기 기관명이 전혀 안 보임 (외부 기관 안내 가능성)

    본문 어딘가에 기관명이 한 번이라도 나오면 True 이므로, '저희 대신증권을
    이용해 주셔서'(인사말)나 피해 기관 언급만으로도 통과한다. 점검 '주체'까지
    보려면 external_subject() 를 함께 쓸 것.
    """
    if not body_text:
        # 본문이 비어있으면 판별 불가 → 보수적으로 True (자기 점검으로 간주)
        return True

    text = normalize(body_text)
    return any(c in text for c in own_name_variants(site_name, aliases))


def build_institution_roster(
    site_names: list[str],
    extra_names: Optional[list[str]] = None,
) -> set[str]:
    """외부기관 감지에 쓸 기관명 명부."""
    names = list(site_names) + list(extra_names or [])
    return {n.strip() for n in names if n and len(n.strip()) >= _MIN_ROSTER_LEN}


def external_subject(
    title: str,
    site_name: str,
    aliases: Optional[list[str]],
    roster: set[str],
) -> str:
    """제목이 '다른 기관의 작업' 안내이면 그 기관명을, 아니면 빈 문자열을 반환.

    판정 우선순위:
      1. 제목에 자기 기관 '정식명/별칭'이 있으면 자기 점검. ('한국산업은행 …'
         → 산업은행. 명부의 더 긴 이름에 지지 않아야 한다.)
      2. 그 외에는 명부에서 가장 긴 기관명을 찾는다. 자기 기관 '핵심어'만
         걸린 경우, 더 긴 외부 기관명이 있으면 그쪽을 점검 주체로 본다.
         ('NH농협카드 …' → 농협은행의 핵심어 '농협'보다 '농협카드'가 길다.)
    """
    t = normalize(title)
    if not t:
        return ""

    full_names = {n for n in (normalize(c) for c in [site_name, *(aliases or [])]) if n}
    if any(n in t for n in full_names):
        return ""

    owns = own_name_variants(site_name, aliases)
    # 긴 이름 우선 — '한국씨티은행'이 '씨티은행'보다 먼저 매칭되도록
    best = ""
    for other in sorted(roster, key=len, reverse=True):
        o = normalize(other)
        if o and o in t and o not in owns:
            best = other
            break
    if not best:
        return ""

    core = own_core(site_name)
    if core and core in t and len(normalize(best)) <= len(core):
        # 핵심어가 외부 기관명보다 구체적이면 자기 점검으로 둔다
        return ""
    return best
