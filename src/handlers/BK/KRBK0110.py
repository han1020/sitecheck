"""
KRBK0110 - NH저축은행 (공지사항)

2026-08-10 사이트가 React(Vite) SPA로 전면 개편됨. 기존 /notice/list.do·
/notice/view.do 는 사라져(200 응답으로 /404.html 로 리다이렉트) requests
포팅이 불가능해졌다. 공지 목록/상세 데이터는 CSM0001SC 거래전문 API 로 오는데
응답이 ASTX2/VestWeb 으로 종단 암호화되어 있어 직접 파싱도 어렵다.

브라우저가 이 암호문을 복호화해 DOM 에 평문으로 렌더링하므로, Playwright 로
페이지를 실제 렌더링한 뒤 렌더된 DOM 만 읽는다.

흐름:
  1. goto /csm/csm024_1.view (공지 목록 SPA) → 렌더 대기
  2. 목록 항목(li>button, 텍스트에 'YYYY.MM.DD' 등록일 포함) 수집.
     네비 메뉴 버튼은 날짜가 없어 걸러진다.
  3. n번째 항목 클릭 → /csm/csm024_2.view 상세 렌더
       - 제목/등록일: div[class*=board-detail__header]
       - 본문 HTML : div[class*=board-detail__content]
     상세는 라우터 state(brd_sno)로만 진입 가능해 URL 직접 접근이 안 되므로,
     클릭 → 파싱 → go_back(목록 복귀) 을 반복한다. CSS 모듈 해시 클래스라
     클래스 접두사 부분일치로 셀렉트한다.
"""
from __future__ import annotations

import logging
import re
from typing import List

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .. import register
from ..base import HandlerResult, block_text

logger = logging.getLogger(__name__)

HOST = "https://www.nhsavingsbank.co.kr"
LIST_URL = HOST + "/csm/csm024_1.view"
DETAIL_URL = HOST + "/csm/csm024_2.view"   # 엑셀 참조용 (목록 없이는 직접 접근 불가)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
LOAD_TIMEOUT_MS = 60_000
RENDER_WAIT_MS = 800            # 렌더 안정화용 짧은 여유 대기
LIST_READY_MS = 20_000         # 목록 항목이 렌더될 때까지 최대 대기
DETAIL_TIMEOUT_MS = 8_000

# 목록 항목(날짜 포함 버튼)이 하나라도 나타났는지 판정하는 JS
_LIST_READY_JS = (
    "() => [...document.querySelectorAll('li button')]"
    ".some(b => /20\\d{2}\\.\\s*\\d{1,2}\\.\\s*\\d{1,2}/.test(b.innerText || ''))"
)

# 등록일 한 줄 전체가 날짜인 경우(목록 항목의 등록일). 제목에 든 '(2026.7.29.기준)'
# 같은 날짜는 걸러야 하므로 '줄 전체가 날짜'인 것만 등록일로 본다.
_DATE_LINE_RE = re.compile(r"^\s*20\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\s*$")
# 목록 버튼 필터용(부분일치)
_DATE_ANY_RE = re.compile(r"20\d{2}\.\s*\d{1,2}\.\s*\d{1,2}")
_LIST_BTN_SEL = "li button"
_CONTENT_SEL = "div[class*='board-detail__content']"
_HEADER_SEL = "div[class*='board-detail__header']"


def _norm_date(text: str) -> str:
    """'2026.08.07' / '2026. 8. 7' → '20260807'."""
    m = _DATE_ANY_RE.search(text or "")
    if not m:
        return ""
    parts = re.split(r"[.\s]+", m.group(0).strip(" ."))
    if len(parts) < 3:
        return ""
    y, mo, d = parts[0], parts[1].zfill(2), parts[2].zfill(2)
    return f"{y}{mo}{d}"


def _parse_item(text: str) -> tuple[str, str]:
    """목록 버튼 텍스트('제목\\n2026.08.07')에서 (제목, 등록일YYYYMMDD)."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    date_line = next((ln for ln in reversed(lines) if _DATE_LINE_RE.match(ln)), "")
    title = " ".join(ln for ln in lines if ln != date_line).strip()
    return title, _norm_date(date_line)


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 20)
    results: List[HandlerResult] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
                page = ctx.new_page()
                page.goto(LIST_URL, wait_until="networkidle", timeout=LOAD_TIMEOUT_MS)
                try:
                    page.wait_for_function(_LIST_READY_JS, timeout=LIST_READY_MS)
                except Exception:
                    logger.warning("[KRBK0110] 목록 항목이 렌더되지 않음 (구조 변경/차단?)")
                    return []
                page.wait_for_timeout(RENDER_WAIT_MS)

                board = page.locator(_LIST_BTN_SEL).filter(has_text=_DATE_ANY_RE)
                count = min(board.count(), max_items)
                if count == 0:
                    logger.warning("[KRBK0110] 목록 항목을 찾지 못함 (구조 변경?)")
                    return []
                logger.info(f"[KRBK0110] 목록 {count}건 확인")

                for idx in range(count):
                    board = page.locator(_LIST_BTN_SEL).filter(has_text=_DATE_ANY_RE)
                    btn = board.nth(idx)
                    title, posted = _parse_item(btn.inner_text())
                    detail_html = ""
                    body_text = ""
                    try:
                        btn.click(timeout=DETAIL_TIMEOUT_MS)
                        page.wait_for_selector(_CONTENT_SEL, timeout=DETAIL_TIMEOUT_MS)
                        page.wait_for_timeout(400)

                        content_html = page.locator(_CONTENT_SEL).first.inner_html()
                        try:
                            posted = _norm_date(
                                page.locator(_HEADER_SEL).first.inner_text()
                            ) or posted
                        except Exception:
                            pass
                        detail_html = (
                            '<div style="padding:24px;font-family:sans-serif;max-width:800px">'
                            f"<h2>{title}</h2><hr/>{content_html}</div>"
                        )
                        body_text = block_text(BeautifulSoup(content_html, "lxml"))
                    except Exception as e:
                        logger.warning(
                            f"[KRBK0110] 상세 수집 실패(idx={idx}, {title!r}): {e}"
                        )
                    finally:
                        # 목록으로 복귀 (다음 항목 클릭 대비)
                        if idx < count - 1:
                            try:
                                page.go_back(wait_until="networkidle",
                                             timeout=LOAD_TIMEOUT_MS)
                                page.wait_for_function(_LIST_READY_JS,
                                                       timeout=LIST_READY_MS)
                                page.wait_for_timeout(600)
                            except Exception as e:
                                logger.warning(
                                    f"[KRBK0110] 목록 복귀 실패(idx={idx}), "
                                    f"재로드 시도: {e}"
                                )
                                page.goto(LIST_URL, wait_until="networkidle",
                                          timeout=LOAD_TIMEOUT_MS)
                                page.wait_for_function(_LIST_READY_JS,
                                                       timeout=LIST_READY_MS)
                                page.wait_for_timeout(RENDER_WAIT_MS)

                    if title:
                        results.append(HandlerResult(
                            title=title,
                            posted_date=posted,
                            detail_url=DETAIL_URL,
                            detail_html=detail_html,
                            body_text=body_text,
                        ))

                logger.info(f"[KRBK0110] 핸들러 추출 {len(results)}건")
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"[KRBK0110] 수집 실패: {e}")
        return []

    return results


register("KRBK0110", handle)
