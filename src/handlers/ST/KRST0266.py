"""
KRST0266 - SK증권 (SK Securities)

흐름:
  1) GET https://www.sks.co.kr (초기 쿠키)
  2) GET https://www.sks.co.kr/main/customer/notice/CU_12000_S01.cmd
       Referer: https://www.sks.co.kr
     → #js-boardList 내부의 각 공지 행에서
         · 제목: <a href="/main/customer/notice/CU_12000_S02.cmd?senumber=...">제목</a>
         · 등록일: 행 내 <li> 들 중 2번째 (YYYY.MM.DD) → '.' 제거 → YYYYMMDD
         · 상세 링크: 위 a 태그의 href
  3) 상세: GET https://www.sks.co.kr{href}
       Referer: 목록 URL
     → div.post_contents 내용을 detail_html / body_text 로 사용
       script / 주석 / 좌측 메뉴 등 잡 요소는 BeautifulSoup 으로 제거
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Comment

from .. import register
from ..base import HandlerResult, block_text

logger = logging.getLogger(__name__)

HOST = "https://www.sks.co.kr"
LIST_PATH = "/main/customer/notice/CU_12000_S01.cmd"
LIST_URL = HOST + LIST_PATH
DETAIL_HREF_HINT = "CU_12000_S02.cmd"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' 등에서 숫자만 추출하여 YYYYMMDD 반환."""
    return re.sub(r"\D", "", s or "")


def _find_row(a_tag):
    """공지 상세 a 태그를 감싸는 1행(row) 컨테이너를 찾는다.

    SK증권은 #js-boardList 하위에 li (또는 tr) 단위로 행이 구성되어 있다.
    가장 가까운 li / tr / div(class에 'list' 또는 'item' 포함) 를 행으로 본다.
    """
    if a_tag is None:
        return None
    cur = a_tag
    for _ in range(6):
        cur = cur.parent
        if cur is None or cur.name in (None, "[document]"):
            break
        if cur.name in ("li", "tr"):
            return cur
    return a_tag.parent


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 초기 쿠키
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0266] 초기 GET 실패(무시): {e}")

    # 2) 목록 GET
    try:
        r = s.get(
            LIST_URL,
            headers={
                "Referer": HOST + "/",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
            },
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0266] 목록 요청 실패: {e}")
        return []

    try:
        r.encoding = r.apparent_encoding or "utf-8"
        list_html = r.text
    except Exception:
        list_html = r.content.decode("utf-8", errors="replace")

    soup = BeautifulSoup(list_html, "lxml")

    board = soup.select_one("#js-boardList")
    if board is None:
        # 일부 페이지에서 id 가 변경되었을 가능성 대비: CU_12000_S02 링크가 있는 부모 컨테이너로 fallback
        anchors_all = soup.select(f'a[href*="{DETAIL_HREF_HINT}"]')
        if not anchors_all:
            logger.warning("[KRST0266] #js-boardList / 상세 링크 모두 없음")
            return []
        board = soup
    anchors = board.select(f'a[href*="{DETAIL_HREF_HINT}"]')

    if not anchors:
        logger.warning("[KRST0266] 공지 a 태그 없음")
        return []

    # 헤더 (Referer) 갱신 - 이후 상세 요청용
    s.headers.update({"Referer": LIST_URL})

    seen_hrefs: set[str] = set()
    results: List[HandlerResult] = []

    for a in anchors:
        if len(results) >= max_items:
            break

        href = a.get("href") or ""
        if not href or DETAIL_HREF_HINT not in href:
            continue
        # 동일 href 중복 방지
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        title = re.sub(r"\s+", " ", a.get_text()).strip()
        if not title:
            continue

        row = _find_row(a)
        posted_date = ""
        if row is not None:
            li_tags = row.find_all("li")
            # JS: ele.grap('<li>', '</li>', 1) → 2번째 <li>
            if len(li_tags) >= 2:
                posted_date = _normalize_date(li_tags[1].get_text(strip=True))
            elif li_tags:
                posted_date = _normalize_date(li_tags[0].get_text(strip=True))

        # 행에서 못 찾으면 행 텍스트 전체에서 날짜 패턴 fallback
        if (not posted_date or len(posted_date) != 8) and row is not None:
            m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", row.get_text(" ", strip=True))
            if m:
                posted_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"

        if not posted_date or len(posted_date) != 8:
            logger.debug(f"[KRST0266] 날짜 파싱 실패 (title={title[:30]})")
            continue

        detail_url = urljoin(HOST, href)

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")

            # 불필요 요소 제거 (JS와 동일)
            for sel in [
                "script", "#quick", "#snsShareQ", "#frmCnd",
                "div#footer", "#accNav", "#gnb", "div.lineMapWrap",
                "#lnb", "div.pageOptWrap", "div.boardPager",
            ]:
                for t in d_soup.select(sel):
                    t.decompose()

            # HTML 주석 제거
            for c in d_soup.find_all(string=lambda x: isinstance(x, Comment)):
                c.extract()

            view = d_soup.select_one("div.post_contents")
            if view is not None:
                detail_html = view.decode_contents().strip()
                # 단어별 <span> 본문(워드 붙여넣기)이라 블록 단위로 줄을 나눈다
                body_text = block_text(view)
            else:
                # fallback
                detail_html = dt_text
                body_text = block_text(d_soup)
        except Exception as e:
            logger.debug(f"[KRST0266] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0266] 핸들러 추출 {len(results)}건")
    return results


register("KRST0266", handle)
