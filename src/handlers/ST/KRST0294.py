"""
KRST0294 - 우리투자증권 (Woori Investment & Securities / fundsupermarket)

흐름:
  1. POST https://fundsupermarket.wooriib.com/fmc/FMC6010101/noticeList.do
       Content-Type: application/x-www-form-urlencoded; charset=UTF-8
       X-Requested-With: XMLHttpRequest
       Body: currentPage=1&defaultRow=10&type=all&search_Text=...
     응답 JSON.resList = [
       { ARTICLENO, REG_DTTM, ARTICLE_TTL, ARTICLE_CONTENTS, ... }
     ]
  2. 상세 본문은 ARTICLE_CONTENTS (HTML)에 이미 포함되어 별도 호출 불필요.
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

HOST = "https://fundsupermarket.wooriib.com"
LIST_PATH = "/fmc/FMC6010101/noticeList.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

LIST_BODY = (
    "currentPage=1"
    "&defaultRow=10"
    "&type=all"
    "&search_Text=%EA%B3%B5%EC%A7%80%EC%82%AC%ED%95%AD%EC%9D%84+%EA%B2%80%EC%83%89%ED%95%98%EC%84%B8%EC%9A%94."
)


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' / '2025/05/29' 등에서 숫자만 추출."""
    return re.sub(r"\D", "", s or "")


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko,en;q=0.9,en-US;q=0.8",
        "Connection": "keep-alive",
    })

    list_headers = {
        "Accept": "text/plain, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": HOST,
        "Referer": HOST + "/fmc/FMC6010101/notice.do",
    }

    try:
        r = s.post(
            HOST + LIST_PATH,
            data=LIST_BODY,
            headers=list_headers,
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.warning(f"[KRST0294] 목록 API 호출 실패: {e}")
        return []

    # 응답 형태: {... , "json": {"resList": [...]}, ...}  (구버전은 최상위 "resList")
    res_list = None
    if isinstance(payload, dict):
        nested = payload.get("json")
        if isinstance(nested, dict):
            res_list = nested.get("resList")
        if not res_list:
            res_list = payload.get("resList")
    if not res_list:
        logger.warning("[KRST0294] resList 비어있음")
        return []

    results: List[HandlerResult] = []

    for ele in res_list:
        if len(results) >= max_items:
            break
        if not isinstance(ele, dict):
            continue

        title = str(ele.get("ARTICLE_TTL") or "").strip()
        posted_date = _normalize_date(str(ele.get("REG_DTTM") or ""))
        # YYYYMMDD 8자리만 사용
        if len(posted_date) > 8:
            posted_date = posted_date[:8]

        article_html = str(ele.get("ARTICLE_CONTENTS") or "")

        if not title or not posted_date or len(posted_date) != 8 or not article_html:
            continue

        # 상세 HTML 파싱 (코멘트/불필요 link 태그 제거)
        detail_html = ""
        body_text = ""
        try:
            d_soup = BeautifulSoup(article_html, "lxml")

            # HTML 코멘트 제거
            from bs4 import Comment
            for c in d_soup.find_all(string=lambda t: isinstance(t, Comment)):
                c.extract()

            # 불필요 link/script/style 제거
            for tag in d_soup.find_all(["script", "style"]):
                tag.decompose()
            for link_tag in d_soup.find_all("link"):
                link_tag.decompose()

            # 이미지 src 절대경로화
            for img in d_soup.find_all("img"):
                src = img.get("src") or ""
                if src.startswith("/"):
                    img["src"] = HOST + src

            # lxml 이 html/body 를 자동 감싸므로 body 안쪽을 우선 사용
            body_tag = d_soup.body
            if body_tag is not None:
                detail_html = body_tag.decode_contents().strip()
            else:
                detail_html = str(d_soup).strip()

            raw_text = d_soup.get_text("\n", strip=True)
            # 공백/개행 정리
            raw_text = re.sub(r"\t", "", raw_text)
            raw_text = re.sub(r" {2,}", " ", raw_text)
            raw_text = re.sub(r"\n{2,}", "\n", raw_text)
            body_text = raw_text.strip()
        except Exception as e:
            logger.debug(f"[KRST0294] 상세 HTML 파싱 실패({title[:30]}): {e}")
            detail_html = article_html
            body_text = ""

        if not body_text:
            # 파싱 실패 시 스킵
            logger.debug(f"[KRST0294] body_text 비어있음, 스킵: {title[:30]}")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url="",  # 상세 페이지 별도 URL 없음 (리스트 응답에 본문 포함)
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0294] 핸들러 추출 {len(results)}건")
    return results


register("KRST0294", handle)
