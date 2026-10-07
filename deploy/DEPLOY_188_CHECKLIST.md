# 188 서버 배포 체크리스트 — SiteCheck + 저장 API 연동

작성 2026-10-07. `deploy/DEPLOY.md`의 절차를 **실제로 손으로 하는 순서**대로 풀어 쓴 것이다.
설치 경로는 `/root/achee7059/siteCheck`, 서비스는 **root로 실행**한다(`/root` 아래라 별도 계정이 접근할 수 없음).
각 단계 끝의 "확인"이 통과해야 다음으로 넘어간다. `<API 서버 IP>`·`<서비스 계정>`·`<사내망-CIDR>`·`<50서버>`는
서버에서 실제 값으로 바꿔 넣는다(이 파일은 공개 저장소에 올라가므로 실제 값은 적지 않는다).

아래 명령은 모두 188 서버에서 root 셸(`sudo -i`) 기준이다.

## 0. 전제 — 완료 (2026-10-07 확인)

운영 저장 API(`http://<API 서버 IP>:9092`)에 SiteCheck 엔드포인트가 배포되어 있음을 읽기 전용으로 확인했다.

| 확인 | 결과 |
|---|---|
| `/api/health` | 200 `UP` |
| 헤더 없음 / 타 도메인 / `@gowid.com` | 401 / 403 / 200 |
| 빈 `:lookup` | `{"data":{"results":[]}}` |
| 후보 GET (파라미터 누락 / 정상) | 422 `VALIDATION_ERROR` / 200, `hasNext=false` |
| 상세 GET (있음 / 없음) | 200, 시각 `+09:00` 소수점 없음 / 404 `ITEM_NOT_FOUND` |
| 정기점검 YAML 11건 lookup | 전부 `FOUND` (itemId 2~12) |
| 시험 행 itemId 46~49 | 404 — 물리 삭제 완료 |

준비물: 188 서버 root 셸, 50 서버 접근, GitHub 접근(HTTPS 또는 SSH 키), SiteCheck 서비스 계정 이메일(`@gowid.com`), 대시보드 접속을 허용할 사내망 CIDR.

## 1. 폴더·코드·가상환경 (Ubuntu 22.04, root)

```bash
sudo -i
mkdir -p /root/achee7059
git clone https://github.com/han1020/sitecheck.git /root/achee7059/siteCheck
cd /root/achee7059/siteCheck

# 가상환경 + 의존성 (torch는 CPU 휠을 먼저 — easyocr가 끌어오는 CUDA 휠 방지)
apt-get install -y python3-venv                 # 없을 때만
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt

# Playwright 브라우저: 프로젝트 안에 고정 (서비스 파일의 PLAYWRIGHT_BROWSERS_PATH 와 같은 경로)
PLAYWRIGHT_BROWSERS_PATH=/root/achee7059/siteCheck/.playwright .venv/bin/playwright install chromium
.venv/bin/playwright install-deps
```

- [ ] 확인: `.venv/bin/python -c "import openpyxl, requests, playwright, yaml; print('ok')"` → `ok`
- [ ] 확인: `ls /root/achee7059/siteCheck/.playwright` 에 `chromium-*` 디렉터리
- [ ] 확인: `git log --oneline -1` 이 `8810ab9` 이상(저장 API 연동 커밋 포함)

## 2. 50 서버 → 188 서버 데이터 이전

50에서 먼저 수집·웹을 멈춰 두 서버가 동시에 수집하지 않게 한다.

```bash
# [50 서버]
sudo systemctl disable --now sitecheck-collect.timer
sudo systemctl stop sitecheck-web

# [188 서버, root] 50의 config/ 와 output/ 복사
rsync -a --info=progress2 <50서버>:/opt/siteCheck/config/ /root/achee7059/siteCheck/config/
rsync -a --info=progress2 --exclude 'state/' <50서버>:/opt/siteCheck/output/ /root/achee7059/siteCheck/output/
chown -R root:root /root/achee7059/siteCheck/config /root/achee7059/siteCheck/output
```

(50의 설치 경로가 다르면 `/opt/siteCheck` 부분을 바꾼다.)
`output/state/`는 **같은 API DB와 같은 itemId를 계속 쓸 때만** 복사한다. 50에서 API 연동을 쓴 적이 없다면 복사할 것이 없다.

- [ ] 확인: `ls /root/achee7059/siteCheck/output/excel | tail -3` 에 최신 `[사이트점검]_YYYYMMDD.xlsx`
- [ ] 확인: `ls /root/achee7059/siteCheck/config` 에 `sites.yaml keywords.yaml regular_maintenance.yaml review_skips.yaml matched_deletes.yaml`
- [ ] 확인: 50 서버에서 `systemctl list-timers sitecheck-collect.timer` 가 비어 있음

## 3. 저장 API 환경파일

```bash
install -d -m 0750 /etc/sitecheck
cat > /etc/sitecheck/api.env <<'EOF'
SITECHECK_API_BASE_URL=http://<API 서버 IP>:9092
SITECHECK_API_ENV=production
SITECHECK_API_AUTH_HEADER=X-Auth-User-Email
SITECHECK_API_AUTH_VALUE=<서비스 계정>@gowid.com
EOF
chmod 0600 /etc/sitecheck/api.env
```

주의: Base URL에 `http://`가 없으면 프로그램이 `ValueError`로 멈춘다. 끝에 `/api/v1/site-check-items`는 붙이지 않는다.
`SITECHECK_API_ENV=production`이면 쓰기 스모크(`--write`)가 거부되므로 운영에서 시험 행이 생기지 않는다.

- [ ] 확인: `cat /etc/sitecheck/api.env` 네 줄, 자리표시자(`<…>`)가 남아 있지 않음

## 4. 연결 확인 (DB에 쓰지 않음)

```bash
cd /root/achee7059/siteCheck
set -a; . /etc/sitecheck/api.env; set +a
.venv/bin/python -m src.notice_api
.venv/bin/python -m src.notice_live_smoke
```

기대 출력:

```
API connectivity and empty lookup: OK
Connection / empty lookup: OK
Regular YAML lookup (11 rows): {'FOUND': 11}
Candidate GET: OK (1 candidates)
```

- [ ] 확인: 위 네 줄. `401/403`이면 헤더 값(`@gowid.com`), `404`면 Base URL 경로, 연결 오류면 188→API 서버 9092 방화벽

## 5. systemd 유닛 작성 (root 실행, 경로 치환) + 웹 상주 + 방화벽

저장소의 유닛 파일은 `/opt/siteCheck`·`User=sitecheck` 기준이므로 복사하면서 바꾼다.

```bash
cd /root/achee7059/siteCheck
for u in sitecheck-web.service sitecheck-collect.service sitecheck-collect.timer; do
  sed -e '/^User=/d' -e '/^Group=/d' \
      -e 's#/opt/siteCheck#/root/achee7059/siteCheck#g' \
      deploy/$u > /etc/systemd/system/$u
done
grep -n -E 'User=|WorkingDirectory|ExecStart|PLAYWRIGHT|EnvironmentFile|OnCalendar' /etc/systemd/system/sitecheck-*
systemd-analyze verify /etc/systemd/system/sitecheck-web.service /etc/systemd/system/sitecheck-collect.service /etc/systemd/system/sitecheck-collect.timer
systemctl daemon-reload

systemctl enable --now sitecheck-web          # 0.0.0.0:9095, /etc/sitecheck/api.env 자동 로드
systemctl status sitecheck-web --no-pager | head -5

ufw allow from <사내망-CIDR> to any port 9095 proto tcp
```

- [ ] 확인: `grep` 결과에 `User=` 줄이 없고 경로가 전부 `/root/achee7059/siteCheck`
- [ ] 확인: 사내망 브라우저에서 `http://<188 서버 IP>:9095` 접속, 엑셀뷰 탭에서 최신 파일이 열림
- [ ] 확인: `journalctl -u sitecheck-web -n 20` 에 `웹 대시보드 실행: http://0.0.0.0:9095`
- [ ] 아직 **지금 수집 / DB 동기화 버튼은 누르지 않는다** (6번에서 순서대로)

## 6. 최초 적재 — 운영 최신 엑셀 한 파일

```bash
cd /root/achee7059/siteCheck
LATEST=$(ls -1 output/excel/*.xlsx | sort | tail -1); echo "$LATEST"
cp "$LATEST" "output/backup_before_sync_$(date +%Y%m%d).xlsx"

set -a; . /etc/sitecheck/api.env; set +a
.venv/bin/python -m src.notice_sync             # 최신 파일 자동 선택. 특정 파일: --file "/root/achee7059/siteCheck/output/excel/[사이트점검]_YYYYMMDD.xlsx"
```

결과 JSON 읽는 법:

| 키 | 기대 | 다르면 |
|---|---|---|
| `errors` | `[]` | `excelRow 0` = API 연결 실패 → 4번부터 다시. 특정 행 = 값 검증 실패(길이·구분·시각) → 엑셀뷰에서 그 행 수정 후 재실행 |
| `held` | 사람이 판단할 행만 | 웹 엑셀뷰 → **DB 동기화** 버튼 → 판단 필요 패널에서 연결 / 별도 신규 등록 / 보류 선택 → 적용 |
| `saved` | DB와 맞춰진 행 수 (신규+기존+연결 합계) | 정기점검 11행은 이미 DB에 있어 "기존"으로 셈 |
| `suppressed` | 0 | 삭제된 공지가 엑셀에 남아 있던 행 수(자동 제거됨) |
| `pendingOperations` | 0 | 0이 아니면 `output/state/production/notice_state.json` 확인 후 담당자 정리 |

- [ ] 확인: `errors` 비어 있음, `held` 처리 완료
- [ ] 확인: 같은 명령을 한 번 더 실행해도 DB에 새 행이 늘지 않음(`saved`만 같은 수로 반복)
- [ ] 확인: 엑셀뷰에서 행을 하나 골라 사유를 고쳐 저장 → "저장 완료" (PATCH 성공)

## 7. 자동 수집 타이머 전환

```bash
timedatectl | grep "Time zone"                  # Asia/Seoul 이 아니면:
timedatectl set-timezone Asia/Seoul

systemctl start sitecheck-collect.service       # 수동 1회 (수 분 소요)
journalctl -u sitecheck-collect.service -n 40 --no-pager

systemctl enable --now sitecheck-collect.timer  # 화·금 10:00·14:00
systemctl list-timers sitecheck-collect.timer --no-pager
```

- [ ] 확인: 수동 1회 로그 끝에 `수집 완료: … | 감지 n건 | 검토 필요 n건` 과 `점검 API 동기화: {...}`, `DB 동기화 오류` 없음
- [ ] 확인: 대시보드 상태줄에 `DB 저장 n · 판단 필요 n · DB 오류 0`
- [ ] 확인: `list-timers` 의 NEXT 가 다음 화/금 10:00 또는 14:00(KST)

## 8. 운영 전환 뒤 첫 주에 볼 것

- [ ] 첫 자동 수집 후 `journalctl -u sitecheck-collect.service` 에 `DB 동기화 오류` 가 없는지. 있으면 상태줄의 건수와 함께 엑셀뷰 **DB 동기화** 로 원인 행 확인
- [ ] `/root/achee7059/siteCheck/output/state/production/notice_state.json` 을 백업 대상에 포함(수동 연결 결정·미완료 PATCH/DELETE 기록). `output/excel/` 의 숨긴 H열(itemId)도 같은 이유로 보존
- [ ] API 팀에 서비스 계정 주소를 회신하고, 타팀에 `GDS.TB_SITE_CHECK` 읽기 전용 접근·`del_dt IS NULL`·KST 해석을 전달
- [ ] 알림: journal에 `DB 동기화 오류` 문자열이 찍히면 알리는 규칙 추가(예: `journalctl -u sitecheck-collect.service --since -1d | grep "DB 동기화 오류"`)

## 코드 업데이트 시

```bash
cd /root/achee7059/siteCheck && git pull
.venv/bin/pip install -r requirements.txt       # requirements 가 바뀐 경우만
systemctl restart sitecheck-web                 # 타이머는 다음 실행부터 새 코드 사용
```

## 롤백 (엑셀 전용으로 되돌리기)

```bash
systemctl disable --now sitecheck-collect.timer
mv /etc/sitecheck/api.env /etc/sitecheck/api.env.off     # 파일이 없으면 엑셀 전용으로 동작
systemctl restart sitecheck-web
systemctl enable --now sitecheck-collect.timer
```

DB에 이미 저장된 공지는 그대로 남는다(삭제하지 않음). 다시 켜려면 파일명을 되돌리고 재시작한다.

## 자주 만나는 메시지

| 메시지 | 뜻 | 조치 |
|---|---|---|
| `SITECHECK_API_BASE_URL must be an HTTP(S) URL without credentials` | Base URL에 `http://` 누락 | `api.env` 수정 후 서비스 재시작 |
| `SITECHECK_API_ENV is required …` | 환경 이름 누락/허용되지 않는 문자 | `production` 으로 설정 |
| 상태줄 `DB 동기화 오류 1건`, errors의 `excelRow: 0` | API에 연결 못 함(사전 점검 실패). 엑셀·이력은 정상 저장됨 | 4번 재확인. 다음 수집이 같은 행을 다시 보냄 |
| 엑셀뷰 저장 시 `DB 항목 ID가 없습니다. 먼저 해당 엑셀을 API와 동기화하세요.` | 그 파일을 아직 동기화하지 않음 | **DB 동기화** 버튼 먼저 |
| 엑셀뷰 저장 시 `일시가 달라지면 새 공지로 등록합니다` 확인창 | 일시 변경은 새 공지 생성 | 수락하면 새 ID, 기존 공지는 유지(삭제는 별도) |
| 공지추가 팝업에 "같은 기관·일시의 기존 공지" 선택 상자 | 같은 구분·기관·일시의 다른 공지가 DB에 있음 | 기존 공지 연결 또는 별도 신규 등록 선택 |
| `삭제된 공지는 다시 등록할 수 없습니다.` | 소프트 삭제된 식별값 재등록 시도 | 복원은 DB 담당자가 `del_dt` 해제 후 재동기화 |
| `지금 수집` 에서 Chromium 실행 오류 | 브라우저가 서비스의 `PLAYWRIGHT_BROWSERS_PATH` 경로에 없음 | 1번의 `playwright install chromium` 을 같은 경로 변수로 다시 실행 |
| `TimeoutStartSec` 로 collect 서비스가 중단 | 수집 자체가 15분 초과(사이트 응답 지연 등) | journal에서 어느 사이트에서 멈췄는지 확인 |
