"""
KRST0270 - 하나증권 (Hana Securities)

흐름:
  1) GET https://www.hanaw.com/corebbs5/notice/list/list.cmd
     → #mainContainer 내 <li> 목록 파싱
       - em.num : 번호
       - .subject > a[href] : 상세 링크 / 제목
  2) 상세: GET https://www.hanaw.com{link}
     - span.txtbasic : 등록일 ("YYYY.MM.DD" → "YYYYMMDD")
     - #contn : 본문 HTML
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.hanaw.com"
LIST_PATH = "/corebbs5/notice/list/list.cmd"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' 등에서 숫자만 추출."""
    return re.sub(r"\D", "", s or "")


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

    # 1) 목록 GET
    list_url = HOST + LIST_PATH
    try:
        r = s.get(list_url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0270] 목록 요청 실패: {e}")
        return []

    try:
        r.encoding = r.apparent_encoding or "utf-8"
        list_html = r.text
    except Exception:
        list_html = r.content.decode("utf-8", errors="replace")

    soup = BeautifulSoup(list_html, "lxml")
    container = soup.select_one("#mainContainer")
    if container is None:
        logger.warning("[KRST0270] #mainContainer 영역 없음")
        return []

    li_items = container.find_all("li")
    if not li_items:
        logger.warning("[KRST0270] 목록 <li> 없음")
        return []

    results: List[HandlerResult] = []

    for li in li_items:
        if len(results) >= max_items:
            break

        num_em = li.select_one("em.num") or li.find(class_="num")
        subject = li.select_one(".subject") or li.find(class_="subject")
        if num_em is None or subject is None:
            # 공지 항목이 아닌 <li>(탭 네비 등) 건너뛰기
            continue

        notice_no = num_em.get_text(strip=True)
        a_tag = subject.find("a")
        if a_tag is None:
            continue

        href = (a_tag.get("href") or "").strip()
        title = a_tag.get_text(strip=True)

        if not notice_no or not title or not href:
            continue

        if href.startswith("http"):
            detail_url = href
        elif href.startswith("/"):
            detail_url = HOST + href
        else:
            detail_url = HOST + "/" + href

        # 2) 상세 GET
        detail_html = ""
        body_text = ""
        posted_date = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")

            date_el = d_soup.select_one("span.txtbasic")
            if date_el is not None:
                posted_date = _normalize_date(date_el.get_text(" ", strip=True))

            content = d_soup.select_one("#contn")
            if content is not None:
                # 스크립트 제거
                for t in content.find_all(["script", "style"]):
                    t.decompose()
                detail_html = content.decode_contents().strip()
                body_text = content.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0270] 상세 요청 실패({title[:30]}): {e}")
            continue

        if not posted_date or len(posted_date) != 8:
            logger.debug(f"[KRST0270] 등록일 파싱 실패({title[:30]}): '{posted_date}'")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0270] 핸들러 추출 {len(results)}건")
    return results


register("KRST0270", handle)
