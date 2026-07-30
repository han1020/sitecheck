"""
프로토타입 (Gemini 버전): SC은행(KRBK0023) 공지 1페이지를 파이썬으로 가져오고,
'사이트 점검 여부 판단 + 일시/업무/사유 추출'을 무료 LLM(Gemini)에 맡겨본다.

목적
  - 무료 티어로 정확도 확인 (비용 $0)
  - 토큰 사용량 측정 + 규칙기반 파이프라인 결과와 비교

준비 (무료)
  1) https://aistudio.google.com/apikey 에서 API 키 발급 (신용카드 불필요)
  2) export GEMINI_API_KEY=...

사용
  source .venv/bin/activate
  export GEMINI_API_KEY=...
  python prototype/sc_gemini_classify.py                       # 기본 gemini-2.5-flash
  python prototype/sc_gemini_classify.py --model gemini-2.5-flash-lite --sleep 4
  python prototype/sc_gemini_classify.py --list                # 내 키로 쓸 수 있는 모델 목록
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google import genai           # noqa: E402
from google.genai import types     # noqa: E402
from pydantic import BaseModel     # noqa: E402

from src.handlers.BK import KRBK0023  # noqa: E402

# 참고용 유료 단가(무료 티어는 $0). gemini-2.5-flash 대략치 (USD/1M)
PAID_PRICING = {
    "gemini-2.5-flash":      (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro":        (1.25, 10.0),
}
N_BANKS = 33
RUNS_PER_DAY = 2
DAYS = 30

SYSTEM_PROMPT = """\
너는 한국 금융기관(은행·저축은행) 홈페이지 '공지사항'을 분석하는 분류기다.
주어진 공지 1건(제목+본문)을 보고, 그 기관의 인터넷/모바일 뱅킹 등 전자금융 서비스가
'점검·작업으로 일시 중단'되는 공지인지 판단하고, 핵심 정보를 추출한다.

판단 규칙:
1) is_maintenance=true: 해당 기관 전자금융(인터넷뱅킹/모바일뱅킹/앱/ARS 등) 서비스가
   점검·시스템작업·이전 등으로 '일시 중단/지연'되는 공지.
   - 약관개정, 금리변경, 상품출시, 이벤트, 채용/입찰공고, 피싱주의 등은 false.
2) is_own_institution=true: 그 기관 '자신'의 서비스 중단 공지.
   - 외부기관(금융결제원/행정안전부/경찰청/타행 등) 사정으로 '일부 서비스'만 영향받는
     단순 안내성 공지는 false. 단, 그 기관 뱅킹 전체가 중단되면 true.
3) schedule: 'YYYY.MM.DD(요일) HH:MM ~ HH:MM' 형식. 종료가 다른 날이면 종료에도 날짜.
   본문에 일시 없으면 빈 문자열.
4) service: 중단 업무/서비스 (없으면 빈 문자열).
5) reason: 점검/중단 사유 (없으면 빈 문자열).
6) confidence: 0~1 확신도.
정확하고 보수적으로. 추측 말고 본문 근거로만.
"""


class Judgment(BaseModel):
    is_maintenance: bool
    is_own_institution: bool
    schedule: str
    service: str
    reason: str
    confidence: float


def _client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 미설정. https://aistudio.google.com/apikey 에서 무료 발급 후 export 하세요.")
    return genai.Client(api_key=key)


def classify(client, model, title, body):
    resp = client.models.generate_content(
        model=model,
        contents=f"[제목]\n{title}\n\n[본문]\n{body[:6000]}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=Judgment,
            temperature=0,
        ),
    )
    j = getattr(resp, "parsed", None)
    if isinstance(j, Judgment):
        judgment = j.model_dump()
    else:
        judgment = json.loads(resp.text)
    return judgment, resp.usage_metadata


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--limit", type=int, default=0, help="공지 N건만 (0=전체)")
    ap.add_argument("--sleep", type=float, default=4.0, help="호출 간 대기초 (무료 RPM 대응)")
    ap.add_argument("--list", action="store_true", help="사용 가능한 모델 목록 출력 후 종료")
    args = ap.parse_args()

    client = _client()

    if args.list:
        print("=== 사용 가능한 모델 ===")
        for m in client.models.list():
            print(" ", m.name)
        return

    print("=== SC은행(KRBK0023) 공지 수집 ===")
    notices = KRBK0023.handle(SimpleNamespace(code="KRBK0023", name="SC은행"))
    if args.limit:
        notices = notices[: args.limit]
    print(f"핸들러 추출 {len(notices)}건 | 모델 {args.model}\n")

    tot_in = tot_out = 0
    hits = []
    t0 = time.time()

    for i, n in enumerate(notices, 1):
        try:
            judgment, usage = classify(client, args.model, n.title, n.body_text)
        except Exception as e:
            print(f"[{i:02d}] 호출 실패: {e}")
            time.sleep(args.sleep)
            continue
        tot_in += getattr(usage, "prompt_token_count", 0) or 0
        tot_out += getattr(usage, "candidates_token_count", 0) or 0

        is_m = judgment.get("is_maintenance")
        is_o = judgment.get("is_own_institution")
        flag = "🟡 점검" if (is_m and is_o) else ("·  비점검" if not is_m else "·  외부기관")
        print(f"[{i:02d}] {flag} (conf {judgment.get('confidence',0):.2f}) | {n.title[:42]}")
        if is_m and is_o:
            print(f"       일시: {judgment.get('schedule') or '(없음)'}")
            print(f"       업무: {judgment.get('service') or '(없음)'}")
            hits.append((n, judgment))

        if i < len(notices):
            time.sleep(args.sleep)

    elapsed = time.time() - t0

    print("\n=== Gemini 판단 결과 ===")
    print(f"점검(자기기관) 판단: {len(hits)}건 / 전체 {len(notices)}건")

    print(f"\n=== 토큰 (model={args.model}) ===")
    print(f"입력: {tot_in:,} | 출력: {tot_out:,} | 소요 {elapsed:.1f}s")
    print("무료 티어 비용: $0 (RPM/RPD 한도 내)")

    if args.model in PAID_PRICING:
        ip, op = PAID_PRICING[args.model]
        per_bank = (tot_in * ip + tot_out * op) / 1_000_000
        monthly = per_bank * N_BANKS * RUNS_PER_DAY * DAYS
        print(f"\n=== 참고: 만약 유료였다면 ===")
        print(f"SC 1기관 ~${per_bank:.4f} → {N_BANKS}기관×{RUNS_PER_DAY}회/일×{DAYS}일 ≈ ${monthly:.2f}/월")

    return hits


if __name__ == "__main__":
    main()
