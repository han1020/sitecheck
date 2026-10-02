# 리눅스 서버 배포 가이드 (웹 대시보드)

사이트 점검 수집 웹 대시보드(`serve.py`)를 리눅스 서버에 상주 실행하는 방법입니다.
경로/계정은 예시(`/opt/siteCheck`, `sitecheck`)이니 환경에 맞게 바꾸세요.

188 서버(Ubuntu 22.04)에서는 systemd 방식을 사용합니다. 웹 포트는 9095이고,
별도 저장 API는 `http://<API 서버 IP>:9092`입니다. 포트 9092를 웹 포트로 사용하지 마세요.
Git에서 코드를 받는다면 DB 연동 파일까지 포함된 배포용 커밋을 먼저 준비해야 합니다.

---

## 1. 코드 + 가상환경 준비

```bash
# 전용 계정과 동일 이름의 그룹 (이미 있다면 생략)
sudo useradd --system --create-home --user-group --home-dir /opt/siteCheck sitecheck

# 코드 배치 (git clone 또는 복사)
sudo -u sitecheck git clone <repo-url> /opt/siteCheck
cd /opt/siteCheck

# 가상환경 + 의존성
sudo -u sitecheck python3 -m venv .venv
sudo -u sitecheck .venv/bin/pip install -U pip
sudo -u sitecheck .venv/bin/pip install torch==2.13.0 torchvision==0.28.0 \
    --index-url https://download.pytorch.org/whl/cpu
sudo -u sitecheck .venv/bin/pip install -r requirements.txt
```

50 서버에서 이전하는 경우, 수집이 중복 실행되지 않도록 기존 타이머와 웹을 멈춘 뒤
최종 `config/`와 `output/`을 188 서버의 동일 경로로 복사하고 `sitecheck` 소유권을 맞춥니다.
`output/`에는 엑셀·감지 이력·스크린샷·API 연결 상태가 포함됩니다.
기존 `output/state/`는 **동일한 API DB와 항목 ID를 계속 사용할 때만** 복사합니다.
새 DB라면 이전 엑셀의 숨긴 H열 ID와 상태 파일을 그대로 사용하지 마세요.

## 2. Playwright 브라우저 설치 (★ 빠지면 '지금 수집' 에러)

서비스 파일과 **같은 경로 변수**로 설치해야 합니다.

```bash
# 브라우저 바이너리를 프로젝트 안(.playwright)에 고정 설치
sudo -u sitecheck PLAYWRIGHT_BROWSERS_PATH=/opt/siteCheck/.playwright \
    .venv/bin/playwright install chromium

# OS 시스템 라이브러리 (root 권한 필요)
sudo .venv/bin/playwright install-deps
# 위 명령이 배포판에서 안 되면, 안내되는 apt/dnf 패키지를 직접 설치
```

## 3. 저장 API 환경파일

웹과 수집 서비스가 `/etc/sitecheck/api.env`를 함께 읽습니다. 아래의 인증값은
API 담당자가 제공한 운영용 값으로 바꾸세요. Base URL에는 `http://`가 필수이고,
`/api/v1/site-check-items`는 프로그램이 붙입니다.

```bash
sudo install -d -m 0750 -o root -g sitecheck /etc/sitecheck
sudoedit /etc/sitecheck/api.env
sudo chown root:sitecheck /etc/sitecheck/api.env
sudo chmod 0640 /etc/sitecheck/api.env
```

```ini
SITECHECK_API_BASE_URL=http://<API 서버 IP>:9092
SITECHECK_API_ENV=production
SITECHECK_API_AUTH_HEADER=X-Auth-User-Email
SITECHECK_API_AUTH_VALUE=<API 담당자가 제공한 운영 인증값>
```

같은 서버의 API가 루프백 주소에서도 접속된다면 Base URL을
`http://127.0.0.1:9092`로 설정할 수 있습니다. DB 연동 전에 조회 API
(후보 목록과 상세 조회)가 운영 서버에 구현되었는지 확인하세요. 로컬 테스트 서버에서는
두 조회 경로가 HTTP 405였으므로, 이를 확인하지 않고 자동 수집을 켜면 신규 공지의
DB 동기화가 실패할 수 있습니다.

## 4. 동작 확인 (서비스 등록 전 1회 수동 실행)

```bash
sudo -u sitecheck bash -c '
  cd /opt/siteCheck
  set -a
  . /etc/sitecheck/api.env
  set +a
  .venv/bin/python -m src.notice_api
  PLAYWRIGHT_BROWSERS_PATH=/opt/siteCheck/.playwright \
    .venv/bin/python serve.py --host 0.0.0.0 --port 9095
'
# 브라우저에서 http://<서버IP>:9095 접속 후 Ctrl+C
```

`notice_api`의 빈 lookup이 성공해야 URL·인증·기본 응답이 정상입니다.
운영 API의 후보/상세 GET을 확인하기 전에는 `지금 수집`이나 타이머를 실행하지 마세요.

## 5. systemd 서비스 등록

```bash
# 서비스 파일 복사 (먼저 User/경로/포트를 환경에 맞게 편집)
sudo cp deploy/sitecheck-web.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now sitecheck-web    # 등록 + 즉시 시작 + 부팅 자동시작

# 상태 / 로그
systemctl status sitecheck-web
journalctl -u sitecheck-web -f
```

재시작/중지:
```bash
sudo systemctl restart sitecheck-web
sudo systemctl stop sitecheck-web
```

## 6. 방화벽 (허용된 접속 대역만)

```bash
# ufw (Ubuntu): <사내망-CIDR>을 실제 허용 대역으로 교체
sudo ufw allow from <사내망-CIDR> to any port 9095 proto tcp
```

Nginx나 VPN을 통해서만 접속한다면 9095를 외부에 열지 마세요.

---

## ⚠️ 보안 주의

- 이 대시보드는 **인증이 없습니다.** 접속한 누구나 `지금 수집` 실행·결과 열람이 가능합니다.
- **사내망/신뢰 네트워크에서만** 사용하세요. 공인 IP로 인터넷에 직접 노출 금지.
- 외부 접근이 필요하면 앞단에 **Nginx 리버스 프록시 + Basic Auth** 또는 **VPN**을 두세요.

### (선택) Nginx Basic Auth 예시

```nginx
server {
    listen 80;
    server_name sitecheck.example.com;
    location / {
        auth_basic "SiteCheck";
        auth_basic_user_file /etc/nginx/.htpasswd;   # htpasswd 로 생성
        proxy_pass http://127.0.0.1:9095;
    }
}
```
이 경우 `serve.py`는 `--host 127.0.0.1`로 띄워 Nginx만 접근하게 하세요.

---

## 7. 자동 주기 수집 (매주 화/금 10:00·14:00) — systemd timer

웹의 `지금 수집` 버튼을 사람이 누르지 않아도, 정해진 시각에 자동 수집되게 합니다.
`collect.py`는 웹의 '지금 수집'과 동일 파이프라인이라 결과가 **감지 목록 + 엑셀뷰** 양쪽에
바로 반영됩니다.

### 7-1. 서버 시간대부터 확인 (★ 중요)

타이머의 `10,14:00`은 **서버 로컬 시간** 기준입니다. 한국 시간으로 돌리려면:

```bash
timedatectl                              # 현재 시간대 확인
sudo timedatectl set-timezone Asia/Seoul # UTC 등으로 되어 있으면 변경
```

### 7-2. 서비스 + 타이머 등록

```bash
# (서비스 파일의 User/경로를 환경에 맞게 편집 후)
sudo cp deploy/sitecheck-collect.service /etc/systemd/system/
sudo cp deploy/sitecheck-collect.timer   /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl start sitecheck-collect.service         # API 조회 구현 확인 후 수동 수집 1회
sudo systemctl enable --now sitecheck-collect.timer   # 수집 성공 후 타이머만 enable
```

자동 시작 대상으로 등록하는 건 **`.timer`** 입니다. `.service`는 수동 검증 때만
start 하고 enable 하지 않습니다. 타이머에 `Persistent=true`가 있어 활성화 직후
놓친 실행을 보충할 수 있으므로, API 조회 구현·수동 수집 성공을 먼저 확인하세요.

### 7-3. 확인

```bash
systemctl list-timers sitecheck-collect.timer   # 다음 실행 예정 시각(NEXT) 확인
journalctl -u sitecheck-collect.service -f       # 수집 로그

```

스케줄 변경은 `sitecheck-collect.timer`의 `OnCalendar=` 한 줄만 고치고
`sudo systemctl daemon-reload && sudo systemctl restart sitecheck-collect.timer` 하면 됩니다.
예) 매일 오전 9시·오후 2시 → `OnCalendar=*-*-* 09,14:00:00`

---

## 정리: 등록되는 systemd 유닛

| 유닛 | 역할 | enable 대상 |
|------|------|-------------|
| `sitecheck-web.service`     | 웹 대시보드 상주 | ✅ `enable --now` |
| `sitecheck-collect.timer`   | 화/금 10:00·14:00 트리거 | ✅ `enable --now` |
| `sitecheck-collect.service` | 수집 1회 실행 | ❌ (타이머가 호출) |
