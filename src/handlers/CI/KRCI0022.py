"""
KRCI0022 - 크레탑 CRETOP (한국평가데이터) 공지사항

Vue SPA. 공지 목록/상세 데이터가 일반 JSON API(common.json)가 아니라 ``dynaPath``
라는 난독화 봇 탐지 채널(암호화 POST)로만 내려오므로 requests 로는 받을 수 없고,
브라우저가 렌더링한 DOM 만 읽는다.

봇 탐지 우회 조건 (2026-09-29 확인):
  - Playwright 기본 headless(chromium-headless-shell)는 dynaPath 가 봇으로 판정해
    공지 페이지를 "페이지가 만료되었습니다[8004]" 로 바꿔 버린다 (홈은 정상).
  - ``channel="chromium"`` (새 headless 모드) + ``--disable-blink-features=AutomationControlled``
    조합이면 통과. 플래그 없이 channel 만 바꾸면 다시 막힌다.
  - 실제 Google Chrome(channel="chrome")도 통과하지만 Docker 이미지에 없으므로 내장 Chromium 사용.

URL 의 ``h=`` 쿼리는 사이트가 붙이는 타임스탬프로, 만료되면 [8004] 가 뜬다.
그래서 항상 ``h`` 없는 URL 로 진입한다 (사이트가 새 값을 붙여 준다).

흐름:
  1. goto /PL/CC/PLCC520M1 → div.board__area table tbody tr 렌더 대기
  2. 행마다 번호·제목(a.link, href 는 javascript:void(0))·등록일자(YYYY-MM-DD) 수집
  3. n번째 제목 클릭 → /PL/CC/PLCC520S1?notiSeq=NNNN&pageNum=1 로 라우팅
       - 제목/등록일: .board-detail-header (.tit / .data em)
       - 본문 HTML : .board-detail-text
     상세 URL 은 직접 접근도 되므로 h 를 뗀 URL 을 엑셀 참조 링크로 쓴다.
     클릭 → 파싱 → go_back(목록 복귀) 반복.

점검 공지 본문 형식(``■ 작업일시 : 2026년 7월 30일(목) 18:00`` / ``■ 작업내용 : …``)은
기존 라벨 파서가 그대로 처리한다. 본문 표기가 "CRETOP" 이라 자기기관 판별용
``aliases: [CRETOP, 한국평가데이터]`` 를 sites.yaml 에 등록한다.
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .. import register
from ..base import HandlerResult, block_text

logger = logging.getLogger(__name__)

HOST = "https://www.cretop.com"
LIST_URL = HOST + "/PL/CC/PLCC520M1"
LOAD_TIMEOUT_MS = 60_000
LIST_READY_MS = 20_000
RENDER_WAIT_MS = 800
DETAIL_TIMEOUT_MS = 10_000

# dynaPath 봇 탐지 우회용 launch 옵션 (모듈 docstring 참고)
LAUNCH_KWARGS = dict(
    channel="chromium",
    headless=True,
    args=["--disable-blink-features=AutomationControlled"],
)

_ROW_SEL = "div.board__area table tbody tr"
_TITLE_SEL = "td a.link"
_HEADER_SEL = ".board-detail-header"
_HEADER_TITLE_SEL = ".board-detail-header .tit"
_HEADER_DATE_SEL = ".board-detail-header .data em"
_CONTENT_SEL = ".board-detail-text"
_EXPIRED_MARK = "페이지가 만료되었습니다"

_DATE_RE = re.compile(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})")


def _norm_date(text: str) -> str:
    """'2026-09-11' / '2026.9.11' → '20260911'."""
    m = _DATE_RE.search(text or "")
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    return f"{y}{mo}{d}"


def _strip_h(url: str) -> str:
    """사이트가 붙이는 만료성 ``h=`` 타임스탬프 쿼리를 제거."""
    try:
        u = urlparse(url)
        q = {k: v for k, v in parse_qs(u.query).items() if k != "h"}
        return urlunparse(u._replace(query=urlencode(q, doseq=True)))
    except Exception:
        return url


def _rows(page):
    return page.locator(_ROW_SEL).filter(has=page.locator(_TITLE_SEL))


def _wait_list(page) -> bool:
    """목록 행이 렌더될 때까지 대기. 봇 차단([8004]) 화면이면 False."""
    try:
        page.wait_for_selector(_ROW_SEL, timeout=LIST_READY_MS)
        page.wait_for_timeout(RENDER_WAIT_MS)
        return True
    except Exception:
        body = ""
        try:
            body = page.inner_text("body")
        except Exception:
            pass
        if _EXPIRED_MARK in body:
            logger.warning("[KRCI0022] dynaPath 봇 차단 화면([8004]) — launch 옵션 확인 필요")
        else:
            logger.warning("[KRCI0022] 목록 행이 렌더되지 않음 (구조 변경/차단?)")
        return False


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 20)
    results: List[HandlerResult] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**LAUNCH_KWARGS)
            try:
                ctx = browser.new_context(locale="ko-KR",
                                          viewport={"width": 1366, "height": 900})
                page = ctx.new_page()
                page.goto(LIST_URL, wait_until="networkidle", timeout=LOAD_TIMEOUT_MS)
                if not _wait_list(page):
                    return []

                count = min(_rows(page).count(), max_items)
                if count == 0:
                    logger.warning("[KRCI0022] 목록 항목을 찾지 못함 (구조 변경?)")
                    return []
                logger.info(f"[KRCI0022] 목록 {count}건 확인")

                for idx in range(count):
                    row = _rows(page).nth(idx)
                    cells = row.locator("td")
                    title = row.locator(_TITLE_SEL).first.inner_text().strip()
                    posted = ""
                    try:
                        posted = _norm_date(cells.nth(cells.count() - 1).inner_text())
                    except Exception:
                        pass

                    detail_url = LIST_URL
                    detail_html = ""
                    body_text = ""
                    try:
                        row.locator(_TITLE_SEL).first.click(timeout=DETAIL_TIMEOUT_MS)
                        page.wait_for_selector(_CONTENT_SEL, timeout=DETAIL_TIMEOUT_MS)
                        page.wait_for_timeout(400)
                        detail_url = _strip_h(page.url)

                        content_html = page.locator(_CONTENT_SEL).first.inner_html()
                        try:
                            title = page.locator(_HEADER_TITLE_SEL).first.inner_text().strip() or title
                            posted = _norm_date(
                                page.locator(_HEADER_DATE_SEL).first.inner_text()
                            ) or posted
                        except Exception:
                            pass
                        detail_html = (
                            '<div style="padding:24px;font-family:sans-serif;max-width:800px">'
                            f"<h2>{title}</h2><p>등록일자 : {posted}</p><hr/>{content_html}</div>"
                        )
                        body_text = block_text(BeautifulSoup(content_html, "lxml"))
                    except Exception as e:
                        logger.warning(
                            f"[KRCI0022] 상세 수집 실패(idx={idx}, {title!r}): {e}"
                        )
                    finally:
                        if idx < count - 1:
                            try:
                                page.go_back(wait_until="networkidle",
                                             timeout=LOAD_TIMEOUT_MS)
                                if not _wait_list(page):
                                    raise RuntimeError("목록 미렌더")
                            except Exception as e:
                                logger.warning(
                                    f"[KRCI0022] 목록 복귀 실패(idx={idx}), 재로드 시도: {e}"
                                )
                                page.goto(LIST_URL, wait_until="networkidle",
                                          timeout=LOAD_TIMEOUT_MS)
                                if not _wait_list(page):
                                    break

                    if title:
                        results.append(HandlerResult(
                            title=title,
                            posted_date=posted,
                            detail_url=detail_url,
                            detail_html=detail_html,
                            body_text=body_text,
                        ))

                logger.info(f"[KRCI0022] 핸들러 추출 {len(results)}건")
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"[KRCI0022] 수집 실패: {e}")
        return []

    return results


register("KRCI0022", handle)
