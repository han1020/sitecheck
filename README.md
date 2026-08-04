# Site Maintenance Checker

은행·공공기관 등 지정된 사이트의 공지사항을 자동 수집하여, **아직 종료되지 않은 점검(일시중단) 공지**만 골라 엑셀로 정리하고 상세 페이지의 스크린샷을 저장하는 Python 프로그램입니다.

## 동작 흐름

매 실행마다 다음 단계로 처리합니다:

1. **이전 엑셀 carryover**: 가장 최근 `output/excel/[사이트점검]_YYYYMMDD.xlsx` (구버전 `maintenance_*.xlsx`도 인식)에서 일반점검 행을 읽어, 점검 종료시각이 이미 지난 항목은 버리고 아직 안 지난 항목만 흰색으로 이어옵니다. 이때 `keywords.yaml`의 exclude 키워드를 한 번 더 적용해, 옛 엑셀에 남아있던 제외 대상(예: 제로페이/MY자산) 잔재도 정리합니다. *(macOS의 한글 NFD 파일명도 NFC 정규화 후 매칭)*
2. **사이트별 핸들러 호출**: `config/sites.yaml`의 `enabled: true` 사이트마다 등록된 핸들러로 공지사항 **1페이지**를 가져옵니다. (POST API·JSON·HTML 등 사이트마다 다른 방식)
3. **4단계 필터** 통과한 공지만 수집:
   - **키워드 매칭**: 제목에 점검 키워드 포함 + 제외 키워드 미포함 (제목/사유·업무 라벨/본문 전체 3계층 — `keywords.yaml` 참고)
   - **자기 기관 점검 검증**: 본문에 해당 기관명이 등장해야 하고(외부 기관 안내 제외), 제목의 점검 '주체'가 명부(`external_orgs`/`institution_aliases` + sites.yaml 기관명)의 다른 기관이면 제외
   - **본문에서 점검 일시 추출**
   - **종료 시각이 현재시각 이후** (이미 지난 점검 제외)

   필터에서 걸러진 공지는 스킵 사유와 함께 run JSON(`skipped`)에 기록되어 웹 대시보드 **스킵 탭**에서 확인할 수 있습니다 (잘못 제외된 공지 검증용).
4. 매칭된 공지마다:
   - 상세 페이지 HTML을 Playwright에 주입 → 전체 페이지 **PNG 스크린샷** 저장 (파일명은 `YYYY-MM-DD_<코드>_<idx>_<제목slug>.png`)
   - 본문에서 **업무** 라벨 추출 (빈값이면 기본값 자동 채움). **사유**는 본문 라벨 → 본문 서술 패턴(`X 작업/점검으로`) → 제목 정제(후행 `안내` 류·괄호 날짜 제거) 순으로 추출
5. 결과를 엑셀로 저장: 컬럼 = `구분 / 기관코드 / 기관명 / 일시 / 업무 / 사유`
   - **진짜 신규**(이번 실행에서 새로 잡은 공지 중, 이전 엑셀에 같은 `(기관, 일시)` 키가 없던 것): 배경 **노란색**
   - **이전부터 유지 중**(carryover) 또는 carryover와 같은 키로 재감지된 항목: 배경 흰색. 단, 같은 키여도 업무/사유 텍스트는 가장 최근 본문에서 새로 추출한 값으로 갱신합니다.
   - **정기점검 baseline** (`config/regular_maintenance.yaml`): 항상 포함, 흰색

> 신규 감지 0건이어도 carryover + 정기점검은 항상 출력됩니다.

## 설치

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # 최초 1회만
```

## 실행

```bash
source .venv/bin/activate          # 새 터미널마다 1회

python run.py                      # 1회 실행 (기본)
python run.py --headed             # 브라우저 창 띄움 (디버깅)
python run.py --schedule           # 주기 실행 (기본 매일 09:00, 14:00)
python run.py --schedule --times 08:00,12:00,17:00
python run.py --help               # 옵션 도움말
```

`python run.py`는 내부적으로 `src/main.py`를 실행합니다. `python -m src.main` 도 동일하게 동작합니다.

> **`run.py` vs `collect.py`**: `run.py`(→`src/main.py`)는 **엑셀+스크린샷만** 생성하고 `output/json/`을 쓰지 않아 웹 대시보드 감지목록/스킵 탭에는 반영되지 않습니다. 대시보드에서 결과를 봐야 하면 `python collect.py`(웹의 '지금 수집' 버튼과 동일 경로)를 사용하세요.

## 웹 대시보드 (`serve.py`)

브라우저에서 수집을 실행하고 결과를 확인·편집할 수 있는 내장 웹 서버입니다. **웹 프레임워크 없이** 파이썬 표준 라이브러리(`http.server`)로만 동작하므로 추가 의존성이 없습니다.

```bash
source .venv/bin/activate
python serve.py                    # http://127.0.0.1:8000
python serve.py --port 8000        # 포트 지정
python serve.py --host 0.0.0.0 --port 8000   # 사내망 등 외부 접속 허용
```

브라우저로 접속 후:

- **지금 수집** 버튼 → 1회 수집 실행. 진행률(`34/58 사이트 · 현재: 농협은행`)이 상태줄에 표시됩니다.
- **감지 목록 탭**: 실행 회차별 감지 공지 조회 (제목·일시·업무·사유·원문·스크린샷). 파싱 의심값은 ⚠로 강조 — 업무가 기본값 폴백이거나 종료 시각을 못 잡은 경우 셀에 표시되므로 원문으로 교차 확인하세요. 🗑로 항목+스크린샷 삭제.
- **검토 필요 탭**: 제목은 점검인데 본문이 비어(이미지 공지 등) 자동 판별이 불가한 항목. 캡처 확인 후 '공지추가'로 엑셀에 수동 반영.
- **스킵 탭**: 점검 키워드에 걸렸으나 제외된 공지를 사유와 함께 표시 (외부기관 안내 / 제외 키워드 / 이미 지난 점검 / 일시 파싱 실패 등). **사유·분류·기관 필터**(분류 선택 시 기관 옵션이 해당 분류로 좁혀짐)로 잘못 제외된 공지가 없는지 검증. 특히 '일시 파싱 실패'는 놓친 점검일 수 있어 주기적 확인 권장.
- **엑셀뷰 탭**: 생성된 `[사이트점검]_YYYYMMDD.xlsx`의 `점검` 시트를 표로 렌더. 노란색(신규) 강조도 그대로 보존.
  - **셀 인라인 편집**: 셀을 직접 수정하면 저장 시 원본 엑셀 파일에 반영(스타일/노란색 유지).
  - **행 삭제 / 일시·기관코드 정렬** 버튼 제공.
  - 수집이 진행 중(`status: running`)일 때는 편집/삭제가 거부됩니다(파일 충돌 방지).
- **정기점검 탭**: `config/regular_maintenance.yaml`의 baseline을 표로 표시하고 **바로 편집** 가능 (셀 편집·행 추가/삭제 → 저장 시 YAML 재작성, 재파싱 검증 포함). 엑셀에는 다음 수집부터 반영.
- **제외 키워드 탭**: `keywords.yaml`의 6개 섹션(include / exclude / exclude_title / exclude_body / external_orgs / institution_aliases)을 인라인 주석과 함께 조회 (읽기 전용).

수집 결과는 `output/json/`에 회차별 JSON으로도 저장되어 감지 목록·스킵 히스토리로 쌓입니다.

> ⚠️ 이 대시보드에는 **인증이 없습니다.** 사내망/신뢰 네트워크에서만 사용하고, 외부 노출이 필요하면 Nginx Basic Auth 또는 VPN을 앞단에 두세요. (`deploy/DEPLOY.md` 참고)

> ⚠️ **같은 날 재수집 시 주의**: 엑셀뷰에서 수동 편집한 내용은 같은 날 `[사이트점검]_YYYYMMDD.xlsx`를 다시 생성(재수집)하면 덮어쓰여 사라집니다.

## 리눅스 서버 배포 + 자동 수집

상시 운영(웹 상주 + 화/금 13:00 자동 수집)은 서버 환경에 따라 두 가지 방식 중 하나로 구성합니다.

### 방식 A — venv + systemd (최신 OS)

Ubuntu 22.04+, Rocky/Alma 9 등 Playwright Chromium이 직접 실행되는 서버용. 자세한 절차는 [`deploy/DEPLOY.md`](deploy/DEPLOY.md) 참고.

- `sitecheck-web.service` — 웹 대시보드 상주 (`serve.py`)
- `sitecheck-collect.timer` — **매주 화·금 13:00**(서버 로컬 시간) 트리거. `Persistent=true`로 서버가 꺼져 시각을 놓치면 부팅 후 1회 보충 실행.
- `sitecheck-collect.service` — 타이머가 호출하는 1회 수집(`collect.py`). 웹의 '지금 수집'과 동일 파이프라인이라 결과가 감지 목록·엑셀뷰에 바로 반영됩니다.

### 방식 B — Docker (CentOS 7 등 구형 OS)

CentOS 7처럼 glibc가 낡아 Playwright Chromium이 실행되지 않는(요구 glibc 2.28+, CentOS 7은 2.17) 서버용. 자세한 절차는 [`deploy/docker/DOCKER.md`](deploy/docker/DOCKER.md) 참고.

```bash
sudo docker compose build            # 이미지 빌드 (Chromium·torch 포함, 최초 10분+)
sudo docker compose up -d web        # 웹 대시보드 상주 (restart: unless-stopped)
sudo docker compose run --rm collect # 수집 1회 실행 (타이머가 이 명령을 호출)
```

- 스케줄 구조는 방식 A와 동일 — `deploy/docker/`의 timer/service가 화·금 13:00에 `docker compose run --rm collect`를 실행합니다.
- `config/`·`output/`·`logs/`는 호스트 디렉토리를 볼륨 마운트하므로 데이터가 컨테이너 밖에 남고, 웹에서 수정하는 `review_skips.yaml`·`regular_maintenance.yaml`도 유지됩니다.
- 컨테이너 시간대는 `Asia/Seoul`로 고정되어 있습니다 (Dockerfile `TZ`).

> 두 방식 모두 타이머의 `13:00`은 **서버 로컬 시간** 기준이므로, 한국 시간으로 돌리려면 `sudo timedatectl set-timezone Asia/Seoul` 로 시간대를 먼저 맞추세요. 스케줄 변경은 `.timer`의 `OnCalendar=` 한 줄만 수정합니다.

## 설정 파일

### `config/sites.yaml`
수집 대상 사이트 목록. `enabled: true`인 사이트만 실제 스크래핑 대상이 됩니다. 사이트 73개가 메타 정보로 등록되어 있고, 핸들러가 작성된 58개(은행 20 + 저축은행 13 + 증권 22 + 카드 3)는 활성 상태입니다.

```yaml
- code: KRBK0088
  name: 신한은행
  category: 은행
  enabled: true
  list_url: https://bizbank.shinhan.com/
```

### `config/keywords.yaml`
점검 공지로 인식할 키워드 + 제외 키워드. 제외는 매칭 범위별 3계층 + 외부기관 명부 2종으로 나뉩니다.

```yaml
include:    # 제목에 하나라도 포함되면 점검 후보
  - 점검
  - 서비스 일시중단
  - 시스템 작업
  - ...
exclude:    # 제목 또는 사유·업무 '라벨 값'에 있으면 제외
  - 점검 결과
  - 일부기관 / 타기관 / 외부기관 / 타금융기관 / 타사 / 타행
  - 오픈뱅킹 / 재해복구훈련       # 모니터링 대상 외
  - 쿠콘 / 스크래핑              # 외부 데이터 제공사 의존
  - ...
exclude_title:   # '제목에서만' 매칭 — 라벨 값에 정상 등장할 수 있는 단어용
  - CD공동망     # exclude에 두면 광주은행 업무 값에 걸려 공지가 통째로 빠짐
exclude_body:    # '본문 전체'에서 매칭 — 오탐 위험 커서 특이 문구만
  - 투자정보 콘텐츠
  - 쿠콘         # 제목·라벨엔 없고 본문 서술에만 등장하는 케이스 (예: KB저축은행)
external_orgs:   # 제목에 자기 기관명 없이 이 이름이 있으면 '남의 점검' 안내로 제외
  - 금융결제원 / 코스콤 / 대법원 / 삼성패스 / ...
institution_aliases:   # sites.yaml 표기와 다른 기관명 변형 (명부에 합산)
  - 케이뱅크 / NH농협카드 / ...
```

> exclude는 `(제목 → False)` 뿐 아니라 carryover로 들어오는 옛 엑셀 항목에도 동일하게 적용됩니다. 새 키워드를 추가하면 다음 실행부터 옛 잔재도 함께 빠집니다.
>
> 어느 계층에 넣을지 판단: 그 단어가 **정상 점검 공지의 업무/사유 라벨 값에 등장할 수 있으면 `exclude_title`**, 제목·라벨에 안 나오고 본문 서술에만 나오면 `exclude_body`, 외부 기관명이면 `external_orgs`(또는 표기 변형이면 `institution_aliases`).

### `config/regular_maintenance.yaml`
사이트별 **정기점검 baseline**. 스크래핑 결과와 무관하게 엑셀에 항상 포함됩니다. 정기점검 일정이 바뀌면 이 파일을 수정하거나 **웹 대시보드 '정기점검' 탭에서 직접 편집**하세요 (다음 수집부터 반영).

## 현재 활성 사이트 (59개 = 은행 20 + 저축은행 13 + 증권 22 + 카드 3 + 공공 1)

사이트별 커스텀 핸들러로 처리합니다.

### 은행 (20)

| 코드 | 사이트 | 코드 | 사이트 |
|------|--------|------|--------|
| KRBK0002 | 산업은행 | KRBK0034 | 광주은행 |
| KRBK0003 | 기업은행 | KRBK0035 | 제주은행 |
| KRBK0004 | 국민은행 | KRBK0037 | 전북은행 |
| KRBK0007 | 수협은행 | KRBK0039 | 경남은행 |
| KRBK0011 | 농협은행 | KRBK0045 | 새마을금고 |
| KRBK0020 | 우리은행 | KRBK0048 | 신협 |
| KRBK0023 | SC은행 | KRBK0071 | 우체국 |
| KRBK0027 | 씨티은행 | KRBK0081 | KEB하나은행 |
| KRBK0031 | 대구은행 | KRBK0088 | 신한은행 |
| KRBK0032 | 부산은행 | KRBK0089 | K뱅크 |

### 저축은행 (13)

| 코드 | 사이트 | 코드 | 사이트 |
|------|--------|------|--------|
| KRBK0101 | 저축은행중앙회(통합) | KRBK0108 | 하나저축은행 |
| KRBK0102 | 대신저축은행 | KRBK0109 | DB저축은행 |
| KRBK0103 | SBI저축은행 | KRBK0110 | NH저축은행 |
| KRBK0104 | 애큐온저축은행 | KRBK0111 | OSB저축은행 |
| KRBK0105 | 웰컴저축은행 | KRBK0112 | BNK저축은행 |
| KRBK0106 | KB저축은행 | KRBK0113 | 신한저축은행 |
| KRBK0107 | 푸른저축은행 | | |

### 증권 (22)

| 코드 | 사이트 | 코드 | 사이트 |
|------|--------|------|--------|
| KRST0209 | 유안타증권 | KRST0266 | SK증권 |
| KRST0218 | KB증권 | KRST0267 | 대신증권 |
| KRST0225 | IBK투자증권 | KRST0269 | 한화투자증권 |
| KRST0227 | 다올투자증권 | KRST0270 | 하나증권 |
| KRST0238 | 미래에셋증권 | KRST0278 | 신한투자증권 |
| KRST0240 | 삼성증권 | KRST0279 | DB금융투자 |
| KRST0243 | 한국투자증권 | KRST0280 | 유진투자증권 |
| KRST0247 | NH투자증권 | KRST0287 | 메리츠증권 |
| KRST0261 | 교보증권 | KRST0294 | 우리투자증권 |
| KRST0262 | 하이투자증권 | KRST0264 | 키움증권 |
| KRST0265 | LS증권 (舊 이베스트투자) | KRST1247 | 모바일증권 나무 |

### 카드 (3)

| 코드 | 사이트 |
|------|--------|
| KRCD0305 | 비씨카드 |
| KRCD0306 | 신한카드 |
| KRCD0311 | 롯데카드 |

> 카드 카테고리는 14개가 메타 등록돼 있으며, 3개가 활성. 나머지는 동일 패턴으로 추가 가능합니다.
>
> **비씨카드(KRCD0305)**: wisebiz 법인 사이트 공지/뉴스. 목록은 POST AJAX(`NewsInfoInqActn.corp`, JSON `contents`=HTML), 상세는 GET(`NewsInfoDetailActn.corp?seq_no=`)로 본문을 가져옵니다. 본문 표기가 'BC카드'라 자기기관 판별용 `aliases: [BC카드, BC, wisebiz, 와이즈비즈]`를 등록.
>
> **롯데카드(KRCD0311)**: 법인 사이트(`corp.lottecard.co.kr`) 공지. 목록 AJAX(`LCCSTAA_A100.lc`) 한 번으로 JSON `noticeList`를 받고, 각 항목의 `newsCn`에 본문 HTML이 통째로 들어와 상세 페이지 추가 호출이 필요 없습니다.

### 공공 (1)

| 코드 | 사이트 |
|------|--------|
| KRNT | 국세청 (홈택스) |

> **국세청 홈택스(KRNT)**: 메인 페이지의 롤링 배너 + 공지사항 리스트를 수집합니다. 웹스퀘어 SPA + eversafe 봇 탐지 때문에 `requests` 접근이 차단되어("비정상적인 요청이 감지되었습니다") **핸들러가 자체적으로 Playwright(sync)** 로 메인 페이지를 1회 렌더링합니다. 상세 흐름:
>
> 1. 로드 중 발생하는 배너 조회 응답(`wqAction.do?actionId=ATXPPCBA001R11`, JSON)을 네트워크 캡처 → `pubcPotlBnerAdmDVOList`(롤링 배너, `(메인)/(서브)` 쌍은 제목 기준 dedup) + `pubcPotlBnerAdmDVOList2`(메인 노출 공지사항: `tbbsSn`/`tbbsTtl`/`bltnStrtDt`)
> 2. 공지 각 건은 같은 페이지 세션에서 `page.evaluate(fetch)` 로 상세 조회(`ATXPPBAA001R02`, 공지사항 게시판 `blrdNo=1` + `tbbsSn`) → `intgTbbsInqrDVO.tbbsCntn`이 본문 HTML
> 3. 배너는 이미지/링크뿐이라 `body_text` 빈 값 → 제목이 점검 키워드에 걸리면 기존 OCR/검토 필요 경로로 처리
>
> 모든 wqAction 요청 본문 끝에는 `<nts<nts>nts>` 마커 + JS가 계산한 토큰이 붙어야 하며, 핸들러는 페이지가 자연 발생시킨 요청에서 토큰을 채집해 재사용합니다. 토큰은 세션 내 재사용이 되지만 **다른 화면(screenId)의 액션에는 거부**되기도 합니다(예: 게시판 화면 토큰으로 배너 조회 불가) — 그래서 배너는 fetch가 아니라 자연 발생 응답을 캡처하는 구조입니다. 본문 표기가 "홈택스"뿐이라 자기기관 판별용 `aliases: [홈택스]` 등록. 점검 공지 본문 형식(`○ 일시 : 7.1.(수) 00:00 ~ 9:00(총 9시간)`)은 기존 파서가 그대로 파싱합니다. 홈택스 로드가 무거워 이 사이트만 회당 15~30초 걸립니다.

> **KRST0225(IBK투자증권)**: 서버가 약한 SSL cipher 만 지원하므로 `requests` 어댑터에 `DEFAULT@SECLEVEL=0` SSL 컨텍스트를 적용해 우회한다.
>
> **KRST1247(모바일증권 나무)**: `/tx/` 경로 POST 는 봇 차단 페이지를 반환. 일반 경로(`/wooriwmBoard/boardList.action`) GET + Sec-Fetch-* 헤더로 우회.

> **제주은행(KRBK0035)**: 본문이 eversafe로 암호화되어, 원본 스크래퍼와 동일하게
> 외부 암호화 서비스(`enctool.goweve.io`, Bearer 토큰)에 암호화를 위임합니다.
> 이 외부 서비스/토큰이 만료되면 핸들러가 동작하지 않습니다.
>
> **신협(KRBK0048)**: 공지 본문이 텍스트가 아니라 이미지(jpg/png) 한 장입니다.
> 스크린샷은 정상 저장되지만, 본문 텍스트가 없어 점검 일시를 자동 파싱할 수 없습니다.
> 제목에 날짜가 없는 점검 공지는 수집되지 않습니다.
>
> **SBI저축은행(KRBK0103)**: 응답이 AES-256-CBC로 암호화되어 in-process로 복호화합니다(`pycryptodome`).
>
> **푸른저축은행(KRBK0107)**: 응답 필드가 EXAFE E2E(HMAC-SHA1 TOTP 키유도 + AES-128-CBC 이중 복호화)로 암호화되어 in-process로 복호화합니다.
>
> **저축은행중앙회(KRBK0101)**: 단일 사이트가 아니라 중앙회·더케이·키움YES·JT친애(EUC-KR) 4곳을
> 집계하며, 제목에 `[중앙회]`/`[더케이]`/`[키움YES]`/`[JT친애]` 태그가 붙습니다.
> 하위 기관명을 `aliases`로 등록해 자기기관 필터를 통과시킵니다.

## 결과물

```
output/
├── excel/
│   └── [사이트점검]_20260529.xlsx              # 실행 날짜별(YYYYMMDD) 결과
│                                              # 같은 날 재실행 시 덮어씀
└── screenshots/
    └── 2026-05-29/                            # 실행 날짜별 폴더
        ├── 2026-05-29_KRBK0023_00_전산_서비스_일시_중지_....png
        ├── 2026-05-29_KRBK0106_00_전자금융서비스_일시_중단_....png
        └── ...

logs/
└── run_2026-05-29_1442.log                    # 실행 시각별 로그 (시/분 포함)
```

> 옛 형식의 결과 파일(`maintenance_YYYY-MM-DD_HHMM.xlsx`)도 carryover 로드 시 함께 인식합니다.

엑셀 파일은 `점검` 시트(메인) + `실행정보` 시트(메타데이터) 2개로 구성됩니다.

## 출력 엑셀 컬럼

| 컬럼 | 설명 |
|------|------|
| 구분 | `일반점검` (감지된 공지) / `정기점검` (baseline) |
| 기관코드 | 예: `KRBK0088` |
| 기관명 | 예: `신한은행` |
| 일시 | `2026.06.07(일) 00:00 ~ 07:00` 형식 (본문에서 추출) |
| 업무 | 본문에서 **중단/제한 서비스·업무·채널·내용·영향, 작업영향** 등 라벨 추출. 라벨 앞에 붙는 `◈ ■ ▶ ◇ ☞` 등 마커는 정제 후 매칭. 표가 텍스트로 풀린 공지는 헤더/시간 셀 누수를 걸러내고(`_LABELISH_VALUE_RE`/`_NUMERICISH_VALUE_RE`) 근처 불릿 항목을 값으로 시도. 추출 실패 시 기본값: `인터넷뱅킹, 스마트폰뱅킹, 모바일웹뱅킹 중단` (대시보드에 ⚠ 표시) |
| 사유 | 우선순위: **제목 키워드 오버라이드(`REASON_OVERRIDES`) → 본문 라벨(`중단 사유` 등) → 본문 서술 패턴(`X 작업/점검으로`, `X 점검에 따라`) → 제목 정제**. 제목 정제 시 후행 `안내/안내드립니다/알림`과 괄호 안 날짜·일시(중첩 요일 괄호 포함)는 제거. |

## 프로젝트 구조

```
.
├── run.py                                # 실행 진입점 (CLI 1회/스케줄 수집)
├── serve.py                              # 웹 대시보드 서버 진입점 (--host/--port)
├── collect.py                            # 자동 수집 1회 실행 진입점 (systemd timer용)
├── requirements.txt
├── Dockerfile                            # Docker 이미지 (python3.11 + Chromium + CPU torch)
├── docker-compose.yml                    # web(상주) + collect(1회 실행) 서비스
├── deploy/                               # 리눅스 배포 파일
│   ├── DEPLOY.md                         # venv+systemd 배포 가이드 (최신 OS)
│   ├── sitecheck-web.service             # 웹 대시보드 상주 서비스
│   ├── sitecheck-collect.service         # 수집 1회 실행 (oneshot)
│   ├── sitecheck-collect.timer           # 화/금 13:00 트리거
│   └── docker/                           # Docker 배포 (CentOS 7 등 구형 OS)
│       ├── DOCKER.md                     # Docker 배포 가이드
│       ├── sitecheck-collect.service     # docker compose run --rm collect 호출
│       └── sitecheck-collect.timer       # 화/금 13:00 트리거
├── config/
│   ├── sites.yaml                        # 사이트 목록 + enabled 플래그
│   ├── keywords.yaml                     # 점검/제외 키워드
│   └── regular_maintenance.yaml          # 정기점검 baseline
├── src/
│   ├── main.py                           # CLI 엔트리, 스케줄링
│   ├── scraper.py                        # 핸들러 디스패치 + 필터 + 스크린샷
│   ├── config_loader.py                  # YAML 로딩 + 데이터 클래스
│   ├── keyword_matcher.py                # 키워드 매칭 + 자기기관 검증
│   ├── datetime_parser.py                # 점검 일시 파싱 + 포맷팅
│   ├── carryover.py                      # 이전 엑셀 → 활성 일반점검 복원
│   ├── excel_writer.py                   # 엑셀 생성 (참조 포맷)
│   ├── webserver.py                      # 웹 대시보드 (수집/감지·검토·스킵 목록/엑셀뷰/정기점검 편집/키워드뷰)
│   └── handlers/
│       ├── __init__.py                   # registry: register / get_handler
│       ├── base.py                       # HandlerResult + block_text(블록 단위 본문 추출)
│       ├── BK/                           # 은행 카테고리
│       │   ├── __init__.py               # 모든 KRBK 모듈 자동 등록
│       │   ├── KRBK0002.py               # 산업
│       │   ├── KRBK0003.py               # 기업
│       │   └── ...                       # 총 33개 (은행 20 + 저축은행 13)
│       ├── ST/                           # 증권 카테고리 (22개 등록, 22개 활성)
│       │   ├── __init__.py               # 등록된 KRST 모듈
│       │   └── KRST*.py                  # KRST0209~KRST0294, KRST1247 등 22개
│       ├── CD/                           # 카드 카테고리
│       │   ├── __init__.py               # 등록된 KRCD 모듈
│       │   ├── KRCD0305.py               # 비씨카드
│       │   ├── KRCD0306.py               # 신한카드
│       │   └── KRCD0311.py               # 롯데카드
│       └── PP/                           # 공공 카테고리
│           ├── __init__.py               # 등록된 공공 모듈
│           └── KRNT.py                   # 국세청 홈택스 (Playwright 세션 + 토큰 재사용)
└── output/
    ├── excel/
    └── screenshots/<YYYY-MM-DD>/
```

## 새 사이트(핸들러) 추가하는 법

1. `src/handlers/<카테고리>/<CODE>.py` 작성. 모듈 안에서 `handle(site_config)` 함수가 `List[HandlerResult]`를 반환하고, 모듈 끝에 `register("<CODE>", handle)`로 등록.
   ```python
   from .. import register
   from ..base import HandlerResult

   def handle(site_config):
       # HTTP 호출 → 공지 목록 + 상세 본문
       return [HandlerResult(title=..., posted_date="YYYYMMDD",
                             detail_url=..., detail_html=..., body_text=...)]

   register("KRBK9999", handle)
   ```
2. `src/handlers/<카테고리>/__init__.py`에 `from . import KRBK9999` 한 줄 추가.
3. `config/sites.yaml`에서 해당 사이트를 `enabled: true`로 변경.

## 알려진 제한

- **자기 기관 검증의 한계**: 외부 기관 점검을 안내하는 공지여도 본문에 자기 기관명이 등장하면 통과될 수 있습니다. exclude 키워드 보강으로 일부 보완 가능.
- **본문 점검 일시 파싱 실패 시 제외**: 점검 일시 형식이 특이한 공지는 자동 수집되지 않습니다. `datetime_parser.py`는 `2026.05.16.(토). 00:00 ~ 07:00`, `0시 ~ 7시`(시 단위), `26.04.24(금)`(2자리 연도), `토요일`(풀 요일), `19:10~24:00`(24시 표기), `2026.4.10 (금) 22:00 ~ 4.11 (토) 04:00`(종료 연도 생략), `7월 19일(일) 23:55 ~ 20일(월) 04:00`(종료가 일(日)만 — 월/연도 상속·월 넘김 처리), `중단일자 : 2026. 8. 9(일)` + 표 안 `00:30 ~ 08:00`(날짜·시간 분리형 폴백) 등 다양한 형식을 지원합니다. 그래도 형식이 더 특이하면 `_to_dt`/`_RANGE_PATTERNS`를 보강하세요. **파싱 실패 건은 대시보드 스킵 탭('점검 일시 파싱 실패' 필터)에서 확인할 수 있습니다.**
- **제주은행 외부 의존**: 본문 암호화(eversafe)를 외부 서비스(`enctool.goweve.io`, Bearer 토큰)에 위임합니다. 서비스 다운/토큰 만료 시 동작하지 않습니다.
- **신협 이미지 본문**: 공지 본문이 이미지(jpg/png)뿐이라 점검 일시 텍스트 파싱이 불가합니다. 스크린샷은 저장되나, 제목에 날짜가 없으면 자동 수집되지 않습니다.
- **사이트 구조 변경 시 재작업 필요**: 금융권 사이트는 URL·셀렉터·API 응답 키가 변경되면 해당 핸들러 수정이 필요합니다.

## 트러블슈팅

- `command not found: python` → 가상환경 활성화 누락 (`source .venv/bin/activate`)
- `playwright._impl._errors.Error: Executable doesn't exist` → `playwright install chromium` 미실행
- 특정 사이트만 결과 0건 → `logs/run_*.log` 확인. "본문에서 점검 일시 파싱 실패" 다발이면 정규식 보강 필요
- 엑셀이 일부 깨져 보임 → 한글 폰트가 없는 환경. macOS/Windows 기본 한글 폰트 환경에서 정상
- (Docker) 수집이 돌다가 결과 없이 끝나고 `output/json`이 빔 → 컨테이너가 도중에 죽고 `restart` 정책으로 조용히 재시작된 것. `sudo docker inspect sitecheck-web --format '{{.RestartCount}}'`가 0보다 크면 확정. `dmesg`에 `invalid opcode ... libtorch_cpu.so`가 있으면 CPU가 AVX2 미지원인 경우로, 기본값(OCR 비양자화)에서는 발생하지 않아야 하나 `SITECHECK_OCR_QUANTIZE=1`을 켰다면 끄세요.
- (Docker) 컨테이너 안에서만 외부 접속 불가 → CentOS 7에서 firewalld와 Docker iptables 충돌 시 발생. `sudo docker run --rm sitecheck:latest python -c "import requests; print(requests.get('https://example.com', timeout=10).status_code)"` 로 확인

## 최근 변경 (2026-07)

### Docker 배포 구성 + OCR 크래시 수정 (2026-07-30)

- **Docker 배포 경로 추가** (`Dockerfile`, `docker-compose.yml`, `deploy/docker/`): CentOS 7 등 glibc가 낡아 Playwright Chromium이 직접 실행되지 않는 서버용. 웹 상주 + systemd timer 수집 구조는 venv 방식과 동일하며, 타이머가 `docker compose run --rm collect`를 호출한다. easyocr가 끌어오는 torch는 CUDA 포함 기본 휠(2GB+) 대신 CPU 휠로 고정.
- **OCR 양자화 기본 비활성화** (`src/ocr.py`): easyocr 기본값(quantize=True)의 fbgemm 커널은 AVX2 필수라, 미지원 CPU에서 이미지 공지 인식 진입 시 `invalid opcode`(SIGILL)로 프로세스가 통째로 죽는다 — 예외로 잡을 수 없고, 수집 결과 저장 전에 죽어 `output/json`이 비는 형태로 나타났다. 기본을 `quantize=False`로 바꾸고 AVX2가 확실한 환경에서만 `SITECHECK_OCR_QUANTIZE=1`로 켜도록 변경.

### 국세청 홈택스 수집 추가 (2026-07-21)

- **공공 카테고리 신설 + 홈택스(KRNT) 핸들러**: 홈택스 메인의 롤링 배너 + 공지사항 리스트 수집 (`handlers/PP/KRNT.py`). 세부 방식은 위 "공공 (1)" 섹션 참고.
  - 홈택스는 eversafe 봇 탐지로 requests가 차단되므로 핸들러가 **자체 Playwright(sync) 세션**을 띄운다 (핸들러는 `asyncio.to_thread`로 별도 스레드에서 돌기 때문에 메인 async Playwright와 충돌 없음).
  - wqAction API는 요청 본문 끝에 `<nts<nts>nts>`+토큰이 필수 → 페이지가 자연 발생시킨 요청에서 토큰을 채집해 상세 조회(`ATXPPBAA001R02`)에 재사용. 배너 조회(`ATXPPCBA001R11`)는 화면(screenId) 불일치 시 토큰이 거부돼 fetch 재현 대신 자연 발생 응답을 캡처.
  - `sites.yaml`: KRNT `enabled: true`, 본문이 "홈택스"로만 표기돼 `aliases: [홈택스]` 등록.
  - 검증: 실공지 "홈택스 서비스 일시 중단 안내" 기준 키워드 매칭 → 자기기관 검증 → 일시 파싱(`○ 일시 : 7.1.(수) 00:00 ~ 9:00` → 2026-07-01 00:00~09:00, 기존 파서 무수정) 전 단계 통과 확인. 당시 종료 시각이 과거라 '이미 지난 점검' 스킵 처리된 것까지 확인 (정상 판정).

### 대시보드 개편 (2026-07-14)

- **스킵 탭 추가**: 점검 키워드에 걸렸으나 필터에서 제외된 공지를 사유(외부기관 안내 / 제외 키워드 '{키워드}' (제목|사유 라벨|업무 라벨|본문) / 이미 지난 점검 / 일시 파싱 실패 등)와 함께 run JSON `skipped`에 기록·표시. **사유·분류·기관 3중 필터**(AND 조합, 분류 선택 시 기관 옵션이 해당 분류로 좁혀지는 캐스케이드, 옵션마다 건수 표기) + 필터 초기화. 잘못 제외된 공지 검증용 — 도입 첫날 부산은행 파싱 실패 건을 이 탭에서 발견해 파서를 보강했다. 판정 사유는 `keyword_matcher.maintenance_verdict()`(기존 `is_maintenance`는 래퍼로 유지)가 제공.
- **감지 목록 의심값 ⚠ 하이라이트**: 업무가 기본값 폴백(`service_default`)이거나 종료 시각이 비면 셀에 ⚠+배경 강조 (툴팁으로 이유 표시).
- **감지 목록 제목 컬럼 추가**: 사유가 본문 우선으로 바뀌며 제목이 안 보이던 문제 보완.
- **수집 진행률**: 상태줄·버튼에 `수집 중… 34/58 사이트 · 현재: 농협은행` 표시 (`scrape_all(progress_cb=…)`).
- **정기점검 탭 + 직접 편집**: baseline을 표로 표시하고 셀 편집·행 추가/삭제 → 저장 시 `regular_maintenance.yaml` 재작성. 값은 더블쿼트 스칼라(JSON 문자열)로 기록해 콜론·따옴표 안전, 저장 전 재파싱 + `load_regular` 검증, 필수값(코드·기관명·일시) 검사.
- **키워드 탭 섹션 보강**: `exclude_title`/`external_orgs`/`institution_aliases` 섹션 추가 표시 (6개 전부).

### 파서/판정 개선 (2026-07-14)

- **사유 컬럼 — 본문 우선으로 전환**: 우선순위 `REASON_OVERRIDES`(제목 키워드) → **본문 라벨** → **본문 서술 패턴** → 제목 정제 (`excel_writer._resolve_reason_text(title, body_reason)`). 본문 서술 패턴(`scraper._prose_reason`)은 `X 작업/점검으로 (인해)` / `X 점검에 따라`에서 사유를 뽑고 선두 수식어("제공을 위한" 등)를 제거 — 예: 부산은행 `전산시스템 교체작업`, 농협 `시스템 정기점검`. 과거(2026-05) 본문 라벨 품질 문제로 제목 기준으로 바꿨었으나, 아래 표 누수 가드 도입으로 본문 우선이 안전해짐.
- **표형 공지 라벨 누수 가드**: 표가 텍스트로 풀린 본문에서 헤더 셀("내용")이 라벨로 매칭돼 다음 셀 라벨("제한일시")이 값으로 새던 문제(농협)와, 라벨 다음 줄이 시간 셀(`00:30 ~`)이던 문제(부산) 수정 — `_LABELISH_VALUE_RE`/`_NUMERICISH_VALUE_RE`로 거르고, 이 경우 **근처 8줄 안의 첫 불릿 항목**을 실제 값으로 시도 (부산 `▪ 인터넷/모바일 뱅킹 및 자동화기기 조회, 이체거래 등`).
- **일시 파싱 2종 추가**: ① 종료 일자가 일(日)만인 형식 `7월 19일(일) 23:55 ~ 20일(월) 04:00` — 월/연도를 시작에서 상속, 월 넘김 처리 (농협). ② 날짜·시간 분리(표) 형식 `중단일자 : 2026. 8. 9(일)` + `00:30 ~ 08:00` — 최후 폴백, 날짜는 오탐 방지로 연도有/한국어 형식만 (부산은행).
- **단어별 `<span>` 본문 대응 — `handlers/base.block_text()`**: 워드 붙여넣기 공지(단어마다 span, 공백은 별도 span)를 `get_text("\n")`로 뽑으면 단어별 줄바꿈이 돼 라벨 오인이 발생(SK증권 업무가 `정기`로 잡히던 문제). 인라인은 이어붙이고 블록(p/li/td)과 `<br>`에서만 줄을 나누는 헬퍼 추가, KRST0266에 적용. ⚠ `detail_html`은 `block_text` 호출 **전에** 뽑을 것(내부에서 `<br>`을 개행으로 치환해 트리를 변형).
- **업무/사유 라벨 추가**: 업무 `제한서비스`(농협)·`작업영향`(DB저축은행)·`서비스 중단 영향`(SK증권, 불릿 결합 대상), 사유 `작업내용`(DB저축은행)·`서비스 중단 사유`(SK증권).
- **외부기관 제외 2건**: `삼성패스` → `external_orgs`(DB저축은행 삼성패스 간편인증 중단 안내), `쿠콘` → `exclude_body`(KB저축은행 — 기존 `exclude`엔 있었지만 본문 서술에만 등장해 안 걸리던 케이스).
- **농협은행 정기점검 baseline 수정**: `매월 셋째 월요일` → `매월 셋째 일요일 23:55(전일) ~ 04:00 (단, 월요일이 공휴일인 경우 익영업일)` (공지 원문 표기 기준).

## 최근 변경 (2026-06)

- **외부기관/상품오픈 안내 제외 2종 추가**: `keywords.yaml` exclude에 `정부24`, `주식옵션위클리` 추가(사용자 요청). `정부24`는 행정안전부 정부24 성능점검 의존 서비스(자기 점검 아님 — 예: 제주은행 KRBK0035·애큐온저축은행 KRBK0104), `주식옵션위클리`는 NH투자증권(KRST0247) '주식옵션 위클리상품 오픈에 따른 점검'으로 점검이 아닌 상품 오픈 안내. ※ exclude는 제목뿐 아니라 사유/업무 라벨 값도 검사하므로, `주식옵션위클리`처럼 제목이 아닌 사유 라벨에만 등장해도 걸러진다.
- **업무 텍스트 — 제목 키워드 오버라이드(`SERVICE_OVERRIDES`/`_resolve_service_text`)**: 본문에 업무 라벨이 없어 기본값으로 떨어지는 특수 공지를 제목으로 식별해 업무를 지정. 예: 코스콤 인증센터 `SignKorea` 시스템 작업(업무순단) → 업무 `일부 인증시스템 중단`(전체 뱅킹 중단 X). 우선순위는 본문 라벨 추출 → 제목 오버라이드 → 기본값.
- **업무 라벨 — 특정 라벨 아래 불릿 목록(여러 줄) 결합**: `_label_value`에 `collect_bullets` 옵션 추가. 라벨만 있는 줄 다음의 연속된 불릿(`·`,`•`,`-` 등, `_is_bullet`) 항목을 모두 모아 `, `로 결합한다. 단 **결합 대상 라벨을 `_BULLET_COLLECT_LABELS`(=`지연 발생 업무`/`지연 발생 서비스`/`지연 업무`)로 한정**한다. (예: 제주은행 KRBK0035 '지연 발생 업무' 아래 `· 일부 대출 서비스 / · ATM 현금서비스 / · 한국전력 가상계좌 / · 방카슈랑스` → 전부 결합. 반면 NH저축은행 KRBK0110 '중단 업무'는 첫 줄이 요약이라 결합하지 않고 첫 줄 `인터넷뱅킹, 모바일앱, 모바일웹을 통한 모든 업무 중단`만 사용.)
  - ⚠️ **회귀 주의**: `collect_bullets`는 **출력 업무 컬럼 전용**이며, 키워드 제외 판정(`is_maintenance`)의 `service_preview`엔 쓰지 않는다(기본 `False`). 영향 서비스 불릿 목록에 우연히 든 exclude 키워드(예: NH저축은행 '중단 업무' 목록 속 `오픈뱅킹`)로 **전체 점검 공지가 통째로 잘못 제외**됐던 회귀를 막기 위함.
- **웹 대시보드 추가(`serve.py` / `src/webserver.py`)**: 브라우저에서 '지금 수집' 실행 + 감지 목록/엑셀뷰 조회. 표준 라이브러리(`http.server`)만 사용해 추가 의존성 없음. 수집 결과는 `output/json/`에 회차별로도 저장.
- **엑셀뷰 인라인 편집·행 삭제**: 엑셀뷰의 셀을 직접 수정하거나 행을 삭제하면 원본 `[사이트점검]_YYYYMMDD.xlsx`에 반영(셀 스타일/노란색 보존, `openpyxl`). 수집 진행 중(`running`)에는 파일 충돌 방지를 위해 편집/삭제 거부.
- **롯데카드(KRCD0311) 추가**: 법인 사이트 공지 핸들러. 목록 AJAX(`LCCSTAA_A100.lc`) 한 번으로 JSON `noticeList`를 받고 각 항목 `newsCn`에 본문 HTML이 포함돼 상세 추가 호출 불필요. 활성 사이트 57→58개(카드 2→3, 비씨·신한·롯데).
- **자동 수집 스케줄 + systemd 배포(`collect.py`, `deploy/`)**: 매주 화·금 13:00 자동 수집. `sitecheck-collect.timer`(`OnCalendar=Tue,Fri *-*-* 13:00:00`, `Persistent=true`) + `sitecheck-collect.service`(oneshot) + 웹 상주용 `sitecheck-web.service`. 가이드는 `deploy/DEPLOY.md`.
- **제외 키워드 3종 추가**: `keywords.yaml` exclude에 `CD공동망`, `펀드 예약신규`, `콜봇` 추가(사용자 요청). ※ `펀드 예약신규`는 제목 표기에 맞춰 **공백 포함**으로 등록. (미래에셋·신한투자 등 일반 점검 공지는 계속 수집되도록 일반 제목은 제외하지 않음.)
- **일시 파싱 — 정오(낮 12시) 인식**: 시간 접두어 정규식(`_TIME_PREFIX`)과 `_to_dt`/`extract_window`의 접두어 제거 패턴에 `정오` 추가. (예: 미래에셋증권에서 종료 `정오 12시`가 누락되던 문제 해결.)
- **일시 파싱 — '분'은 '분' 글자가 붙을 때만 인정**: `_TIME` 정규식을 `HH:MM` / `HH시MM분` / `HH시`만 허용하도록 조정. `18시` 뒤에 오는 다음 항목번호(`2.`)를 분(`02`)으로 오인해 `18:02`로 찍히던 문제(예: 신한투자증권) 해결.
- **업무 라벨 — 번호/마커 붙은 라벨 파싱**: 본문 업무/사유 라벨 추출(`_label_value`)이 `2. 중단업무 : ...`, `(2)`, `가.`, `①`, `-`, `·` 등 앞에 목록 마커가 붙은 라벨도 인식하도록 `_LIST_MARKER` prefix 추가. (예: 교보증권 KRST0261 — 기존엔 마커 때문에 추출 실패해 기본값 `인터넷뱅킹, 스마트폰뱅킹, 모바일웹뱅킹 중단`이 들어가던 문제 해결.) 업무 칸이 이 기본값과 같으면 라벨 파싱 실패를 의심할 것.
- **외부기관 작업 안내 제외 — `서울보증보험` 추가**: `keywords.yaml` exclude에 `서울보증보험` 추가. SGI서울보증보험 시스템 작업(사잇돌대출)처럼 자기 점검이 아닌 외부기관 작업 안내를 거른다. (`SC제일은행`·`금융인증서`와 동일 패턴. 예: 신한저축은행 KRBK0113.)
- **carryover에서 오늘 날짜 엑셀 제외**: `find_latest_excel`/`load_previous_general`에 `exclude_date`(YYYYMMDD) 추가, `main`이 오늘 날짜를 전달. 같은 날 재실행 시 오늘 쓴 엑셀을 carryover로 읽어 신규건이 노란색 강조를 잃던 문제를 해결 — carryover는 직전 날짜 엑셀에서만 가져온다.
- **업무/사유 선두 노이즈 정리**: 업무 추출값(`_strip_bullet`) 선두의 목록 마커(`①②③…`, `1.`, `1)`, `(1)`, `가.`)를 1개 제거(예: `① 웰컴저축은행 전체 전자금융 서비스` → `웰컴저축은행 전체 전자금융 서비스`). 사유(`_clean_reason_title`)에서 `[06/13]`처럼 숫자(날짜)가 든 대괄호 제거(`[중요]`처럼 숫자 없는 대괄호는 유지).

## 최근 변경 (2026-05)

- **증권 카테고리 22개 전체 활성화**: 1차 5개(KB·미래에셋·삼성·한국투자·키움) + 2차 13개(유안타·다올·NH·교보·하이·SK·대신·한화·하나·신한투자·DB금융·유진·메리츠) + 3차 4개 보강(IBK투자 — SSL 약 cipher 우회, 모바일나무 — `/tx/` POST → 일반 경로 GET 우회, LS증권 — td 위치 변경 대응, 우리투자 — JSON 응답 `json.resList` 경로 보정). 활성 사이트 총 55개(은행 20 + 저축은행 13 + 증권 22).
- **결과 엑셀 파일명**: `maintenance_YYYY-MM-DD_HHMM.xlsx` → **`[사이트점검]_YYYYMMDD.xlsx`**. 같은 날 재실행 시 같은 파일에 덮어쓰며, carryover는 옛 형식도 함께 인식합니다.
- **스크린샷 파일명**: 시·분 제거 → `YYYY-MM-DD_<코드>_<idx>_<제목slug>.png`.
- **사유 컬럼 정리**: 본문 사유 라벨 대신 **공지 제목**을 사용하고, `안내`/`안내드립니다`/`알림` 접미와 `(2026.06.07)`·`(6.14.(일) 00:00 ~ 02:00)` 같은 괄호 안 날짜·일시(중첩 요일 괄호 포함)를 제거. 게시일·원문 URL·스크린샷 경로 등 부가 메모도 제거.
- **업무 컬럼 강화**: 본문 라벨에 `서비스 중단 내용/업무/채널`, `중단 서비스/내용/업무` 추가. 라벨 앞 `◈ ▣ ◇ ☞ ➤` 등 마커 정제, 추출값 선두 불릿(`-`, `•`) 정리.
- **노란색 강조 정밀화**: "신규 hit"이라도 이전 엑셀에 같은 `(기관코드, 일시)`가 있던 항목은 흰색으로 표시. 진짜 새로 추가된 공지만 노란색. 같은 키여도 본문에서 새로 추출한 업무/사유로 갱신은 됩니다.
- **carryover에 exclude 키워드 적용**: 새 키워드(예: `제로페이`/`my자산`/`마이자산`/`타금융기관`)를 추가하면 다음 실행부터 옛 엑셀에 남아있던 해당 공지도 함께 빠집니다.
- **macOS 한글 파일명 NFD 호환**: 결과 엑셀의 한글 파일명이 NFD(자모 분리)로 저장돼도 carryover 탐지 정규식이 NFC로 정규화해 매칭.

## 라이선스

내부 사용 프로젝트. 외부 배포 전 각 사이트 robots.txt 및 이용약관 확인 권장.
