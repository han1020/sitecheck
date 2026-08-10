from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urljoin

from playwright.async_api import (
    Browser,
    Page,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

from .config_loader import KeywordConfig, SiteConfig
from .datetime_parser import (
    MaintenanceWindow,
    extract_windows,
    format_schedule,
)
from .handlers import get_handler, has_handler
from .handlers.base import HandlerResult
from .keyword_matcher import (
    build_institution_roster, external_subject, is_maintenance,
    is_own_institution_notice, maintenance_verdict, normalize,
)

logger = logging.getLogger(__name__)


@dataclass
class NoticeHit:
    site_code: str
    site_name: str
    category: str
    title: str
    posted_date: str
    detail_url: str
    screenshot_path: str
    window: Optional[MaintenanceWindow]
    schedule_text: str = ""   # 본문에서 추출한 점검 일시 원문 (예: '2026.06.07(일) 00:00 ~ 07:00')
    service_text: str = ""    # 영향받는 업무
    reason_text: str = ""     # 사유
    error: str = ""
    body_text: str = ""       # 공지 본문 텍스트 — 대시보드에서 제목 클릭 시 표시 (스크린샷 실패 대비)
    needs_review: bool = False  # 제목은 점검 공지인데 본문이 비어(이미지 공지 등) 자동 판별 불가 → 사람이 검토
    via_ocr: bool = False     # 이미지 공지를 OCR로 읽어 자동 감지 — 오탈자 가능성 있어 대시보드에서 구분 표시
    is_regular: bool = False  # 정기점검 baseline과 일시가 일치 — 엑셀 신규에선 제외, 대시보드엔 체크 표시
    regular_reason: str = ""  # 일치한 baseline의 사유 (대시보드 툴팁용)


def _review_external(subject: str, keywords: KeywordConfig) -> bool:
    """외부기관 작업 공지를 버리지 않고 '검토 필요'로 보낼 기관인지 (keywords.yaml external_review)."""
    s = normalize(subject)
    if not s:
        return False
    return any(normalize(k) in s for k in keywords.external_review if k)


def _note_skip(skips: Optional[List[dict]], site: SiteConfig, title: str,
               why: str, detail_url: str = "", posted_date: str = "") -> None:
    """스킵된 공지를 기록 (웹 대시보드 '스킵' 탭용). skips가 None이면 무시."""
    if skips is None:
        return
    skips.append({
        "site_code": site.code,
        "site_name": site.name,
        "category": site.category,
        "title": title,
        "why": why,
        "detail_url": detail_url or "",
        "posted_date": posted_date or "",
    })


_REVIEW_MAX_AGE_DAYS = 60  # 검토 대상: 등록일이 이보다 오래되면 옛 공지로 보고 제외


def _posted_too_old(posted_date: str, max_age_days: int = _REVIEW_MAX_AGE_DAYS) -> bool:
    """등록일(YYYYMMDD/YYYY.MM.DD 등)이 max_age_days 이전이면 True.

    검토 필요 항목은 본문이 비어 점검 일시를 알 수 없으므로, 등록일이 오래된
    공지(예: 몇 년 전 공지가 상단에 고정)를 자동 판별 대상에서 제외하는 데 쓴다.
    파싱 불가하면 False(제외하지 않음)로 안전하게 처리.
    """
    digits = re.sub(r"\D", "", posted_date or "")[:8]
    if len(digits) != 8:
        return False
    try:
        posted = datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return False
    return (datetime.now() - posted).days > max_age_days


def strip_already_collected(hits: List["NoticeHit"], carried) -> List["NoticeHit"]:
    """carryover(이전 실행에서 이미 수집)와 동일 키의 신규 hit을 감지목록에서 제외하고
    해당 스크린샷 파일을 삭제한다.

    - 대상: 제목이 있는 '신규 점검' hit 중 (기관코드, 일시)가 carryover와 겹치는 것.
    - 검토(needs_review)·에러 hit, 겹치지 않는 신규 hit은 그대로 유지.
    - 엑셀은 스크린샷 파일을 참조하지 않으므로 삭제해도 무방하며, 엑셀에는 여전히
      (흰색으로) carryover 행이 남는다. 이 함수는 '감지목록/JSON 표시'와 파일 정리용이다.
    """
    carried_keys = {(c.code, (c.schedule or "").strip()) for c in (carried or [])}
    kept: List["NoticeHit"] = []
    for h in hits:
        if h.title and not h.needs_review and not h.error:
            key = (h.site_code, (h.schedule_text or "").strip())
            if key in carried_keys:
                if h.screenshot_path:
                    try:
                        Path(h.screenshot_path).unlink()
                    except OSError:
                        pass
                logger.info(
                    f"[{h.site_code}] 이미 수집된 항목 → 감지목록 제외 & 스샷 삭제: {h.title}"
                )
                continue
        kept.append(h)
    return kept


def _future_windows(windows: List[MaintenanceWindow], now: datetime) -> List[MaintenanceWindow]:
    """종료(없으면 시작) 시각이 아직 지나지 않은 창만 남긴다.

    한 공지에 일시가 여러 개 나열된 경우(예: 씨티은행 8/2·8/9), 첫 창이
    지났다고 공지를 통째로 버리지 않고 남은 창을 감지하기 위한 필터.
    """
    return [w for w in windows if (w.end or w.start) and (w.end or w.start) >= now]


async def _ocr_extract_window(shot_path, site, now):
    """이미지 스샷을 OCR → 점검 일시(window)와 OCR 텍스트를 파싱.

    (파싱 성공 + 종료/시작이 미래 + 본문에 자기 기관명 등장)까지 통과하면
    (window, ocr_text) 튜플, 아니면 None. OCR 미설치/실패 시에도 None.
    """
    if not shot_path:
        return None
    import asyncio
    from . import ocr
    text = await asyncio.to_thread(ocr.ocr_image, str(shot_path))
    if not text:
        return None
    future = _future_windows(extract_windows(text), now)
    if not future:
        return None
    window = min(future, key=lambda w: w.start or w.end)
    if not is_own_institution_notice(text, site.name, getattr(site, "aliases", None)):
        return None
    return window, text


async def _safe_text(locator) -> str:
    try:
        return (await locator.inner_text(timeout=2000)).strip()
    except (PWTimeoutError, Exception):
        return ""


async def _open_detail(page: Page, list_url: str, row_locator, title_selector: str,
                       detail_url_attr: str) -> Optional[str]:
    """상세 페이지로 이동. href 모드면 URL 반환, click 모드면 새 페이지 진입 후 현재 URL 반환."""
    title_loc = row_locator.locator(title_selector).first

    if detail_url_attr == "click":
        try:
            await title_loc.click(timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            return page.url
        except Exception as e:
            logger.warning(f"  detail click failed: {e}")
            return None

    href = await title_loc.get_attribute("href")
    if not href or href.startswith("javascript:") or href == "#":
        # 폴백: javascript 링크면 클릭으로 시도
        try:
            await title_loc.click(timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            return page.url
        except Exception as e:
            logger.warning(f"  href is js/#, click fallback failed: {e}")
            return None

    detail_url = urljoin(list_url, href)
    try:
        await page.goto(detail_url, wait_until="networkidle", timeout=20000)
        return detail_url
    except Exception as e:
        logger.warning(f"  detail goto failed: {e}")
        return None


async def _screenshot_full(page: Page, out_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(out_path), full_page=True)
        return True
    except Exception as e:
        logger.warning(f"  screenshot failed: {e}")
        return False


def _slug(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^\w가-힣\-]+", "_", text or "")
    return s.strip("_")[:max_len] or "notice"


# 본문에서 '업무: ...' / '대상 서비스: ...' 같은 줄을 찾는 라벨들
# (구체적인 라벨을 앞에 두어, 같은 줄에서 더 정확한 매칭이 우선되도록 한다)
_SERVICE_LABELS = ["서비스 중단 내용", "서비스중단내용",
                   "서비스 중단 업무", "서비스중단업무",
                   "서비스 중단 채널", "서비스중단채널",
                   "서비스 중단 영향", "서비스중단영향",
                   "중단 서비스", "중단서비스",
                   "중단 내용", "중단내용",
                   "중단 업무", "중단업무",
                   "중단 거래", "중단거래",
                   "중단 영향", "중단영향",
                   "중단 기능", "중단기능",
                   "제한 서비스", "제한서비스",
                   "작업 영향", "작업영향",
                   "대상 시스템", "대상시스템",
                   "대상 서비스", "대상서비스", "대상 업무", "대상업무",
                   "영향 서비스", "영향서비스",
                   "지연 발생 업무", "지연발생업무", "지연 발생 서비스", "지연발생서비스",
                   "지연 업무", "지연업무",
                   "중단 대상", "중단대상", "서비스", "업무", "대상"]

# 라벨 단독 줄 다음의 불릿 목록을 '모두 모아 결합'할 라벨.
# (불릿이 곧 내용 전부인 경우만. 예: 제주은행 '지연 발생 업무' → · 항목 4개)
# '중단 업무' 등은 첫 줄이 요약이라 불릿 결합 시 장황해지므로 제외한다.
# (예: NH저축은행 '중단 업무' 첫 줄 = 인터넷뱅킹...모든 업무 중단)
_BULLET_COLLECT_LABELS = {"지연 발생 업무", "지연발생업무",
                          "지연 발생 서비스", "지연발생서비스",
                          "지연 업무", "지연업무",
                          "서비스 중단 영향", "서비스중단영향",
                          # 인터넷등기소: '○ 중단기능' 단독 줄 + 불릿 목록
                          "중단 기능", "중단기능"}
_REASON_LABELS = ["서비스 중단 사유", "서비스중단사유",
                  "중단 사유", "중단사유",
                  "점검 사유", "점검사유",
                  "작업 내용", "작업내용",
                  "사유", "목적", "내용"]

# 라벨 앞에 흔히 붙는 목록 마커 (예: '2.', '2)', '(2)', '가.', '①', '-', '·')
_LIST_MARKER = r"(?:\(?\d+[.)]|[가-힣][.)]|[①-⑳]|[-•·▶◆◇○●.])"

# '값'으로 오인하기 쉬운 라벨성 단어 (표가 텍스트로 풀린 경우의 헤더/셀 라벨).
# 예: 농협 '구분/내용' 표 헤더의 '내용' 라벨 다음 줄이 '제한일시'(또 다른 라벨)인 케이스.
_LABELISH_VALUE_RE = re.compile(
    r"^(?:제한|중단|점검|작업|대상|영향|지연)?\s*"
    r"(?:일시|일자|일정|시간|시각|서비스|업무|내용|사유|구분|대상|채널)$"
)

# 숫자/시각 조각만으로 된 값 — 표가 텍스트로 풀리며 라벨 다음 줄이 시간 셀인 케이스.
# 예: 부산은행 표 헤더 '중단업무' 다음 줄이 '00:30 ~' (시간 컬럼 셀)
_NUMERICISH_VALUE_RE = re.compile(r"^[\d\s:~∼\-‐–—.,()시분간]+$")

# 본문 서술에서 사유 추출: 'X 작업/점검으로 (인해)' / 'X 점검에 따라' 패턴.
# 라벨('중단 사유:' 등)이 없는 공지의 폴백 (예: 부산은행 '전산시스템 교체작업으로 …').
_PROSE_REASON_RE = re.compile(
    r"((?:[가-힣A-Za-z0-9()/·]+\s+){0,3}[가-힣A-Za-z0-9()/·]*"
    r"(?:점검|작업|업그레이드|교체|이관|전환|증설|개편))"
    r"(?:\s*(?:으로|로)(?:\s*인하여|\s*인해)?|에\s*따라|\s*관계로)"
)
# 캡처 선두의 수식어 토큰 제거용 ('제공을 위한 전산시스템 교체작업' → '전산시스템 교체작업')
_REASON_LEAD_DROP_RE = re.compile(
    r"^(?:[가-힣A-Za-z0-9()/·]*(?:을|를|의|위한|따른|인한|위해)|보다|더|당사)\s+"
)


def _prose_reason(body_text: str, max_len: int = 60) -> str:
    """본문 서술에서 'X 작업/점검으로' 패턴으로 사유를 추출. 없으면 ''."""
    if not body_text:
        return ""
    text = re.sub(r"\s+", " ", body_text)
    m = _PROSE_REASON_RE.search(text)
    if not m:
        return ""
    val = m.group(1).strip()
    prev = None
    while prev != val:  # 선두 수식어 반복 제거
        prev = val
        val = _REASON_LEAD_DROP_RE.sub("", val)
    return val[:max_len]


def _extract_reason(body_text: str) -> str:
    """사유 텍스트: 본문 라벨 우선 → 본문 서술 패턴 폴백.

    (엑셀 사유 컬럼은 이 값이 비면 제목으로 폴백 — excel_writer._resolve_reason_text)
    """
    return _label_value(body_text, _REASON_LABELS) or _prose_reason(body_text)

# 업무 폴백 기본값 (본문에서 라벨로 추출 못 했을 때)
DEFAULT_SERVICE_TEXT = "인터넷뱅킹, 스마트폰뱅킹, 모바일웹뱅킹 중단"

# 제목 키워드 기반 업무(중단 서비스) 오버라이드.
# 본문에 업무 라벨이 없어 기본값으로 떨어지는 특수 공지에 한해, 제목으로
# 식별해 업무 텍스트를 지정한다. (키워드는 소문자 비교)
# 예: 코스콤 인증센터 SignKorea 시스템 작업 → 일부 인증시스템만 중단(전체 뱅킹 X)
SERVICE_OVERRIDES: list[tuple[str, str]] = [
    ("signkorea", "일부 인증시스템 중단"),
    # 홈택스 메인 배너 — 본문(배너 대체텍스트)에 업무 라벨이 없어 은행용 기본값으로 떨어짐
    ("홈택스 서비스 일시 지연", "홈택스·손택스 전체 서비스 지연"),
]


_PARENS_RE = re.compile(r"\s*[\(（][^\(\)（）]*[\)）]")


def _strip_parens(s: str) -> str:
    """괄호(( ), （ ）) 안 내용을 통째로 제거하고 잔여 구두점·공백 정리."""
    if not s:
        return s
    prev = None
    while prev != s:  # 중첩 괄호까지 반복 제거
        prev = s
        s = _PARENS_RE.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip(" ,·-")


def _resolve_service_text(title: str, body_text: str) -> str:
    """업무(중단 서비스) 텍스트 결정.

    우선순위: 본문 라벨 추출 → 제목 키워드 오버라이드 → 기본값.
    (라벨 추출이 실패한 특수 공지만 오버라이드로 보정한다.)
    추출값은 괄호 안 부연(예: '(MTS, HTS, ...)')을 제거해 간결하게 만든다.
    """
    val = _label_value(body_text, _SERVICE_LABELS, collect_bullets=True)
    if val:
        return _strip_parens(val)
    t = (title or "").lower()
    for kw, override in SERVICE_OVERRIDES:
        if kw in t:
            return override
    return DEFAULT_SERVICE_TEXT


def _clean_line(s: str) -> str:
    """라벨 비교용 줄 정규화: 노이즈 문자(불릿/마커) 제거 + 공백 압축."""
    s = re.sub(r"[;※\*■▶◆●·◈▣◇□☞➤▷□◆◇]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_bullet(s: str) -> str:
    """선두의 불릿/대시/콜론/목록 마커(예: '-', '·', '①', '1.', '가.')와 공백을 제거."""
    # 선두 목록 마커 1개 제거 (예: '① 웰컴저축은행...' → '웰컴저축은행...')
    s = re.sub(rf"^\s*{_LIST_MARKER}\s*", "", s)
    return re.sub(r"^[\-•·▪:：\s]+", "", s).strip()


def _is_bullet(raw: str) -> bool:
    """원본 줄이 불릿(·, •, ▪, -, * 등)으로 시작하는 목록 항목인지."""
    return bool(re.match(r"^\s*[·•∙‣◦・·•▪\-\*]\s*\S", raw))


def _label_value(text: str, labels: list[str], max_len: int = 200,
                 collect_bullets: bool = False) -> str:
    """본문에서 라벨에 해당하는 값을 추출.

    지원 패턴 (라벨 비교 시 ;/※/* 등 노이즈 제거):
      1. '라벨: 값' / '라벨 - 값'  (같은 줄)
      2. '라벨' (단독 줄) → 다음 비어있지 않은 줄을 값으로

    collect_bullets=True 이고 매칭 라벨이 `_BULLET_COLLECT_LABELS`에 속할 때만,
    라벨 단독 줄 다음의 연속된 불릿 항목을 모두 모아 ', '로 결합한다.
    그 외 라벨은 첫 줄만 값으로 쓴다.
      - 출력 업무 컬럼 전용. 키워드 제외 판정엔 쓰지 말 것 — 영향 서비스 목록에
        우연히 든 exclude 키워드(예: '오픈뱅킹')로 전체 점검 공지가 잘못 제외될
        수 있다. 예: NH저축은행 KRBK0110.
      - '중단 업무'처럼 첫 줄이 요약인 라벨은 결합 대상에서 제외(장황 방지),
        '지연 발생 업무'처럼 불릿이 곧 내용 전부인 라벨만 결합. 예: 제주은행.
    """
    if not text:
        return ""
    raw_lines = [ln for ln in text.splitlines() if ln.strip()]
    cleaned = [_clean_line(ln) for ln in raw_lines]
    for i, ln in enumerate(cleaned):
        if not ln:
            continue
        for lab in labels:
            # 1) 같은 줄
            m = re.match(rf"^\s*(?:{_LIST_MARKER}\s*)?{re.escape(lab)}\s*[:：\-]\s*(.+)$", ln)
            if m:
                return _strip_bullet(m.group(1).rstrip("."))[:max_len]
            # 2) 라벨만 있는 줄 → 다음 줄(들)을 값으로
            m2 = re.match(rf"^\s*(?:{_LIST_MARKER}\s*)?{re.escape(lab)}\s*[:：]?\s*$", ln)
            if m2:
                rest_raw = raw_lines[i + 1:]
                # 2a) 다음 줄이 불릿 목록이면 연속된 불릿 항목을 모두 모아 ', '로 결합
                #     (예: 제주은행 '지연 발생 업무' 아래 · 항목 4개)
                if (collect_bullets and lab in _BULLET_COLLECT_LABELS
                        and rest_raw and _is_bullet(rest_raw[0])):
                    items = []
                    for raw in rest_raw:
                        if _is_bullet(raw):
                            val = _strip_bullet(_clean_line(raw))
                            if val:
                                items.append(val)
                        else:
                            break
                    if items:
                        return ", ".join(items)[:max_len]
                # 2b) 일반 케이스: 다음 비어있지 않은 줄 하나
                for nxt_i in range(i + 1, len(cleaned)):
                    nxt = cleaned[nxt_i]
                    if nxt:
                        val = _strip_bullet(nxt.rstrip("."))
                        # 다음 줄이 또 다른 라벨이거나 숫자/시각 조각이면 표 헤더로
                        # 판단 (예: 농협 '내용' 헤더 → '제한일시',
                        # 부산은행 '중단업무' 헤더 → '00:30 ~').
                        if _LABELISH_VALUE_RE.match(val) or _NUMERICISH_VALUE_RE.match(val):
                            # 표가 컬럼 단위로 풀린 경우 실제 값이 몇 줄 뒤 불릿
                            # 항목인 케이스(부산은행 '▪ 인터넷/모바일 뱅킹 …')
                            # → 근처의 첫 불릿 줄을 값으로 시도
                            for cand in raw_lines[nxt_i:nxt_i + 8]:
                                if _is_bullet(cand):
                                    bval = _strip_bullet(_clean_line(cand))
                                    if bval and not _NUMERICISH_VALUE_RE.match(bval):
                                        return bval[:max_len]
                            break
                        return val[:max_len]
    return ""


async def _screenshot_html(browser: Browser, html: str, out_path: Path,
                            base_url: str = "") -> bool:
    """주어진 HTML을 임시 페이지에 set_content 후 full-page 스크린샷."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ctx = await browser.new_context(viewport={"width": 1366, "height": 900}, locale="ko-KR")
    page = await ctx.new_page()
    try:
        # 외부 리소스 (CSS/이미지)도 로드하도록 base URL 지정
        if base_url:
            wrapped = (
                f'<base href="{base_url}">'
                if "<head" not in html.lower()
                else ""
            ) + html
        else:
            wrapped = html
        await page.set_content(wrapped, wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeoutError:
            pass
        await page.screenshot(path=str(out_path), full_page=True)
        return True
    except Exception as e:
        logger.warning(f"  screenshot(html) 실패: {e}")
        return False
    finally:
        await ctx.close()


async def _scrape_site_via_handler(
    browser: Browser,
    site: SiteConfig,
    keywords: KeywordConfig,
    screenshot_root: Path,
    run_id: str,
    roster: Optional[set] = None,
    skips: Optional[List[dict]] = None,
) -> List[NoticeHit]:
    """사이트별 커스텀 핸들러 사용 (HTTP API/POST 등)."""
    fn = get_handler(site.code)
    hits: List[NoticeHit] = []
    try:
        # handler는 동기 함수. 별도 스레드에서 실행하여 이벤트루프 차단 방지
        import asyncio
        results: List[HandlerResult] = await asyncio.to_thread(fn, site)
    except Exception as e:
        logger.exception(f"[{site.code}] handler 실패: {e}")
        return [NoticeHit(
            site_code=site.code, site_name=site.name, category=site.category,
            title="", posted_date="", detail_url=site.list_url,
            screenshot_path="", window=None, error=f"handler: {e}",
        )]

    now = datetime.now()
    logger.info(
        f"[{site.code}] handler 결과 {len(results)}건 | "
        f"점검 종료 시각이 {now:%Y-%m-%d %H:%M} 이후인 공지만 통과"
    )
    for r in results:
        # 1) 키워드 매칭 (제목 + 본문의 '사유'·'업무' 라벨 + 본문 전체 exclude_body)
        reason_preview = _label_value(r.body_text, _REASON_LABELS)
        service_preview = _label_value(r.body_text, _SERVICE_LABELS)
        kw_ok, kw_why = maintenance_verdict(
            r.title, keywords, reason_preview, service_preview, r.body_text)
        if not kw_ok:
            # include 키워드는 맞았는데 exclude로 걸러진 경우만 스킵 기록 (일반 공지 제외)
            if kw_why:
                logger.info(f"[{site.code}] {kw_why} 매칭, skip: {r.title}")
                _note_skip(skips, site, r.title, kw_why, r.detail_url, r.posted_date)
            continue
        # 1.5) 제목은 점검 공지인데 본문이 비어있으면(이미지로만 된 공지 등) 자기 기관
        #      검증·일시 추출이 불가능하다. 조용히 버리지 말고 이미지를 캡처해 '검토 필요'로
        #      남겨 사람이 확인하도록 한다.
        if not (r.body_text or "").strip():
            if _posted_too_old(r.posted_date):
                logger.info(
                    f"[{site.code}] 본문 비어있으나 등록일 오래됨({r.posted_date}), "
                    f"검토 목록에서도 제외: {r.title}"
                )
                _note_skip(skips, site, r.title,
                           f"본문 비어있음 + 등록일 오래됨({r.posted_date})",
                           r.detail_url, r.posted_date)
                continue
            run_date = run_id.split("_", 1)[0]
            shot_name = f"{run_date}_{site.code}_{len(hits):02d}_review_{_slug(r.title)}.png"
            shot_path = screenshot_root / shot_name
            ok = await _screenshot_html(browser, r.detail_html, shot_path, base_url=site.list_url)
            # 이미지 OCR로 점검 일시를 읽어내면 검토 없이 바로 감지목록으로 승격
            parsed = await _ocr_extract_window(shot_path if ok else None, site, now)
            if parsed is not None:
                window, ocr_text = parsed
                schedule_text = format_schedule(window) or window.raw
                service_text = _resolve_service_text(r.title, ocr_text)
                reason_text = _extract_reason(ocr_text)
                logger.info(
                    f"[{site.code}] OCR로 점검 일시 추출 → 자동 감지: {r.title} (일시: {window.raw})"
                )
                hits.append(NoticeHit(
                    site_code=site.code, site_name=site.name, category=site.category,
                    title=r.title, posted_date=r.posted_date, detail_url=r.detail_url,
                    screenshot_path=str(shot_path) if ok else "",
                    window=window, schedule_text=schedule_text,
                    service_text=service_text, reason_text=reason_text,
                    via_ocr=True, body_text=ocr_text or "",
                ))
                continue
            logger.info(
                f"[{site.code}] 본문 비어있음(이미지, OCR 파싱 실패) → 검토 목록에 추가: {r.title}"
            )
            hits.append(NoticeHit(
                site_code=site.code, site_name=site.name, category=site.category,
                title=r.title, posted_date=r.posted_date, detail_url=r.detail_url,
                screenshot_path=str(shot_path) if ok else "",
                window=None, needs_review=True,
            ))
            continue
        # 2) 점검 주체가 남의 기관인지 먼저 확인. external_review 기관(금융결제원·코스콤 등)의
        #    작업이면 버리지 않고 '검토 필요'로 보낸다 — 인증서 등 자사 서비스 영향 가능성.
        subject = external_subject(
            r.title, site.name, getattr(site, "aliases", None), roster or set()
        )
        if subject and _review_external(subject, keywords):
            windows = extract_windows(r.body_text) if r.body_text else []
            future = _future_windows(windows, now)
            window = future[0] if future else (windows[0] if windows else None)
            ref = (window.end or window.start) if window else None
            if ref and ref < now:
                logger.info(
                    f"[{site.code}] 외부 기관({subject}) 작업, 이미 지난 점검({ref:%Y-%m-%d %H:%M}), skip: {r.title}"
                )
                _note_skip(skips, site, r.title,
                           f"외부 기관({subject}) 작업 안내 (이미 지난 점검)",
                           r.detail_url, r.posted_date)
                continue
            logger.info(
                f"[{site.code}] 외부 기관({subject}) 작업 안내 → 검토 목록에 추가: {r.title}"
            )
            hits.append(NoticeHit(
                site_code=site.code, site_name=site.name, category=site.category,
                title=r.title, posted_date=r.posted_date, detail_url=r.detail_url,
                screenshot_path="",
                window=window,
                schedule_text=(format_schedule(window) or window.raw) if window else "",
                service_text=_label_value(r.body_text, _SERVICE_LABELS, collect_bullets=True) or "",
                reason_text=f"외부 기관({subject}) 작업",
                needs_review=True, body_text=r.body_text or "",
            ))
            continue
        # 2.2) 본문이 자기 기관 점검인지 확인 (외부 기관 안내성 공지 제외)
        if not is_own_institution_notice(r.body_text, site.name, getattr(site, "aliases", None)):
            logger.info(
                f"[{site.code}] 외부 기관 안내로 판단(본문에 {site.name} 미등장), "
                f"skip: {r.title}"
            )
            _note_skip(skips, site, r.title,
                       f"외부 기관 안내 (본문에 {site.name} 미등장)",
                       r.detail_url, r.posted_date)
            continue
        # 2.5) 본문에 자기 기관명이 있어도 점검 '주체'가 남의 기관이면 제외
        #      (인사말·피해 기관 언급만으로 2.2)를 통과하는 케이스)
        if subject:
            logger.info(
                f"[{site.code}] 외부 기관({subject}) 작업 안내로 판단, skip: {r.title}"
            )
            _note_skip(skips, site, r.title, f"외부 기관({subject}) 작업 안내",
                       r.detail_url, r.posted_date)
            continue
        # 3) 본문에서 점검 일시 추출. 종료(or 시작)가 미래인 창만 통과
        #    (한 공지에 여러 일시가 나열되면 창마다 개별 hit — 예: 씨티은행 8/2·8/9)
        windows = extract_windows(r.body_text) if r.body_text else []
        if not windows:
            logger.info(f"[{site.code}] 본문에서 점검 일시 파싱 실패, skip: {r.title}")
            _note_skip(skips, site, r.title, "점검 일시 파싱 실패",
                       r.detail_url, r.posted_date)
            continue
        future = _future_windows(windows, now)
        if not future:
            refs = [w.end or w.start for w in windows if (w.end or w.start)]
            if not refs:
                logger.info(f"[{site.code}] 점검 일시 미상, skip: {r.title}")
                _note_skip(skips, site, r.title, "점검 일시 미상",
                           r.detail_url, r.posted_date)
                continue
            ref = max(refs)
            logger.info(
                f"[{site.code}] 이미 지난 점검({ref:%Y-%m-%d %H:%M}), skip: {r.title}"
            )
            _note_skip(skips, site, r.title, f"이미 지난 점검({ref:%Y-%m-%d %H:%M})",
                       r.detail_url, r.posted_date)
            continue
        logger.info(
            f"[{site.code}] 매칭 (점검 일시 {len(future)}건: "
            f"{' / '.join(w.raw for w in future)}): {r.title}"
        )

        run_date = run_id.split("_", 1)[0]
        shot_name = f"{run_date}_{site.code}_{len(hits):02d}_{_slug(r.title)}.png"
        shot_path = screenshot_root / shot_name
        ok = await _screenshot_html(browser, r.detail_html, shot_path, base_url=site.list_url)

        service_text = _resolve_service_text(r.title, r.body_text)
        reason_text = _extract_reason(r.body_text)

        for window in future:
            hits.append(NoticeHit(
                site_code=site.code, site_name=site.name, category=site.category,
                title=r.title, posted_date=r.posted_date,
                detail_url=r.detail_url,
                screenshot_path=str(shot_path) if ok else "",
                window=window,
                schedule_text=format_schedule(window) or window.raw,
                service_text=service_text,
                reason_text=reason_text,
                body_text=r.body_text or "",
            ))

    return hits


async def _scrape_site(
    browser: Browser,
    site: SiteConfig,
    keywords: KeywordConfig,
    screenshot_root: Path,
    run_id: str,
    roster: Optional[set] = None,
    skips: Optional[List[dict]] = None,
) -> List[NoticeHit]:
    hits: List[NoticeHit] = []
    context = await browser.new_context(
        viewport={"width": 1366, "height": 900},
        locale="ko-KR",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    page.set_default_timeout(20000)

    try:
        logger.info(f"[{site.code}] {site.name} - 목록 로딩: {site.list_url}")
        await page.goto(site.list_url, wait_until="domcontentloaded", timeout=30000)
        if site.wait_selector:
            try:
                await page.wait_for_selector(site.wait_selector, timeout=15000)
            except PWTimeoutError:
                logger.warning(f"[{site.code}] wait_selector 타임아웃, 계속 진행")

        rows = page.locator(site.list_item_selector)
        count = min(await rows.count(), site.max_items)
        logger.info(f"[{site.code}] 공지 행 {count}개 검사")

        week_start, week_end = current_week_bounds()
        logger.info(
            f"[{site.code}] 이번 주 필터: {week_start} ~ {week_end}"
        )
        for i in range(count):
            row = rows.nth(i)
            title = await _safe_text(row.locator(site.title_selector).first)
            if not title:
                continue

            kw_ok, kw_why = maintenance_verdict(title, keywords)
            if not kw_ok:
                # 제목 단계 제외 (include 매칭 + exclude 걸림)만 기록
                if kw_why:
                    logger.info(f"[{site.code}] {kw_why} 매칭, skip: {title}")
                    _note_skip(skips, site, title, kw_why)
                continue

            posted_date = ""
            if site.date_selector:
                posted_date = await _safe_text(row.locator(site.date_selector).first)

            logger.info(f"[{site.code}] 1차 매칭: {title}")

            # 상세 페이지 진입 (click 모드는 현재 페이지에서 이동될 수 있음)
            list_page_url = page.url
            detail_url = await _open_detail(
                page, site.list_url, row, site.title_selector, site.detail_url_attr
            )

            run_date = run_id.split("_", 1)[0]
            shot_name = f"{run_date}_{site.code}_{i:02d}_{_slug(title)}.png"
            shot_path = screenshot_root / shot_name
            shot_ok = await _screenshot_full(page, shot_path)

            # 본문 텍스트 추출 → 점검 일시 파싱
            body_text = ""
            try:
                content_loc = page.locator(site.detail_content_selector).first
                if await content_loc.count() > 0:
                    body_text = await content_loc.inner_text(timeout=5000)
                else:
                    body_text = await page.locator("body").inner_text(timeout=5000)
            except Exception:
                pass

            # 제목은 점검 공지인데 본문이 비어있으면(이미지로만 된 공지 등) 자동 판별
            # 불가 → 캡처만 남기고 '검토 필요'로 표시 (조용히 버리지 않음)
            if not (body_text or "").strip():
                if _posted_too_old(posted_date):
                    logger.info(
                        f"[{site.code}] 본문 비어있으나 등록일 오래됨({posted_date}), "
                        f"검토 목록에서도 제외: {title}"
                    )
                    _note_skip(skips, site, title,
                               f"본문 비어있음 + 등록일 오래됨({posted_date})",
                               detail_url or "", posted_date)
                    try:
                        if page.url != list_page_url:
                            await page.goto(site.list_url, wait_until="domcontentloaded", timeout=20000)
                            if site.wait_selector:
                                try:
                                    await page.wait_for_selector(site.wait_selector, timeout=10000)
                                except PWTimeoutError:
                                    pass
                            rows = page.locator(site.list_item_selector)
                    except Exception:
                        pass
                    continue
                # 이미지 OCR로 점검 일시를 읽어내면 검토 없이 바로 감지목록으로 승격
                _now = datetime.now()
                parsed = await _ocr_extract_window(shot_path if shot_ok else None, site, _now)
                if parsed is not None:
                    window, ocr_text = parsed
                    schedule_text = format_schedule(window) or window.raw
                    service_text = _resolve_service_text(title, ocr_text)
                    reason_text = _extract_reason(ocr_text)
                    logger.info(
                        f"[{site.code}] OCR로 점검 일시 추출 → 자동 감지: {title} (일시: {window.raw})"
                    )
                    hits.append(NoticeHit(
                        site_code=site.code, site_name=site.name, category=site.category,
                        title=title, posted_date=posted_date, detail_url=detail_url or "",
                        screenshot_path=str(shot_path) if shot_ok else "",
                        window=window, schedule_text=schedule_text,
                        service_text=service_text, reason_text=reason_text,
                        via_ocr=True, body_text=ocr_text or "",
                    ))
                else:
                    logger.info(
                        f"[{site.code}] 본문 비어있음(이미지, OCR 파싱 실패) → 검토 목록에 추가: {title}"
                    )
                    hits.append(NoticeHit(
                        site_code=site.code, site_name=site.name, category=site.category,
                        title=title, posted_date=posted_date, detail_url=detail_url or "",
                        screenshot_path=str(shot_path) if shot_ok else "",
                        window=None, needs_review=True,
                    ))
                try:
                    if page.url != list_page_url:
                        await page.goto(site.list_url, wait_until="domcontentloaded", timeout=20000)
                        if site.wait_selector:
                            try:
                                await page.wait_for_selector(site.wait_selector, timeout=10000)
                            except PWTimeoutError:
                                pass
                        rows = page.locator(site.list_item_selector)
                except Exception:
                    pass
                continue

            # 본문이 자기 기관 점검인지 + 점검 주체가 남의 기관은 아닌지 확인
            not_own = bool(body_text) and not is_own_institution_notice(body_text, site.name)
            subject = external_subject(
                title, site.name, getattr(site, "aliases", None), roster or set()
            )
            if not_own or subject:
                if subject and _review_external(subject, keywords):
                    # external_review 기관(금융결제원·코스콤 등) 작업 → '검토 필요'로 보낸다
                    windows = extract_windows(body_text) if body_text else []
                    future = _future_windows(windows, datetime.now())
                    window = future[0] if future else (windows[0] if windows else None)
                    ref = (window.end or window.start) if window else None
                    if ref and ref < datetime.now():
                        _note_skip(skips, site, title,
                                   f"외부 기관({subject}) 작업 안내 (이미 지난 점검)",
                                   detail_url or "", posted_date)
                    else:
                        logger.info(
                            f"[{site.code}] 외부 기관({subject}) 작업 안내 → 검토 목록에 추가: {title}"
                        )
                        hits.append(NoticeHit(
                            site_code=site.code, site_name=site.name, category=site.category,
                            title=title, posted_date=posted_date, detail_url=detail_url or "",
                            screenshot_path="",
                            window=window,
                            schedule_text=(format_schedule(window) or window.raw) if window else "",
                            service_text=_label_value(body_text, _SERVICE_LABELS, collect_bullets=True) or "",
                            reason_text=f"외부 기관({subject}) 작업",
                            needs_review=True, body_text=body_text or "",
                        ))
                else:
                    why = (f"외부 기관({subject}) 작업 안내" if subject
                           else f"외부 기관 안내(본문에 {site.name} 미등장)")
                    logger.info(f"[{site.code}] {why}로 판단, skip: {title}")
                    _note_skip(skips, site, title, why, detail_url or "", posted_date)
                # 목록으로 복귀 후 다음 행
                try:
                    if page.url != list_page_url:
                        await page.goto(site.list_url, wait_until="domcontentloaded", timeout=20000)
                        if site.wait_selector:
                            try:
                                await page.wait_for_selector(site.wait_selector, timeout=10000)
                            except PWTimeoutError:
                                pass
                        rows = page.locator(site.list_item_selector)
                except Exception:
                    pass
                continue

            # 본문이 확보된 뒤, 본문 라벨/전체 기준 제외 키워드 재확인
            # (목록 단계에선 제목만 봤으므로 exclude_body 등은 여기서 걸러진다)
            if body_text:
                reason_preview = _label_value(body_text, _REASON_LABELS)
                service_preview = _label_value(body_text, _SERVICE_LABELS)
                kw_ok, kw_why = maintenance_verdict(
                    title, keywords, reason_preview, service_preview, body_text)
                if not kw_ok:
                    logger.info(f"[{site.code}] 본문 기준 제외 키워드 매칭, skip: {title}")
                    _note_skip(skips, site, title,
                               kw_why or "본문 기준 제외 키워드 매칭",
                               detail_url or "", posted_date)
                    # 목록으로 복귀 후 다음 행
                    try:
                        if page.url != list_page_url:
                            await page.goto(site.list_url, wait_until="domcontentloaded", timeout=20000)
                            if site.wait_selector:
                                try:
                                    await page.wait_for_selector(site.wait_selector, timeout=10000)
                                except PWTimeoutError:
                                    pass
                            rows = page.locator(site.list_item_selector)
                    except Exception:
                        pass
                    continue

            windows = extract_windows(body_text) if body_text else []
            # 점검 종료(or 시작) 시각이 미래인 창만 통과
            # (한 공지에 여러 일시가 나열되면 창마다 개별 hit — 예: 씨티은행 8/2·8/9)
            now = datetime.now()
            if not windows:
                logger.info(f"[{site.code}] 본문에서 점검 일시 파싱 실패, skip: {title}")
                _note_skip(skips, site, title, "점검 일시 파싱 실패",
                           detail_url or "", posted_date)
                continue
            future = _future_windows(windows, now)
            if not future:
                refs = [w.end or w.start for w in windows if (w.end or w.start)]
                logger.info(
                    f"[{site.code}] 이미 지난 점검(또는 일시 미상), skip: {title}"
                )
                _note_skip(skips, site, title,
                           (f"이미 지난 점검({max(refs):%Y-%m-%d %H:%M})" if refs else "점검 일시 미상"),
                           detail_url or "", posted_date)
                continue

            service_text = _resolve_service_text(title, body_text)
            reason_text = _extract_reason(body_text)

            for window in future:
                hits.append(
                    NoticeHit(
                        site_code=site.code,
                        site_name=site.name,
                        category=site.category,
                        title=title,
                        posted_date=posted_date,
                        detail_url=detail_url or "",
                        screenshot_path=str(shot_path) if shot_ok else "",
                        window=window,
                        schedule_text=format_schedule(window) or window.raw,
                        service_text=service_text,
                        reason_text=reason_text,
                        body_text=body_text or "",
                    )
                )

            # 목록으로 복귀
            try:
                if page.url != list_page_url:
                    await page.goto(site.list_url, wait_until="domcontentloaded", timeout=20000)
                    if site.wait_selector:
                        try:
                            await page.wait_for_selector(site.wait_selector, timeout=10000)
                        except PWTimeoutError:
                            pass
                    rows = page.locator(site.list_item_selector)
            except Exception as e:
                logger.warning(f"[{site.code}] 목록 복귀 실패: {e}")
                break

    except Exception as e:
        logger.exception(f"[{site.code}] 스크래핑 실패: {e}")
        hits.append(
            NoticeHit(
                site_code=site.code,
                site_name=site.name,
                category=site.category,
                title="",
                posted_date="",
                detail_url=site.list_url,
                screenshot_path="",
                window=None,
                error=str(e),
            )
        )
    finally:
        await context.close()

    return hits


async def scrape_all(
    sites: List[SiteConfig],
    keywords: KeywordConfig,
    screenshot_root: Path,
    run_id: str,
    headless: bool = True,
    skips: Optional[List[dict]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[NoticeHit]:
    """전체 사이트 수집.

    skips: 전달하면 제외/스킵된 공지 기록(dict)이 append 된다 (대시보드 '스킵' 탭용).
    progress_cb(done, total, site_name): 사이트 처리 전마다 호출 (대시보드 진행률용).
    """
    enabled = [s for s in sites if s.enabled]
    logger.info(f"스크래핑 대상: {len(enabled)}/{len(sites)}개 사이트 (enabled=true)")

    all_hits: List[NoticeHit] = []
    if not enabled:
        return all_hits

    # 외부기관 감지 명부 — 비활성 사이트의 기관명도 '남의 기관'이므로 전부 포함
    roster = build_institution_roster(
        [s.name for s in sites],
        keywords.external_orgs + keywords.institution_aliases,
    )

    total = len(enabled)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            for idx, site in enumerate(enabled):
                if progress_cb:
                    try:
                        progress_cb(idx, total, site.name)
                    except Exception:  # 진행률 보고 실패가 수집을 막으면 안 됨
                        pass
                if has_handler(site.code):
                    site_hits = await _scrape_site_via_handler(
                        browser, site, keywords, screenshot_root, run_id, roster, skips
                    )
                else:
                    site_hits = await _scrape_site(
                        browser, site, keywords, screenshot_root, run_id, roster, skips
                    )
                all_hits.extend(site_hits)
            if progress_cb:
                try:
                    progress_cb(total, total, "")
                except Exception:
                    pass
        finally:
            await browser.close()
    return all_hits


def make_run_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d_%H%M")
