"""
KRST0265 - LS증권 (구 이베스트투자증권, LS Securities)

2024 사명 변경으로 호스트가 https://www.ls-sec.co.kr 로 바뀌었고 공지 본문도 'LS증권'으로만
표기된다. sites.yaml 의 name 은 LS증권, 옛 이름은 aliases 로 두어 자기기관 판별을 통과시킨다.

흐름:
  1) GET https://www.ls-sec.co.kr/Main.jsp (초기 쿠키)
  2) GET https://www.ls-sec.co.kr/EtwFrontBoard/List.jsp
        ?board_no=42&left_menu_no=1147&front_menu_no=393&parent_menu_no=392
     EUC-KR 인코딩 HTML 응답 → caption '게시판리스트' table tbody tr 파싱
  3) 각 행에서 제목(td.subject 안의 a) / 등록일(3번째 td) / View.jsp 쿼리스트링 추출
  4) 상세: GET https://www.ls-sec.co.kr/EtwFrontBoard/View.jsp?{queryStr}
        EUC-KR 디코딩 후 div.tbViewCon 내용을 detail_html/body_text 로 사용
"""
from __future__ import annotations

import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.ls-sec.co.kr"
MAIN_PATH = "/Main.jsp"
LIST_PATH = (
    "/EtwFrontBoard/List.jsp"
    "?board_no=42&left_menu_no=1147&front_menu_no=393&parent_menu_no=392"
)
DETAIL_BASE = "/EtwFrontBoard/View.jsp"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)


def _decode_eucr(resp: requests.Response) -> str:
    """EUC-KR 응답을 안전하게 디코딩."""
    try:
        return resp.content.decode("euc-kr", errors="replace")
    except Exception:
        return resp.text


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' 등에서 숫자만 추출하여 YYYYMMDD 반환."""
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits


def _extract_query(href: str) -> str:
    """href='View.jsp?xxx=yyy&...' 또는 'View.jsp?...' 에서 쿼리스트링만 추출."""
    if not href:
        return ""
    if "?" in href:
        return href.split("?", 1)[1]
    return ""


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
        s.get(HOST + MAIN_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0265] 초기 Main.jsp GET 실패(무시): {e}")

    # 2) 목록 GET
    list_url = HOST + LIST_PATH
    try:
        r = s.get(
            list_url,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
                "Referer": HOST + MAIN_PATH,
            },
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0265] 목록 요청 실패: {e}")
        return []

    list_html = _decode_eucr(r)
    soup = BeautifulSoup(list_html, "lxml")

    # caption 텍스트에 '게시판리스트' 가 포함된 table 을 찾는다.
    target_table = None
    for cap in soup.find_all("caption"):
        cap_text = cap.get_text(strip=True)
        if "게시판리스트" in cap_text:
            target_table = cap.find_parent("table")
            break

    if target_table is None:
        logger.warning("[KRST0265] 목록 테이블(caption '게시판리스트') 없음")
        return []

    tbody = target_table.find("tbody")
    if tbody is None:
        logger.warning("[KRST0265] tbody 없음")
        return []

    rows = tbody.find_all("tr", recursive=False)
    if not rows:
        logger.warning("[KRST0265] tbody tr 없음")
        return []

    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        # 제목 셀 (td.subject) 와 a 태그
        subject_td = tr.find("td", class_="subject")
        if subject_td is None:
            continue

        a_tag = subject_td.find("a")
        if a_tag is None:
            continue

        # 원본 JS는 <strong> 안의 텍스트를 우선 사용
        strong = a_tag.find("strong")
        if strong is not None:
            title = strong.get_text(strip=True)
        else:
            title = a_tag.get_text(strip=True)

        if not title:
            continue

        href = a_tag.get("href") or ""
        query_str = _extract_query(href)
        if not query_str:
            # onclick 등에 들어있는 케이스 대비
            onclick = a_tag.get("onclick") or ""
            query_str = _extract_query(onclick)
        if not query_str:
            continue

        # 등록일: subject td 다음 td (실제 구조는 [빈, 카테고리, subject, 날짜, 조회수])
        # td 위치가 사이트 개편에 따라 달라질 수 있어, td 텍스트 중 YYYY.MM.DD 형태를 직접 찾는다.
        tds = tr.find_all("td", recursive=False)
        import re as _re
        raw_date = ""
        for td in tds:
            t = td.get_text(strip=True)
            if _re.match(r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}$", t):
                raw_date = t
                break
        posted_date = _normalize_date(raw_date)

        if not posted_date or len(posted_date) != 8:
            # 날짜 정상 추출 실패 시 스킵
            logger.debug(f"[KRST0265] 날짜 파싱 실패: '{raw_date}' (title={title[:30]})")
            continue

        detail_url = f"{HOST}{DETAIL_BASE}?{query_str}"

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(
                detail_url,
                headers={
                    "Referer": list_url,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/webp,image/apng,*/*;q=0.8,"
                        "application/signed-exchange;v=b3;q=0.7"
                    ),
                },
                timeout=20,
            )
            dr.raise_for_status()
            dt_text = _decode_eucr(dr)
            d_soup = BeautifulSoup(dt_text, "lxml")

            # 주석/스크립트/스타일 제거
            for tag in d_soup(["script", "style"]):
                tag.decompose()

            view = d_soup.select_one("div.tbViewCon")
            if view is not None:
                detail_html = str(view).strip()
                body_text = view.get_text("\n", strip=True)
            else:
                # fallback
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0265] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0265] 핸들러 추출 {len(results)}건")
    return results


register("KRST0265", handle)
