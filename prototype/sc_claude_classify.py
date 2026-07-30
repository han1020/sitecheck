"""
프로토타입: SC은행(KRBK0023) 공지 1페이지를 파이썬으로 가져오고,
'사이트 점검 여부 판단 + 일시/업무/사유 추출'을 Claude API에 맡겨본다.

목적
  - 실제 토큰/비용 측정 (프롬프트 캐싱 적용)
  - 규칙기반 파이프라인 결과와 정확도 비교

사용
  source .venv/bin/activate
  export ANTHROPIC_API_KEY=sk-ant-...
  python prototype/sc_claude_classify.py --model claude-haiku-4-5
  python prototype/sc_claude_classify.py --model claude-sonnet-4-6 --limit 20

구조화 출력은 SDK 버전에 무관하게 동작하도록 'tool_choice 강제 호출' 방식 사용.
시스템 프롬프트(판단 기준)는 prompt caching 으로 재사용 → 2번째 호출부터 캐시 read.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# 프로젝트 루트 import 경로
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

from src.handlers.BK import KRBK0023  # noqa: E402

# 1M 토큰당 단가 (USD) — 입력 / 출력
PRICING = {
    "claude-haiku-4-5":  (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7":   (5.00, 25.00),
}
CACHE_WRITE_MULT = 1.25   # 캐시 쓰기 = 입력단가 × 1.25
CACHE_READ_MULT = 0.10    # 캐시 읽기 = 입력단가 × 0.10

# 활성 기관 수 / 하루 실행 횟수 (월 비용 추정용)
N_BANKS = 33
RUNS_PER_DAY = 2
DAYS = 30

SYSTEM_PROMPT = """\
너는 한국 금융기관(은행·저축은행) 홈페이지 '공지사항'을 분석하는 분류기다.
주어진 공지 1건(제목+본문)을 보고, 그 기관의 인터넷/모바일 뱅킹 등 전자금융 서비스가
'점검·작업으로 일시 중단'되는 공지인지 판단하고, 핵심 정보를 추출한다.

판단 규칙:
1) is_maintenance = true 조건: 해당 기관의 전자금융(인터넷뱅킹/모바일뱅킹/앱/ARS 등)
   서비스가 점검·시스템작업·이전 등으로 '일시 중단/지연'되는 공지.
   - 단순 약관개정, 금리변경, 상품출시, 이벤트, 채용/입찰공고, 피싱주의 안내 등은 false.
2) is_own_institution = true 조건: 그 기관 '자신'의 서비스 중단 공지.
   - 외부기관(금융결제원/행정안전부/경찰청/타행 등) 사정으로 '일부 서비스'만 영향받는
     단순 안내성 공지는, 그 기관 자체 점검이 아니면 false 로 둔다.
   - 단, 외부기관 작업이어도 그 기관의 뱅킹 전체가 중단되면 true.
3) schedule: 점검 일시를 'YYYY.MM.DD(요일) HH:MM ~ HH:MM' 형식으로. 종료가 다른 날이면
   종료에도 날짜 포함. 본문에 일시가 없으면 빈 문자열.
4) service: 중단되는 업무/서비스 (없으면 빈 문자열).
5) reason: 점검/중단 사유 (없으면 빈 문자열).
6) confidence: 0~1 사이 확신도.
정확하고 보수적으로 판단하라. 추측하지 말고 본문 근거로만.
"""

CLASSIFY_TOOL = {
    "name": "record_judgment",
    "description": "공지 1건의 점검 판단 결과를 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_maintenance": {"type": "boolean"},
            "is_own_institution": {"type": "boolean"},
            "schedule": {"type": "string"},
            "service": {"type": "string"},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": [
            "is_maintenance", "is_own_institution",
            "schedule", "service", "reason", "confidence",
        ],
    },
}


def classify(client, model, title, body):
    """공지 1건 → (판단 dict, usage)."""
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # 시스템 프롬프트 캐싱
        }],
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "record_judgment"},
        messages=[{
            "role": "user",
            "content": f"[제목]\n{title}\n\n[본문]\n{body[:6000]}",
        }],
    )
    judgment = {}
    for block in resp.content:
        if block.type == "tool_use":
            judgment = block.input
            break
    return judgment, resp.usage


def cost_of(model, usage):
    """usage → USD."""
    in_price, out_price = PRICING[model]
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        inp * in_price
        + cw * in_price * CACHE_WRITE_MULT
        + cr * in_price * CACHE_READ_MULT
        + out * out_price
    ) / 1_000_000, (inp, out, cw, cr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5", choices=list(PRICING))
    ap.add_argument("--limit", type=int, default=0, help="공지 N건만 (0=전체)")
    args = ap.parse_args()

    print(f"=== SC은행(KRBK0023) 공지 수집 ===")
    notices = KRBK0023.handle(SimpleNamespace(code="KRBK0023", name="SC은행"))
    if args.limit:
        notices = notices[: args.limit]
    print(f"핸들러 추출 {len(notices)}건\n")

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 사용

    tot_cost = 0.0
    tot_in = tot_out = tot_cw = tot_cr = 0
    hits = []
    t0 = time.time()

    for i, n in enumerate(notices, 1):
        try:
            judgment, usage = classify(client, args.model, n.title, n.body_text)
        except Exception as e:
            print(f"[{i:02d}] 호출 실패: {e}")
            continue
        c, (inp, out, cw, cr) = cost_of(args.model, usage)
        tot_cost += c
        tot_in += inp; tot_out += out; tot_cw += cw; tot_cr += cr

        is_m = judgment.get("is_maintenance")
        is_o = judgment.get("is_own_institution")
        flag = "🟡 점검" if (is_m and is_o) else ("·  비점검" if not is_m else "·  외부기관")
        print(f"[{i:02d}] {flag} (conf {judgment.get('confidence',0):.2f}) | {n.title[:42]}")
        if is_m and is_o:
            print(f"       일시: {judgment.get('schedule') or '(없음)'}")
            print(f"       업무: {judgment.get('service') or '(없음)'}")
            hits.append((n, judgment))

    elapsed = time.time() - t0

    print("\n=== Claude 판단 결과 ===")
    print(f"점검(자기기관) 판단: {len(hits)}건 / 전체 {len(notices)}건")

    print("\n=== 토큰 / 비용 (model={}) ===".format(args.model))
    print(f"입력(uncached): {tot_in:,} | 캐시쓰기: {tot_cw:,} | 캐시읽기: {tot_cr:,} | 출력: {tot_out:,}")
    print(f"이번 실행(SC 1개 기관) 비용: ${tot_cost:.4f}  | 소요 {elapsed:.1f}s")

    # 월 비용 추정 (B안: 전체 본문 다 보냄 / SC 건수를 기관당 평균으로 가정)
    per_bank = tot_cost
    monthly_full = per_bank * N_BANKS * RUNS_PER_DAY * DAYS
    print(f"\n=== 월 비용 추정 (가정: 기관당 SC와 비슷한 본문량, {N_BANKS}기관 × {RUNS_PER_DAY}회/일 × {DAYS}일) ===")
    print(f"B안(전체 본문 전송): ~${monthly_full:.2f}/월")
    print("  ※ 캐시 적중률이 올라가고(같은 시스템프롬프트 재사용),")
    print("     C안(키워드 1차 필터로 후보만 전송)이면 이보다 크게 낮아짐.")

    return hits


if __name__ == "__main__":
    main()
