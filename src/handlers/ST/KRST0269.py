"""
KRST0269 - 한화투자증권 (Hanwha Investment & Securities)

흐름:
  1. GET https://www.hanwhawm.com (초기 쿠키)
  2. POST https://www.hanwhawm.com/main/bbs/index.cmd?vc_bid=notice&mode=center&pageVal=1
       Body: cc_gubun=sum15&gubun=1&text=
       Headers: X-Requested-With=XMLHttpRequest,
                Content-Type=application/x-www-form-urlencoded; charset=UTF-8,
                Accept=text/plain, */*; q=0.01,
                Referer=https://www.hanwhawm.com/main/bbs/index.cmd
     응답 HTML: table.noticeList2 tbody tr
       - 행 HTML 안의 javascript:view('NUM','','NN_ID') 패턴에서 num, nn_id 추출
       - td.title a → 제목
       - td.date → 등록일(YYYYMMDD)
  3. 상세 POST https://www.hanwhawm.com/main/bbs/indexView.cmd
       ?vc_bid=notice&mode=center&cc_gubun=sum15&nn_id={nn_id}&num={num}
       Body: cc_gubun=sum15&gubun=1&text=
       div.realContentWrap 추출 → detail_html / body_text
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

HOST = "https://www.hanwhawm.com"
LIST_PATH = "/main/bbs/index.cmd"
DETAIL_PATH = "/main/bbs/indexView.cmd"
REFERER = HOST + LIST_PATH

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

LIST_BODY = "cc_gubun=sum15&gubun=1&text="

# javascript:view('NUM','','NN_ID')
_VIEW_RE = re.compile(r"javascript:view\('([^']*)','([^']*)','([^']*)'")


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Connection": "keep-alive",
    })

    # 1) 초기 쿠키
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0269] 초기 GET 실패(무시): {e}")

    # 2) 목록 POST (페이지 1)
    list_url = f"{HOST}{LIST_PATH}?vc_bid=notice&mode=center&pageVal=1"
    list_headers = {
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/plain, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }

    try:
        r = s.post(list_url, data=LIST_BODY, headers=list_headers, timeout=15)
        r.raise_for_status()
        try:
            r.encoding = r.apparent_encoding or "utf-8"
            list_html = r.text
        except Exception:
            list_html = r.content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[KRST0269] 목록 요청 실패: {e}")
        return []

    soup = BeautifulSoup(list_html, "lxml")
    rows = soup.select("table.noticeList2 tbody tr")
    if not rows:
        logger.warning("[KRST0269] table.noticeList2 tbody tr 없음")
        return []

    # 상세 호출용 헤더 (X-Requested-With 제거, Accept/Content-Type 변경)
    detail_headers = {
        "Referer": REFERER,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        row_html = tr.decode_contents()
        m = _VIEW_RE.search(row_html)
        if not m:
            continue
        num = m.group(1).strip()
        nn_id = m.group(3).strip()
        if not num or not nn_id:
            continue

        title_a = tr.select_one("td.title a")
        title = title_a.get_text(strip=True) if title_a else ""
        if not title:
            continue

        date_td = tr.select_one("td.date")
        raw_date = date_td.get_text(strip=True) if date_td else ""
        posted_date = re.sub(r"[\s\-/.]", "", raw_date)

        detail_url = (
            f"{HOST}{DETAIL_PATH}"
            f"?vc_bid=notice&mode=center&cc_gubun=sum15"
            f"&nn_id={nn_id}&num={num}"
        )

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(
                detail_url,
                data=LIST_BODY,
                headers=detail_headers,
                timeout=15,
            )
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")

            # 불필요 요소 제거 (JS 원본 참고)
            for sel in [
                "script",
                "#skiptoContent",
                "#header",
                "div.btnWrap",
                "ul.bbs_prevNext",
                "div.incRight_banner",
                "div.new_footer",
                "div.f_account",
                "div.renewal_guide_layer",
            ]:
                for tag in d_soup.select(sel):
                    tag.decompose()

            content = d_soup.select_one("div.realContentWrap")
            if content is not None:
                detail_html = str(content).strip()
                body_text = content.get_text("\n", strip=True)
            else:
                # fallback
                body_tag = d_soup.body
                if body_tag is not None:
                    detail_html = body_tag.decode_contents().strip()
                    body_text = body_tag.get_text("\n", strip=True)
                else:
                    detail_html = dt_text
                    body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0269] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0269] 핸들러 추출 {len(results)}건")
    return results


register("KRST0269", handle)
