# SiteCheck 저장 API 연동 검증

이 저장소의 Python 연동 구현은 모의 API 테스트와 로컬 API의 기본 저장 계약 검증까지 완료했다. 로컬 API는 `http://localhost:8888`에서 제공된다. SQL DDL은 별도 API 프로젝트가 적용하며 SiteCheck는 DB에 직접 연결하지 않는다.

후보 목록 `GET /api/v1/site-check-items`와 상세 `GET /api/v1/site-check-items/{itemId}`는 2026-10-01 확인 기준 구현되어 있다(9/29에는 HTTP 405였다). 후보 조회는 `checkType`·`institutionCode`·`scheduleText` 세 파라미터가 필수이고 페이지네이션은 없다(`hasNext`는 항상 `false`, 조건에 맞는 행 전부 반환). 401/403은 본문 없이 상태 코드만 온다. 상세는 dashboard-server의 `docs/SITECHECK_API_HANDOFF.md`를 본다.

## 1. 테스트 환경 준비

로컬 서버에서 테스트할 때는 아래처럼 설정한다. 인증값은 `@gowid.com` 도메인의 시험용 주소를 실행 환경에만 둔다. 운영 인증값은 Git에 기록하지 않는다.

```bash
export SITECHECK_API_BASE_URL='http://localhost:8888'
export SITECHECK_API_ENV='local'
export SITECHECK_API_AUTH_HEADER='X-Auth-User-Email'
export SITECHECK_API_AUTH_VALUE='<local-user>@gowid.com'
```

Base URL 끝에 `/api/v1/site-check-items`를 붙이지 않는다. 웹 서버와 CLI/타이머가 동일 URL·환경 이름을 사용해야 하며, 운영 URL로 시험 데이터를 보내지 않는다.

## 2. 연결 및 계약 확인

```bash
.venv/bin/pytest -q
.venv/bin/python -m src.notice_api
.venv/bin/python -m src.notice_live_smoke
```

빈 `:lookup` 요청이 성공하면 URL·인증·기본 JSON 응답을 확인한 것이다. `notice_live_smoke`의 기본 실행은 빈 lookup, 정기점검 YAML 11건의 lookup, 후보 GET을 확인하며 DB에 쓰지 않는다. 2026-09-29 로컬 실행에서는 11건 모두 `FOUND`, 후보 GET은 HTTP 405였다. 2026-10-01 재실행에서는 11건 `FOUND`(itemId 2~12), 후보 GET 정상(소문자 기관코드·앞뒤 공백 입력도 식별 정규화로 같은 결과), 상세 GET은 `+09:00`·소수점 없는 시각으로 응답했다. 실패하면 서버 실행 상태·인증 헤더·API 버전을 확인한다.

## 3. 테스트 쓰기

먼저 독립적인 저장 API 점검을 실행한다. `--write`는 유일한 시험 공지 한 건을 생성·수정·소프트 삭제한다. **삭제 상태의 시험 행은 DB에 남는다.** `SITECHECK_API_ENV`가 `test` 또는 `local`일 때만 실행된다.

```bash
.venv/bin/python -m src.notice_live_smoke --write
```

2026-09-29 로컬 실행 결과: `INSERTED` → 같은 식별키 import `EXISTING` → 사유 PATCH `UPDATED` → 옛 사유 재전송 `REASON_PRESERVED` → `DELETED` → 조회 `DELETED`/재등록 `SUPPRESSED_DELETED`. 시험 행 ID 18은 소프트 삭제 상태다. 2026-10-01 재실행도 같은 순서로 통과했고 시험 행 ID 46(`TST4ACB5AA`)이 소프트 삭제 상태로 남았다.

전체 엑셀 동기화는 2026-10-01 시험 워크북으로 종단 간 검증을 마쳤다(5절). 운영 파일에 대해 실행하면 파일의 모든 최종 B:G 행을 API에 저장하고 기존 행은 DB 값으로 덮어쓰므로, 실행 전에 파일을 백업한다.

```bash
.venv/bin/python -m src.notice_sync --file 'output/excel/[사이트점검]_YYYYMMDD.xlsx'
```

결과 JSON의 `errors`가 빈 배열인지, `held`가 사람이 판단할 후보만 포함하는지 확인한다. `pendingOperations`가 0이 아니면 재실행 전에 로컬 상태 파일의 작업 단계와 API 현재 값을 확인한다. 파일을 다시 실행해 같은 공지가 새 DB 행으로 늘어나지 않는지 확인한다.

## 4. 웹 경로 확인

1. 테스트 환경의 `serve.py`에서 최신 엑셀을 선택해 **DB 동기화**를 실행한다. 보류 행은 기존 공지 연결·별도 신규·보류를 각각 확인한다.
2. 검토 필요의 **공지추가**에서 일시 필수 검증, 후보 선택, API 성공 후 엑셀 삽입을 확인한다.
3. 테스트 공지의 사유/업무를 수정하고 API 상세·엑셀 H열 `itemId`·다음 재수집 값을 비교한다. 담당자 수정값이 유지되어야 한다.
4. 일시를 바꾸면 새 ID가 생기고 기존 DB 행은 자동 삭제되지 않아야 한다.
5. 테스트 공지를 엑셀뷰에서 삭제하고 DB `del_dt`가 설정되는지, 다음 재수집에서 다시 등록되지 않는지 확인한다.
6. 정기점검의 `window_start/window_end = null`, KST `+09:00` 응답, 종료된 공지의 DB 보관을 확인한다.

`output/state/test/notice_state.json`과 숨긴 H열은 백업 대상이다. PATCH/DELETE 응답이 불명확한 경우 대기 작업을 자동으로 재전송하지 않는다. API에서 현 상태를 확인한 후 담당자가 정리한다.

## 5. 종단 간 검증 기록 (2026-10-01)

로컬 dashboard-server(`http://localhost:8888`, IntelliJ 디버그 실행, 운영 gcp DB 연결)에 `X-Auth-User-Email: <시험용 계정>@gowid.com`으로 접속해 `notice_sync`의 `sync_workbook / update_row / append_row / delete_row`를 시험 워크북(기관코드 `TST*`, 기관명 "SiteCheck 연동 시험", 일시는 다음 날 01:00 ~ 02:00)으로 직접 호출했다. 상태 파일은 임시 디렉터리의 `state/local/notice_state.json`을 썼다. 검증 스크립트는 저장소에 두지 않았으며 아래 순서대로 다시 만들면 된다. 19개 검사 전부 통과.

| 단계 | 검사 | 결과 |
|---|---|---|
| S1 | 새 행 동기화 → `saved=1`, 숨긴 H열에 itemId, lookup `FOUND` 같은 id | 통과 (id 47) |
| S2 | 같은 파일 재동기화(H열 id로 LINK 재검증) → 행 1개·같은 id | 통과 |
| S3 | 사유 PATCH → `UPDATED`, 상세에 반영 | 통과 |
| S4 | 옛 사유로 재수집(H열 없는 새 파일) → `REASON_PRESERVED`, 엑셀 사유가 담당자 값, 같은 id | 통과 |
| S5 | 같은 구분·기관·일시, 다른 업무 → `held` 1건, 후보에 id 47, 로컬 HOLD 기록, 저장 없음 | 통과 |
| S6 | 담당자 LINK 결정 후 동기화 → `LINKED_EXISTING`, 업무가 DB 값으로 되쓰기 | 통과 |
| S7 | 상태 파일 재로딩(프로세스 재시작) 후 같은 입력 → 자동 연결 | 통과 |
| S8 | 다른 일시로 LINK 결정 → 행 오류 `연결 대상의 일시 또는 기관이 변경되었습니다.`(409 LINK_TARGET_CHANGED), DB 미변경 | 통과 |
| S9 | 검토 '공지추가'(후보 있음) → `needs_decision`; `action=LINK`로 재요청 → 행 삽입·`LINKED_EXISTING` | 통과 |
| S10 | 일시 변경 편집 → 1차 `schedule_change` 거부, `allow_schedule_create`로 새 공지(id 48), 기존 id 47은 live 유지 | 통과 |
| S11 | 행 삭제 → `DELETED`, 파일 행 제거, 상세 `deletedAt` 설정, lookup `DELETED` | 통과 |
| S12 | 삭제된 공지 재수집 → `suppressed=1`, 행 제거, `saved=0`, 재등록 없음 | 통과 |
| S13 | 상태 파일 `pendingOperations` 비어 있음 (decisions 3건) | 통과 |

남은 시험 데이터(소프트 삭제, 타팀 조회 `del_dt IS NULL`에는 보이지 않음): itemId 46(`TST4ACB5AA`), 47·48(`TST4FA6AE4`). 물리 삭제는 DB 담당자가 `DELETE FROM GDS.TB_SITE_CHECK WHERE institution_code LIKE 'TST%';`로 수행한다.

확인된 주의점: 대상이 맞지 않는 LINK 결정이 상태 파일에 남아 있으면(S8) 매 동기화마다 그 행이 `errors`에 들어간다. 복구는 엑셀뷰 **DB 동기화** 결과의 판단 필요 행에서 보류·별도 신규·다른 연결을 다시 선택하는 것이다.

API 장애 시나리오(2026-10-01, 가짜 수집 + 임시 출력 경로로 `_collect` 경로 실행): 포트가 닫힌 경우 13행이 행마다 3회 재시도해 10초, 호스트가 응답하지 않는 경우 행당 timeout×3+백오프(기본 15초면 약 46초, 70행이면 약 54분 — 타이머 서비스 `TimeoutStartSec=900`을 넘김)가 걸렸고, 두 경우 모두 엑셀은 생성됐지만 실행이 `RuntimeError`로 끝나 run JSON이 남지 않았다. 이에 따라 같은 날 두 가지를 바꿨다. ① `sync_workbook`은 시작할 때 빈 `:lookup`으로 연결·인증을 1회 점검하고 실패하면 `errors=[{"excelRow": 0, "error": "API 연결 실패: …"}]`로 즉시 반환한다(행별 재시도 없음, 최대 timeout×3). ② `_collect`/`run_once`는 동기화 오류가 있어도 계속 진행해 run JSON을 남기고 상태를 `done`으로 두되 상태줄에 "DB 동기화 오류 n건"을 표시한다. 같은 행은 다음 수집에서 다시 전송되므로 별도 재시도 큐는 없다.

검증하지 않은 항목: 상태 파일 손상·쓰기 실패, API 성공 후 파일 실패, PATCH 결과 불명(장애 주입이 필요해 `tests/`의 FakeApi로만 확인), 운영 최신 엑셀 한 파일의 최초 적재, `serve.py` HTTP 라우트 자체(라이브러리 함수 수준으로 검증).

## 6. 운영 전환

테스트 API와 DB 검증이 끝난 뒤 운영 Base URL·인증·환경 이름(`production` 등)을 별도로 설정한다. 최초 적재는 운영 최신 엑셀 한 파일에만 수행한다. 테스트 상태 파일을 운영 상태로 복사하지 않는다. 타팀에는 `GDS.TB_SITE_CHECK`의 읽기 전용 접근과 `del_dt IS NULL`, KST 시각 해석을 전달한다.
