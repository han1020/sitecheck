from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from .carryover import load_previous_general
from .config_loader import load_keywords, load_regular, load_sites
from .excel_writer import write_excel
from .scraper import make_run_id, scrape_all, strip_already_collected

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
SCREENSHOT_DIR = OUTPUT_DIR / "screenshots"
EXCEL_DIR = OUTPUT_DIR / "excel"
LOG_DIR = PROJECT_ROOT / "logs"


def setup_logging(run_id: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"run_{run_id}.log"

    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


async def run_once(headless: bool = True) -> Path:
    run_id = make_run_id()
    setup_logging(run_id)
    log = logging.getLogger("main")

    sites = load_sites(CONFIG_DIR / "sites.yaml")
    keywords = load_keywords(CONFIG_DIR / "keywords.yaml")
    regular = load_regular(CONFIG_DIR / "regular_maintenance.yaml")
    log.info(
        f"실행 ID: {run_id} | 사이트 {len(sites)}개 (enabled: "
        f"{sum(1 for s in sites if s.enabled)}) | 정기점검 baseline {len(regular)}건"
    )

    # 이전 엑셀에서 일반점검 carryover (종료 지난 건은 제외, 유효 건은 흰색으로 유지)
    # 같은 날 재실행 시 오늘 쓴 엑셀을 carryover로 읽으면 신규건이 강조를 잃으므로,
    # 오늘 날짜(YYYYMMDD) 엑셀은 carryover 후보에서 제외한다.
    today_compact = run_id.split("_", 1)[0].replace("-", "")
    carried = load_previous_general(EXCEL_DIR, exclude_date=today_compact)
    # 이후 exclude 키워드가 추가된 경우(예: 제로페이/MY자산), 옛 엑셀에 남아있던
    # 해당 공지도 함께 제외한다. 사유(reason) 텍스트가 원래 제목을 포함하므로
    # 키워드 매칭에 사용한다.
    from .keyword_matcher import is_maintenance
    before = len(carried)
    carried = [c for c in carried if is_maintenance(c.reason, keywords)]
    if len(carried) != before:
        log.info(f"carryover: exclude 키워드 적용으로 {before - len(carried)}건 추가 제거")

    # 실행 날짜별 스크린샷 디렉토리 (YYYY-MM-DD 부분만 사용)
    run_date = run_id.split("_", 1)[0]
    shot_dir = SCREENSHOT_DIR / run_date

    hits = await scrape_all(
        sites=sites,
        keywords=keywords,
        screenshot_root=shot_dir,
        run_id=run_id,
        headless=headless,
    )

    # 정기점검 baseline과 일시가 일치하는 감지 건 표시 (엑셀 신규에선 제외)
    from .datetime_parser import extract_window
    from .regular_match import find_regular_match, mark_regular_hits
    mark_regular_hits(hits, regular)
    carried = [
        c for c in carried
        if not (
            (w := extract_window(c.schedule) if c.schedule else None)
            and find_regular_match(c.code, w.start, w.end, regular)
        )
    ]

    # 결과 파일명: [사이트점검]_YYYYMMDD.xlsx
    date_compact = run_date.replace("-", "")
    out_path = EXCEL_DIR / f"[사이트점검]_{date_compact}.xlsx"
    # 엑셀은 전체 hits(carryover 재감지 포함)로 작성 — 텍스트 갱신을 위해
    write_excel(hits, regular, out_path, run_id, carried=carried)

    # 검토 필요 탭에서 스킵 처리한 공지는 다시 올리지 않음
    from .review_skips import filter_review_hits, load_review_skips
    hits = filter_review_hits(hits, load_review_skips(CONFIG_DIR / "review_skips.yaml"))

    # 이미 수집된(carryover) 재감지 항목은 감지목록에서 제외 + 스샷 삭제
    hits = strip_already_collected(hits, carried)
    matched = [h for h in hits if h.title and not h.needs_review]
    review = [h for h in hits if h.needs_review]
    errors = [h for h in hits if h.error]
    log.info(
        f"신규(일반)점검 감지: {len(matched)}건 | "
        f"이전 carryover: {len(carried)}건 | 검토 필요: {len(review)}건 | "
        f"에러 사이트: {len(errors)}곳"
    )
    return out_path


def schedule_loop(times: list[str], headless: bool = True) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone="Asia/Seoul")
    for t in times:
        hh, mm = t.split(":")
        sched.add_job(
            lambda: asyncio.run(run_once(headless=headless)),
            CronTrigger(hour=int(hh), minute=int(mm)),
            name=f"daily_{t}",
        )
        print(f"[scheduler] 등록됨: 매일 {t}")
    print("[scheduler] 실행 중 (Ctrl+C 종료)")
    sched.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="사이트 점검 공지 수집기")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="스케줄 모드로 상주 실행 (--times 로 시각 지정, 기본 09:00,14:00)",
    )
    parser.add_argument(
        "--times",
        default="09:00,14:00",
        help="스케줄 실행 시각 (콤마 구분, HH:MM 형식)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="브라우저 창을 띄워서 실행 (디버깅용)",
    )
    args = parser.parse_args()

    if args.schedule:
        times = [t.strip() for t in args.times.split(",") if t.strip()]
        schedule_loop(times, headless=not args.headed)
    else:
        out_path = asyncio.run(run_once(headless=not args.headed))
        print(f"\n완료. 결과 파일: {out_path}")


if __name__ == "__main__":
    main()
