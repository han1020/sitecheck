"""
KRBK0037 - 전북은행 (JBBank)

흐름:
  1. GET https://www.jbbank.co.kr/                                  → 쿠키
  2. POST https://www.jbbank.co.kr/search/commonnews.act
       Body: _DUMMY_INPUT=&_MENU_PRJ_TYPE_=INTRODUCE&bbs_spage=1
     응답 HTML. div.board_list_01_blue table tbody tr 행, #form1 의 input 모음
  3. 각 행: a onclick="jbbbs_detailPage($('#form1'), <bbs_seq>);" 에서 bbs_seq 추출
  4. POST https://www.jbbank.co.kr/search/commonnews_detail.act
       Body: form input + bbs_seq + bbs_listAction=commonnews.act + BBSSEARCH_TYPE=INBN_BLBD_TITL_NM
  5. div.board_view_01_blue 본문
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)
HOST = "https://www.jbbank.co.kr"
LIST_PATH = "/search/commonnews.act"
DETAIL_PATH = "/search/commonnews_detail.act"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_BBS_SEQ_RE = re.compile(r"jbbbs_detailPage\(\s*\$\(['\"]#form1['\"]\)\s*,\s*(\d+)")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0037] 워밍업 실패(무시): {e}")

    s.headers.update({"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = s.post(HOST + LIST_PATH,
                   data="_DUMMY_INPUT=&_MENU_PRJ_TYPE_=INTRODUCE&bbs_spage=1",
                   timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[KRBK0037] 목록 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    form_data = {
        inp.get("name"): inp.get("value", "")
        for inp in soup.select("#form1 input")
        if inp.get("name")
    }
    form_data.update({
        "bbs_listAction": "commonnews.act",
        "BBSSEARCH_TYPE": "INBN_BLBD_TITL_NM",
        "_DUMMY_INPUT": "",
    })

    rows = soup.select("div.board_list_01_blue table tbody tr")
    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []
    s.headers["Referer"] = HOST + LIST_PATH

    for tr in rows[:max_items]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        # 제목/등록일 - thead 순서: 번호 / 제목 / 등록일 / 조회수
        title_td = tds[1] if len(tds) >= 2 else None
        a = title_td.find("a") if title_td else None
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        m = _BBS_SEQ_RE.search(a.get("onclick") or "")
        if not m:
            continue
        bbs_seq = m.group(1)
        # 등록일은 td 중 'YYYY.MM.DD' 또는 'YYYY-MM-DD' 패턴 찾기
        posted_date = ""
        for td in tds:
            t = td.get_text(strip=True)
            if re.fullmatch(r"\d{4}[.\-/]\d{2}[.\-/]\d{2}", t):
                posted_date = re.sub(r"\D", "", t)
                break

        body = dict(form_data)
        body["bbs_seq"] = bbs_seq

        contents_html, body_text = "", ""
        try:
            dr = s.post(HOST + DETAIL_PATH, data=urlencode(body), timeout=15)
            dr.encoding = dr.apparent_encoding or "utf-8"
            d_soup = BeautifulSoup(dr.text, "lxml")
            for sel in [".board_reply_blue", "iframe", "#left_menu",
                        "#skipnavi", "#div_com_timeout_popup", "#_LOADING_",
                        ".renew-footer", ".pre_next", "style", "script"]:
                for el in d_soup.select(sel):
                    el.decompose()
            view = d_soup.select_one("div.board_view_01_blue")
            if view:
                contents_html = str(view)
                body_text = view.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRBK0037] 상세 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title, posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=contents_html, body_text=body_text,
        ))

    logger.info(f"[KRBK0037] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0037", handle)
