"""
각 사이트의 공지 페이지 DOM 구조를 분석하기 위한 디버그 스크립트.

각 enabled 사이트를:
1. Playwright로 로드
2. networkidle 대기
3. table/ul/div[class*=notice] 같은 후보를 모두 찾고 첫 행 정보 출력
4. 전체 페이지 스크린샷 + HTML 일부 저장 (output/debug/<code>/)

사용:
    .venv/bin/python -m src.debug_dom
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from playwright.async_api import async_playwright

from .config_loader import load_sites

DEBUG_DIR = Path("output/debug")

JS_DETECT = r"""
() => {
  function summarize(el) {
    const cn = (el.className && typeof el.className === 'string') ? el.className.slice(0, 60) : '';
    return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${cn ? '.' + cn.replace(/\s+/g, '.') : ''}`;
  }

  const candidates = [];

  // 1) 모든 table → tbody → tr
  document.querySelectorAll('table').forEach((t, ti) => {
    const trs = t.querySelectorAll('tbody tr, tr');
    if (trs.length < 2) return;
    // 첫 의미있는 행 추정 (헤더 제외)
    let sample = '';
    for (const tr of trs) {
      const text = tr.innerText.replace(/\s+/g, ' ').trim();
      if (text.length > 10) { sample = text.slice(0, 80); break; }
    }
    candidates.push({
      type: 'table',
      selector: `table${t.id ? '#' + t.id : ''}${t.className ? '.' + t.className.split(/\s+/).join('.') : ''} tbody tr`,
      where: summarize(t),
      rowCount: trs.length,
      sample,
    });
  });

  // 2) ul/ol 리스트 형태
  document.querySelectorAll('ul, ol').forEach((ul) => {
    const lis = ul.children;
    if (lis.length < 3) return;
    const text = (lis[0].innerText || '').replace(/\s+/g, ' ').trim();
    if (text.length < 10) return;
    candidates.push({
      type: 'list',
      selector: `${ul.tagName.toLowerCase()}${ul.id ? '#' + ul.id : ''}${ul.className ? '.' + (typeof ul.className === 'string' ? ul.className.split(/\s+/).join('.') : '') : ''} > li`,
      where: summarize(ul),
      rowCount: lis.length,
      sample: text.slice(0, 80),
    });
  });

  // 3) iframe 존재 여부
  const iframes = [...document.querySelectorAll('iframe')].map(f => ({
    src: f.src, id: f.id, name: f.name, width: f.width, height: f.height,
  }));

  return { candidates, iframes, url: location.href, title: document.title };
}
"""


async def analyze_site(p, code: str, url: str, name: str):
    out_dir = DEBUG_DIR / code
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n========== [{code}] {name} ==========")
    print(f"URL: {url}")

    browser = await p.chromium.launch(headless=True)
    ctx = await browser.new_context(
        viewport={"width": 1366, "height": 900},
        locale="ko-KR",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    page.set_default_timeout(20000)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        title = await page.title()
        cur = page.url
        print(f"loaded: {title!r}  →  {cur}")

        # 전체 페이지 스크린샷
        await page.screenshot(path=str(out_dir / "page.png"), full_page=True)

        # 페이지 HTML 일부 (헤드 + body 첫 부분)
        html = await page.content()
        (out_dir / "page.html").write_text(html, encoding="utf-8")

        # DOM 후보 분석
        info = await page.evaluate(JS_DETECT)
        print(f"document.title: {info.get('title')!r}")
        print(f"iframes: {len(info.get('iframes', []))}")
        for i, f in enumerate(info.get("iframes", [])[:5]):
            print(f"  iframe[{i}]: {f}")

        cands = info.get("candidates", [])
        print(f"후보 {len(cands)}개:")
        for c in cands[:10]:
            print(f"  [{c['type']:5}] {c['where']:50} rows={c['rowCount']:3}  sample={c['sample'][:70]}")

        # 후보 요약을 텍스트로도 저장
        (out_dir / "summary.txt").write_text(
            f"URL: {cur}\nTITLE: {title}\n\n=== iframes ===\n"
            + "\n".join(f"{i}: {f}" for i, f in enumerate(info.get("iframes", [])))
            + "\n\n=== 후보 ===\n"
            + "\n".join(
                f"[{c['type']}] {c['where']} rows={c['rowCount']} sample={c['sample']}"
                for c in cands
            ),
            encoding="utf-8",
        )

    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        await ctx.close()
        await browser.close()


async def main():
    sites = load_sites(Path("config/sites.yaml"))
    enabled = [s for s in sites if s.enabled]
    async with async_playwright() as p:
        for s in enabled:
            await analyze_site(p, s.code, s.list_url, s.name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
