"""
KRBK0101 - 저축은행중앙회 (통합: 4개 하위 기관 집계)

원본 스크래퍼 그대로 포팅. 단일 기관이 아니라 아래 4곳을 각각 긁어
제목에 태그를 붙여 합칩니다.
  [중앙회]  fsb.or.kr            (HTML)
  [더케이]  thekbank.co.kr       (form POST → HTML)
  [키움YES] kiwoomyesbank.com    (form POST → HTML)
  [JT친애]  jtchinae-bank.co.kr  (form POST → HTML, EUC-KR)

각 하위 기관 본문에는 자기 기관명만 등장하므로, sites.yaml 의 aliases 에
[중앙회, 더케이, 키움, JT친애] 를 등록해 자기기관 필터를 통과시킵니다.

상세(detail) 호출 비용을 줄이기 위해 제목에 점검 힌트가 있는 공지만 상세를 가져옵니다.
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_TITLE_HINTS = ("점검", "중단", "중지", "작업", "이용제한", "거래중단", "maintenance", "순단")
_DATE_RE = re.compile(r"(\d{4})[-.\s]?(\d{2})[-.\s]?(\d{2})")


def _grap(s: str, pre: str, suf: str) -> str:
    i = s.find(pre)
    if i < 0:
        return ""
    i += len(pre)
    j = s.find(suf, i)
    return s[i:j] if j >= 0 else ""


def _norm_date(s: str) -> str:
    m = _DATE_RE.search(s or "")
    return (m.group(1) + m.group(2) + m.group(3)) if m else ""


def _hint(title: str) -> bool:
    return any(h in title for h in _TITLE_HINTS)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def _result(tag, title, date, url, node) -> HandlerResult:
    for st in node.find_all("style"):
        st.decompose()
    return HandlerResult(
        title=f"[{tag}] {title}".strip(),
        posted_date=date,
        detail_url=url,
        detail_html=str(node).strip(),
        body_text=node.get_text("\n", strip=True),
    )


# ---------------------------------------------------------------- 중앙회
def _fsb() -> List[HandlerResult]:
    host = "https://www.fsb.or.kr"
    out: List[HandlerResult] = []
    s = _session()
    s.get(host, timeout=20)
    r = s.get(host + "/nesfnewnoti_0100.act",
              headers={"Referer": host + "/nesfnewnoti_0100.act"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    for li in soup.select("ul.common-list02 > li"):
        ttl = li.select_one("p.title")
        if not ttl:
            continue
        title = re.sub(r"\s+", " ", ttl.get_text(" ", strip=True)).strip()
        if not title or not _hint(title):
            continue
        seq = _grap(str(ttl), "goDetail('", "'")
        if not seq:
            continue
        date = _norm_date(li.select_one("div.info div.date").get_text(strip=True)
                          if li.select_one("div.info div.date") else "")
        durl = f"{host}/nesfnewnoti_0200.act?SEQ={seq}&SEARCHTEXT="
        dr = s.get(durl, timeout=20)
        dsoup = BeautifulSoup(dr.text, "lxml")
        node = dsoup.select_one("div.common-viewdetail")
        if not node:
            continue
        out.append(_result("중앙회", title, date, durl, node))
    return out


# ---------------------------------------------------------------- 더케이
def _thek() -> List[HandlerResult]:
    host = "https://www.thekbank.co.kr"
    out: List[HandlerResult] = []
    s = _session()
    s.get(host, timeout=20)
    r = s.post(host + "/cs/noticeList.do", data="page=1&list_cnt=10&idx=",
               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    heads = [th.get_text(strip=True) for th in soup.select("div.contents > table thead th")]
    ti = heads.index("제목") if "제목" in heads else 0
    di = heads.index("등록일") if "등록일" in heads else len(heads) - 1
    for tr in soup.select("div.contents > table tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di):
            continue
        title = re.sub(r"\s+", " ", tds[ti].get_text(" ", strip=True)).strip()
        if not title or not _hint(title):
            continue
        idx = _grap(str(tr), "goView(", ")").strip().strip("'\"")
        if not idx:
            continue
        date = _norm_date(tds[di].get_text(strip=True))
        durl = f"{host}/cs/noticeView.do?page=1&list_cnt=10&idx={idx}"
        dr = s.get(durl, timeout=20)
        node = BeautifulSoup(dr.text, "lxml").select_one("div.view-con")
        if not node:
            continue
        out.append(_result("더케이", title, date, durl, node))
    return out


# ---------------------------------------------------------------- 키움YES
def _kiwoom() -> List[HandlerResult]:
    host = "https://www.kiwoomyesbank.com"
    out: List[HandlerResult] = []
    s = _session()
    s.get(host, timeout=20)
    r = s.post(host + "/CstList_002.act", data="CTGR_NO=1&TAB_CD=",
               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    heads = [th.get_text(strip=True) for th in soup.select("div.bbs-list table thead th")]
    ti = heads.index("제목") if "제목" in heads else 1
    di = heads.index("등록일") if "등록일" in heads else 2
    for tr in soup.select("div.bbs-list table tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di):
            continue
        cell = BeautifulSoup(str(tds[ti]), "lxml")
        for c in cell.select("span.ico-category"):
            c.decompose()
        title = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
        if not title or not _hint(title):
            continue
        params = _grap(str(tr), "fn_detail(", ')"').split(",")
        if len(params) < 3:
            continue
        p0, p1, p2 = (p.strip().strip("'\"") for p in params[:3])
        date = _norm_date(tds[di].get_text(strip=True))
        durl = f"{host}/CstInfo_001.act?CTGR_NO=1&NTCE_NO={p0}&SBCD={p1}&PARA={quote(p2, safe='')}"
        dr = s.get(durl, timeout=20)
        node = BeautifulSoup(dr.text, "lxml").select_one("div.bbs-cont")
        if not node:
            continue
        out.append(_result("키움YES", title, date, durl, node))
    return out


# ---------------------------------------------------------------- JT친애 (EUC-KR)
def _jt() -> List[HandlerResult]:
    host = "https://www.jtchinae-bank.co.kr"
    out: List[HandlerResult] = []
    s = _session()
    s.get(host, timeout=20)
    body = ("currentPage=1&pageNo=1&pageSize=&ord=&ttlValue=&ctnsValue="
            "&selectOption=1&searchValue=")
    r = s.post(host + "/bank/newsList.do", data=body,
               headers={"Content-Type": "application/x-www-form-urlencoded",
                        "Referer": host + "/bank/newsList.do"}, timeout=20)
    r.raise_for_status()
    html = r.content.decode("euc-kr", "replace")
    soup = BeautifulSoup(html, "lxml")
    heads = [th.get_text(strip=True) for th in
             soup.select("div.sub-container div.sub-inner table.ta-c thead th")]
    ti = heads.index("제목") if "제목" in heads else 2
    di = heads.index("등록일") if "등록일" in heads else 3
    ci = heads.index("구분") if "구분" in heads else 1
    for tr in soup.select("div.sub-container div.sub-inner table.ta-c tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di, ci):
            continue
        gubun = tds[ci].get_text(strip=True)
        if gubun in ("공고", "안내"):
            continue
        title = re.sub(r"\s+", " ", tds[ti].get_text(" ", strip=True)).strip()
        if not title or not _hint(title):
            continue
        idx = _grap(str(tr), "javascript:goDetailView('", "');")
        if not idx:
            continue
        date = _norm_date(tds[di].get_text(strip=True))
        durl = (f"{host}/bank/newsViewForm.do?currentPage=&pageNo=1&pageSize=&ord={idx}"
                "&ttlValue=&ctnsValue=&selectOption=1&searchValue=")
        dr = s.get(durl, timeout=20)
        dhtml = dr.content.decode("euc-kr", "replace")
        node = BeautifulSoup(dhtml, "lxml").select_one("div#content div.view-text")
        if not node:
            continue
        out.append(_result("JT친애", title, date, durl, node))
    return out


def handle(site_config) -> List[HandlerResult]:
    results: List[HandlerResult] = []
    for name, fn in (("중앙회", _fsb), ("더케이", _thek),
                     ("키움YES", _kiwoom), ("JT친애", _jt)):
        try:
            sub = fn()
            logger.info(f"[KRBK0101] {name} {len(sub)}건")
            results.extend(sub)
        except Exception as e:
            logger.warning(f"[KRBK0101] {name} 실패(무시): {e}")
    logger.info(f"[KRBK0101] 핸들러 추출 합계 {len(results)}건")
    return results


register("KRBK0101", handle)
