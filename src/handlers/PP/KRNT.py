"""
KRNT - 국세청 홈택스 (메인 배너 + 공지사항)

홈택스는 웹스퀘어 SPA + eversafe 봇 탐지라서 requests 로는 접근 불가
("비정상적인 요청이 감지되었습니다"). 모든 wqAction.do 요청 본문 끝에
'<nts<nts>nts>' 마커 + JS가 계산한 토큰이 붙어야 한다. Playwright 로 메인
페이지를 실제 렌더링하면 토큰·쿠키가 유효한 세션이 만들어진다.

흐름 (메인 페이지 1회 로드):
  1. goto 메인(index_pp.xml) → 로드 중 발생하는 wqAction 요청에서 토큰 채집,
     배너 조회 응답(ATXPPCBA001R11 JSON) 캡처
       - pubcPotlBnerAdmDVOList  : 메인 롤링 배너 (제목 '...(메인)/(서브)' 쌍)
       - pubcPotlBnerAdmDVOList2 : 메인 노출 공지사항 (tbbsSn/tbbsTtl/bltnStrtDt)
  2. 공지사항 각 건: 같은 세션에서 page.evaluate(fetch) 로 상세 조회
     (ATXPPBAA001R02, blrdNo=1 공지사항 게시판 + tbbsSn, 캡처한 토큰 재사용)
     → intgTbbsInqrDVO.tbbsCntn 이 본문 HTML
  3. 배너는 이미지지만 응답의 bnerCntn/bnerImgCntn 에 대체 텍스트 전문이
     들어있다(일시 포함). 이를 body_text 로 쓰면 일반 파이프라인이 일시를
     파싱해 자동 감지한다. bnerFleUrl(실제 배너 이미지)은 detail_html 에
     넣어 대시보드 캡처가 실제 배너를 보여주게 한다.

주의: 토큰은 세션(페이지) 단위로 유효하며 다른 화면(screenId)의 액션에는
재사용이 거부되기도 한다. R02 는 메인 세션 토큰으로 호출 가능함을 확인함.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .. import register
from ..base import HandlerResult, block_text

logger = logging.getLogger(__name__)

MAIN_URL = (
    "https://hometax.go.kr/websquare/websquare.html"
    "?w2xPath=/ui/pp/index_pp.xml&menuCd=index3"
)
ACTION_URL = "https://hometax.go.kr/wqAction.do"
BANNER_ACTION = "ATXPPCBA001R11"   # 메인 배너/공지 목록 조회
DETAIL_ACTION = "ATXPPBAA001R02"   # 통합게시판 게시글 상세 조회
TOKEN_MARK = "<nts<nts>nts>"
NOTICE_BLRD_NO = "1"               # 공지사항 게시판 번호
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
LOAD_TIMEOUT_MS = 60_000
CAPTURE_WAIT_MS = 30_000           # 배너 응답 캡처 최대 대기

# 배너 제목의 '...(메인)' / '...(서브)' 꼬리표 (메인/서브 쌍으로 중복 등록됨)
_BNER_SUFFIX_RE = re.compile(r"\s*\((메인|서브)\)\s*$")
# bnerUrl 의 '<WINDOWOPEN>url' / '<INBODY>url' / '<MENUID>id' 태그
_BNER_URL_RE = re.compile(r"^<[A-Z]+>")


def _detail_page_url(tbbs_sn) -> str:
    """엑셀 참조용 공지 상세 팝업 URL."""
    return (
        "https://hometax.go.kr/websquare/websquare.html"
        f"?w2xPath=/ui/pp/b/a/UTXPPBAA27.xml&tbbsSn={tbbs_sn}"
    )


def _fetch_detail(page, token: str, tbbs_sn) -> Optional[dict]:
    """메인 세션 안에서 공지 상세(R02)를 fetch 로 조회."""
    payload = {
        "blrdNo": NOTICE_BLRD_NO, "tbbsSn": str(tbbs_sn), "inqrCntPlus": "true",
        "tbbsClsfCd": "all", "schType": "all", "schCntn": "",
        "strtDt": "", "endDt": "",
    }
    body = json.dumps(payload, ensure_ascii=False) + TOKEN_MARK + token
    url = (
        f"{ACTION_URL}?actionId={DETAIL_ACTION}"
        "&screenId=UTXPPBAA27&popupYn=false&realScreenId="
    )
    text = page.evaluate(
        """async ({url, body}) => {
            const resp = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json; charset=UTF-8'},
                body,
                credentials: 'include',
            });
            return await resp.text();
        }""",
        {"url": url, "body": body},
    )
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        logger.warning(f"[KRNT] 상세 응답 JSON 아님 (tbbsSn={tbbs_sn}): {text[:120]!r}")
        return None
    result = (data.get("resultMsg") or {}).get("result")
    vo = data.get("intgTbbsInqrDVO") or {}
    if result != "S" or not vo.get("tbbsTtl"):
        logger.warning(
            f"[KRNT] 상세 조회 실패 (tbbsSn={tbbs_sn}): "
            f"{(data.get('resultMsg') or {}).get('msg', '')}"
        )
        return None
    return vo


def _notice_results(page, token: str, notices: list, max_items: int) -> List[HandlerResult]:
    results: List[HandlerResult] = []
    for row in notices[:max_items]:
        title = (row.get("tbbsTtl") or "").strip()
        tbbs_sn = row.get("tbbsSn")
        if not title or tbbs_sn is None:
            continue
        posted = str(row.get("bltnStrtDt") or "")
        detail_url = _detail_page_url(tbbs_sn)

        detail_html = ""
        body_text = ""
        try:
            vo = _fetch_detail(page, token, tbbs_sn)
        except Exception as e:
            logger.warning(f"[KRNT] 상세 fetch 실패 (tbbsSn={tbbs_sn}): {e}")
            vo = None
        if vo:
            cntn = vo.get("tbbsCntn") or ""
            posted = str(vo.get("bltnStrtDt") or posted)
            detail_html = (
                '<div style="padding:24px;font-family:sans-serif;max-width:800px">'
                f"<h2>{title}</h2><hr/>{cntn}</div>"
            )
            body_text = block_text(BeautifulSoup(cntn, "lxml"))

        results.append(HandlerResult(
            title=title,
            posted_date=posted,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))
    return results


def _banner_results(banners: list) -> List[HandlerResult]:
    results: List[HandlerResult] = []
    seen = set()
    for row in banners:
        raw_name = (row.get("bnerTrgtNm") or "").strip()
        title = _BNER_SUFFIX_RE.sub("", raw_name)
        if not title or title in seen:
            continue
        seen.add(title)
        url = _BNER_URL_RE.sub("", (row.get("bnerUrl") or "").strip())
        if not url.startswith("http"):
            url = ""  # <MENUID> 등 내부 메뉴 이동은 링크로 표현 불가
        # 배너 이미지의 대체 텍스트 전문 (일시 등 포함) — 본문으로 사용
        body_text = (row.get("bnerCntn") or row.get("bnerImgCntn") or "").strip()
        img_url = (row.get("bnerFleUrl") or "").strip()
        detail_html = (
            '<div style="padding:24px;font-family:sans-serif;max-width:800px">'
            f"<h2>{title}</h2>"
            + (f'<p><img src="{img_url}" style="max-width:100%"/></p>' if img_url else "")
            + (f"<p>{body_text}</p>" if body_text else "")
            + (f'<p><a href="{url}">{url}</a></p>' if url else "")
            + "</div>"
        )
        results.append(HandlerResult(
            title=title,
            posted_date=str(row.get("bltnStrtDt") or row.get("bnerRgtDtm") or ""),
            detail_url=url,
            detail_html=detail_html,
            body_text=body_text,
        ))
    return results


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 10)
    captured: dict = {}
    tokens: list = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
                page = ctx.new_page()

                def on_request(req):
                    if "wqAction.do" in req.url:
                        post = req.post_data
                        if post and TOKEN_MARK in post:
                            tokens.append(post.split(TOKEN_MARK, 1)[1])

                def on_response(resp):
                    if f"actionId={BANNER_ACTION}" in resp.url:
                        try:
                            captured["banner"] = resp.text()
                        except Exception as e:
                            logger.debug(f"[KRNT] 배너 응답 읽기 실패: {e}")

                page.on("request", on_request)
                page.on("response", on_response)
                page.goto(MAIN_URL, wait_until="domcontentloaded",
                          timeout=LOAD_TIMEOUT_MS)

                # 배너 응답 + 토큰이 잡힐 때까지 대기 (SPA 렌더링 완료 신호)
                waited = 0
                while ("banner" not in captured or not tokens) and waited < CAPTURE_WAIT_MS:
                    page.wait_for_timeout(500)
                    waited += 500

                if "banner" not in captured:
                    logger.warning("[KRNT] 배너 조회 응답을 캡처하지 못함 (봇 차단/구조 변경?)")
                    return []

                data = json.loads(captured["banner"])
                notices = data.get("pubcPotlBnerAdmDVOList2") or []
                banners = data.get("pubcPotlBnerAdmDVOList") or []

                results: List[HandlerResult] = []
                if tokens:
                    results += _notice_results(page, tokens[-1], notices, max_items)
                else:
                    logger.warning("[KRNT] 토큰 미채집 → 공지 상세 본문 없이 제목만 반환")
                    results += [HandlerResult(
                        title=(row.get("tbbsTtl") or "").strip(),
                        posted_date=str(row.get("bltnStrtDt") or ""),
                        detail_url=_detail_page_url(row.get("tbbsSn")),
                        detail_html="",
                        body_text="",
                    ) for row in notices[:max_items] if row.get("tbbsTtl")]
                n_notice = len(results)
                results += _banner_results(banners)
                logger.info(
                    f"[KRNT] 핸들러 추출 {len(results)}건 "
                    f"(공지 {n_notice}, 배너 {len(results) - n_notice})"
                )
            finally:
                browser.close()
    except Exception as e:
        logger.warning(f"[KRNT] 홈택스 수집 실패: {e}")
        return []

    return results


register("KRNT", handle)
