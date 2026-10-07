# SiteCheck 저장 API 구현 명세
최종본 1.3 · 2026-09-28 · 별도 API 프로젝트 전달용

## 1. 범위와 전달 정보

이 문서가 이전 0.x 명세를 대체한다. 184는 서버 구분용 표현이며 실제 IP가 아니다. DBMS는 MySQL 8.0 이상이다. 하나의 업무 테이블만 저장하고 타팀은 해당 테이블을 읽기 전용으로 조회한다. 186 복제, VIEW, 서버 간 전송 outbox는 이번 범위에 없다.

**DB 주소·포트·스키마·계정·비밀번호·접속 조건은 사용자가 API 구현 프로젝트에 직접 전달할 예정이다.** 이 저장소나 문서에 비밀값을 기록하지 않는다. API 구현 프로젝트는 제공받은 정보를 비밀 설정으로 관리하고 테스트·운영 API Base URL, 인증 방법, 배포 버전과 접속 조건을 사용자에게 회신한다. SiteCheck에는 DB 자격증명이 아니라 API 접속 정보만 전달한다.

이 저장소의 SiteCheck 측 API 클라이언트와 엑셀 연동은 구현했다. 별도 프로젝트의 실제 DB 생성·API 구현·배포 상태는 이 저장소에서 확인하지 못했고, 실제 서버 연결 테스트도 아직 수행하지 않았다. 세부 역할은 [SiteCheck 작업 명세](sitecheck-implementation-handoff.md), 전체 결정은 보고서를 함께 참조한다.

## 2. 확정 정책

| 항목 | 최종 정책 |
|---|---|
| 저장 대상 | 업무 6개 값 + 시작·종료 시각 + 필요한 관리 정보 |
| 보존 | 최초 적재는 최신 엑셀 한 파일만. 이후 저장된 공지는 종료되어도 보관 |
| 동일 공지 | 구분·기관코드·기관명·일시 원문·업무를 대소문자 통일 후 비교해 같으면 동일. 사유는 제외 |
| 일시 변경 | 대소문자 통일 후에도 일시 원문이 다르면 신규 등록. 기존 공지는 자동 삭제하지 않으며 담당자가 판단 |
| 자동 저장 | 기존 공지의 업무 값·시작·종료 시각을 덮어쓰지 않음 |
| 명시적 수정 | 마지막 DB 반영 요청 우선. version·수정 이력·작업자 이력 없음 |
| 삭제 | 엑셀 행 삭제와 연결되는 소프트 삭제. 같은 공지의 자동 재등록 금지 |
| 복원 | API 없음. 권한 있는 담당자가 DB의 del_dt를 NULL로 변경하고 up_dt를 갱신 |
| 일시 입력 | scheduleText 필수. windowStart/windowEnd는 각각 NULL 가능 |
| 관측 시각 | last_seen_at·observedAt 제거 |
| 모호한 공지 | SiteCheck가 후보를 비교해 기존 연결·신규 등록·보류를 선택 |
| 수동 연결 기억 | SiteCheck 로컬 상태에 입력 식별 값 → itemId 연결을 보관. API는 매번 대상 재검증 |

수동 연결은 기존 공지의 값을 사용하는 동작이며 수정이 아니다. 동일 구분·기관코드·식별 정규화 일시 안에서만 허용한다. 다른 일시의 공지는 기존 공지로 연결하지 않는다. 원문 URL/게시물 ID가 같아도 일시가 다르면 신규다.

## 3. DB 테이블

13개 컬럼: 업무 6 + 시각 2 + item_id/dedup_hash/crt_dt/up_dt/del_dt 5.
대상 테이블은 GDS.TB_SITE_CHECK다. 컬럼명은 기존 snake_case를 유지한다. 구현 시 스키마와 테이블명의 대소문자를 그대로 사용한다.
DB 제약은 PK·UNIQUE를 담당하고, 필수 문자열·구분·기간 순서는 API에서 검증한다. NOT NULL은 빈 문자열을 막지 않는다.

~~~sql
-- SiteCheck final design: one MySQL 8.0+ business table on the primary server.
-- Different schedule_text identities are separate notices; reason is not an identity field.
-- Explicit edits are last-applied-write-wins. Manual links live in SiteCheck state files.
-- The GDS schema must exist before execution. All DATETIME values are KST (+09:00) wall-clock values.
-- This session setting applies only to this connection; the API must set +09:00 on every DB connection.
-- The API validates nonempty values, time order and identity; UNIQUE protects duplicate writes.
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;
SET SESSION time_zone = '+09:00';

CREATE TABLE `GDS`.`TB_SITE_CHECK` (
                                 `item_id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '점검 공지 고유번호',
                                 `check_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '점검 구분\n 일반점검 또는 정기점검',
                                 `institution_code` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '대상 기관 코드',
                                 `institution_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '대상 기관명',
                                 `schedule_text` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '점검 일시 원문\n 실제 일정 또는 정기점검 일정',
                                 `service_text` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '점검 대상 업무',
                                 `reason_text` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '점검 사유',
                                 `window_start` datetime DEFAULT NULL COMMENT '점검 시작 일시\n 미확인 또는 정기점검 일정은 NULL',
                                 `window_end` datetime DEFAULT NULL COMMENT '점검 종료 일시\n 미확인 또는 정기점검 일정은 NULL',
                                 `dedup_hash` char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '공지 중복 식별값\n 구분 및 기관코드 및 기관명 및 일시 및 업무의 구분값 삭제 후에도 유지',
                                 `crt_dt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '최초 등록 일시',
                                 `up_dt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '최초 변경 일시',
                                 `del_dt` datetime DEFAULT NULL COMMENT '삭제 일시\n NULL이면 정상 공지',
                                 PRIMARY KEY (`item_id`),
                                 UNIQUE KEY `uq_site_check_dedup_hash` (`dedup_hash`),
                                 KEY `ix_site_check_live_id` (`del_dt`,`item_id`),
                                 KEY `ix_site_check_type_end_id` (`check_type`,`del_dt`,`window_end`,`item_id`),
                                 KEY `ix_site_check_type_org_id` (`check_type`,`institution_code`,`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='기관별 점검 공지';
~~~

별도 실행 파일: [site_check_item.mysql.sql](sql/site_check_item.mysql.sql).
위 DDL의 `up_dt` COMMENT는 현재 '최초 변경 일시'이지만, API 동작상 생성 시 기본값이 설정되고 이후 명시적 수정·삭제·복원 때마다 마지막 변경 시각으로 갱신한다. 실제 의미에 맞는 COMMENT 문구는 테이블 적용 전에 확정해야 한다.
이미 테이블을 만들었다면 CREATE를 다시 실행하거나 테이블을 삭제하지 말고 SHOW CREATE TABLE/SHOW INDEX로 실제 구조를 비교해 마이그레이션한다. 기존 다섯 시각 컬럼이 DATETIME(6)이면 소수 초 데이터의 존재와 처리 방침을 먼저 확인한 뒤 DATETIME(0)으로 변경한다. MySQL에 변환을 맡기면 소수 초가 반올림될 수 있다. 관리 시각의 실제 컬럼명도 crt_dt/up_dt/del_dt와 비교한다. 이전 문서의 version·last_seen_at 제거, window_start 추가, 인덱스와 COMMENT 변경 여부도 확인한다. DB 직접 수정을 통한 복원은 del_dt만 해제하고 식별 값·해시를 바꾸지 않는다. 업무 값을 직접 변경해야 한다면 API의 정규화·해시 갱신도 함께 따라야 하므로 일반 담당자의 업무 수정은 API를 사용한다.

테이블과 일반 문자열 컬럼은 utf8mb4_unicode_ci를 사용한다. dedup_hash의 ascii_bin은 유지해 64자리 해시의 정확한 일치와 UNIQUE를 적용한다. 이미 데이터가 있다면 컬럼 collation과 기존 해시를 함께 이관해야 한다. 대소문자만 다른 기존 행이 한 키로 합쳐질 수 있으므로 삭제 행을 포함해 충돌을 먼저 조사하고 담당자 판단 후 이관한다. 테이블 기본 collation만 바꾸거나 기존 해시를 그대로 두면 신규 중복 규칙과 일치하지 않는다.

### 시간대 계약

window_start/window_end/crt_dt/up_dt/del_dt는 모두 KST(+09:00) 현지 시각을 초 단위 DATETIME(= DATETIME(0))에 저장한다. DATETIME에는 시간대 정보가 들어 있지 않으므로 API와 타팀 조회자가 이 약속을 공유해야 한다. 서버 운영체제의 시간대가 KST여도 접속 세션이나 API 드라이버가 다르게 설정될 수 있다. DDL의 SET SESSION은 DDL을 실행한 연결에만 적용되고 이후 API 연결에는 적용되지 않는다.

- API 커넥션 풀의 모든 연결에서 SET SESSION time_zone = '+09:00'을 실행하거나 동등한 연결 설정을 적용한다. crt_dt/up_dt의 DEFAULT CURRENT_TIMESTAMP, 삭제·수정·복원 때 사용하는 NOW()가 KST가 되도록 확인한다. API 코드가 현재 시각을 직접 바인딩할 때도 초 단위 KST 값으로 맞춘다.
- offset/Z가 있는 API 입력은 해당 시점을 KST로 변환한 뒤 offset 없는 DATETIME 값으로 바인딩한다. 예: 2026-09-19T00:00:00+09:00과 2026-09-18T15:00:00Z는 둘 다 DB에 2026-09-19 00:00:00을 저장한다. 드라이버의 자동 시간대 변환 여부를 확인하고 이중 변환하지 않는다.
- 조회 범위의 입력 시각도 KST로 변환한 뒤 window_end와 비교한다. DB에 저장된 값을 UTC로 해석하지 않는다. API 응답은 DB의 KST 값을 +09:00 오프셋을 붙인 ISO 8601 문자열로 직렬화한다.
- DB 세션 점검: SELECT @@system_time_zone, @@global.time_zone, @@session.time_zone, NOW(6), UTC_TIMESTAMP(6); 실제 API 연결에서 @@session.time_zone이 +09:00인지 확인한다. 시스템/전역 설정만으로 추정하지 않는다.
- 이미 UTC 값으로 저장한 행이 있다면 COMMENT나 세션 설정 변경만으로 바뀌지 않는다. 저장된 시간대와 영향 범위를 먼저 확인해 다섯 시각 컬럼을 별도 데이터 마이그레이션한다. 아직 테이블을 만들지 않았다면 이 KST 계약으로 시작한다.

### 조회 인덱스

- 일반점검: check_type='일반점검' AND del_dt IS NULL AND window_end >= :fromKst. 입력 경계는 API에서 KST 값으로 바꾼다.
- 정기점검: check_type='정기점검' AND del_dt IS NULL. 종료 범위 조건을 붙이지 않아 반복 일정의 NULL도 조회한다.
- 구분·삭제·종료·ID 인덱스는 구분별 범위 조회용이다. 현재 API는 ID 내림차순이므로 추가 정렬이 필요할 수 있다. EXPLAIN과 실제 데이터로 검증한다.
- 후보 조회는 구분·기관코드 인덱스로 범위를 줄인 뒤 입력·저장값의 구분·기관코드·일시를 normalize_for_identity로 다시 비교한다. 첫 페이지 일부만 보고 후보가 없다고 결론 내리지 않는다.
- 시작 시각 단독 인덱스는 아직 없다. 실제 시작·종료 조건 조합과 정렬이 확정되면 별도 측정한다.
- 타팀 계정은 SELECT만 허용한다. soft 삭제 제외 조건과 DATETIME 값의 KST 해석을 반드시 전달한다.

## 4. 식별과 문자열 규칙

아래 순서의 5개 값만 SHA-256으로 계산한다. reasonText/windowStart/windowEnd는 제외한다.
저장용 normalize는 NFC·줄바꿈·앞뒤 공백만 정리한다. 기관코드와 구분은 이 값으로 저장하고, 기관명·일시·업무·사유는 원문을 저장한다. 중복·사유 상태·후보·연결 비교에는 별도의 normalize_for_identity를 사용한다. 이 함수는 저장용 normalize 다음에 Unicode casefold를 적용하고 다시 NFC로 정리한다. 원문에는 대소문자 변환을 적용하지 않는다.

~~~python
import hashlib
import json
import unicodedata

FIELDS = ("checkType", "institutionCode", "institutionName", "scheduleText", "serviceText")

def normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.strip("\t\n\v\f\r ")

def normalize_for_identity(value: str) -> str:
    return unicodedata.normalize("NFC", normalize(value).casefold())

def identity_hash(item: dict) -> str:
    values = [normalize_for_identity(item[name]) for name in FIELDS]
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
~~~

내부 공백·요일·날짜 형식은 자동 통합하지 않는다. 이 명세에서 '같은 일시'는 normalize_for_identity(scheduleText)의 정확한 일치다. 일시 원문의 영문 대소문자만 다른 경우는 같은 일시다. 파싱한 시작·종료만 같다는 이유로 서로 다른 원문을 자동 병합하지 않는다. 표기 차이가 많은 경우에는 수집부에서 표시 형식을 일관되게 만든다.

utf8mb4_unicode_ci는 SQL 문자열 비교에서 대소문자와 악센트를 구분하지 않지만, 이 API의 중복키는 위 casefold 결과의 해시로 확정한다. 예를 들어 A사이트 점검/a사이트 점검은 같은 키이고, e/é처럼 악센트만 다른 값은 이 해시 규칙에서 별개일 수 있다. SQL collation 결과를 중복키로 대신 사용하지 않는다. DB 조회 결과는 normalize_for_identity로 다시 확인한다. dedup_hash 컬럼은 명시된 ascii_bin 그대로 둔다.

같은 hash를 찾으면 normalize_for_identity를 적용한 5개 값도 비교한다. 값이 다르면 HASH_COLLISION이며 정상 중복으로 취급하지 않는다.

~~~json
{
  "checkType": "일반점검",
  "institutionCode": "KRBK0101",
  "institutionName": "저축은행중앙회",
  "scheduleText": "2026.09.19(토) 00:00 ~ 06:00",
  "serviceText": "통합금융정보시스템 전체 업무",
  "reasonText": "코어뱅킹 긴급 시스템 작업",
  "windowStart": "2026-09-19T00:00:00+09:00",
  "windowEnd": "2026-09-19T06:00:00+09:00"
}
~~~

위 예시의 해시는 7e519b200e8e52706022d94c18fe6fa7cad3b76137192ae2f1d324f50ad6d531이다. 사유·파싱 시각·영문 대소문자만 바꾸면 유지되고, 대소문자 통일 후에도 일시 원문이 다르면 달라진다. serviceText가 A사이트 점검일 때와 a사이트 점검일 때의 해시는 모두 ec65df74401d7ade40072621bbd7b12c75541d00da714f8ad98c01308f3a5d60이다.

## 5. 공통 API 규약

- 기본 경로: /api/v1/site-check-items. JSON UTF-8, 인증 필수. 인증 방식은 구현 프로젝트 규약에 맞춘다.
- itemId/linkedItemId는 양의 BIGINT UNSIGNED 범위 10진수 문자열이다. JS 정밀도 손실을 피한다.
- 업무 6개 필드는 문자열. 생성 시 모두 필수이며 null은 422다.
- 구분은 정규화 후 일반점검 또는 정기점검. 기관코드·기관명·일시는 정규화 후 비어 있으면 422다.
- 길이 상한은 VARCHAR 정의와 같다(Unicode code point 기준): 기관코드 10자, 기관명 100자, 일시 200자, 업무 200자, 사유 200자. 기관코드는 정규화 전에도 10자 이내여야 한다. 업무·사유는 빈 문자열 허용.
- 생성에는 windowStart/windowEnd를 모두 명시하되 각각 null 가능. 문자열이면 offset/Z가 있는 ISO 8601 날짜·시분초 형식이어야 한다. 소수 초 표기가 있으면 최대 6자리까지 모두 0일 때만 허용한다. KST 변환 후 소수 초가 0이고 MySQL DATETIME 범위 안이어야 한다. 0이 아닌 소수 초는 422로 거부하며 반올림·절삭하지 않는다.
- 두 시각이 모두 있으면 시작 <= 종료여야 한다. 한쪽이 없으면 추정해서 채우지 않는다. 종료된 과거 시각도 허용한다.
- PATCH는 변경 필드만 보낸다. scheduleText 또는 checkType을 보내면 windowStart/windowEnd도 둘 다 명시한다. 일시 원문의 normalize_for_identity 값 자체가 바뀌는 PATCH는 거부하고 신규 등록으로 안내한다.
- 알 수 없는 필드·잘못된 ID·형식은 422. version/observedAt은 지원하지 않는다.
- import/lookup은 최대 1,000행, JSON 요청 본문 5 MiB 이하. 초과는 413. linkedItemId는 import/단건 저장에서만 선택 필드다.
- import/lookup의 최상위 필드는 items만 허용한다. clientRow는 요청 내 중복 없는 1 이상의 안전 정수(최대 9,007,199,254,740,991)이며 응답 연결용이다. Excel 행 번호로 사용할 수 있지만 공지 식별키는 아니다. lookup도 빈 배열을 허용한다.
- DB 연결 세션은 +09:00·utf8mb4/utf8mb4_unicode_ci·strict SQL mode. JSON의 모든 시각(windowStart/windowEnd/createdAt/updatedAt/deletedAt)은 null이 아니면 소수점 없는 KST ISO 8601 +09:00으로 반환한다.
- 성공 응답은 {"data": ...} 구조다. 조회·lookup·import·PATCH·DELETE는 200이며 단건 POST만 아래의 201/200 규칙을 사용한다. import의 SUPPRESSED_DELETED에도 삭제 상태의 item을 반환한다.

### 공통 item 응답

~~~json
{
  "itemId": "101",
  "checkType": "일반점검",
  "institutionCode": "KRBK0101",
  "institutionName": "저축은행중앙회",
  "scheduleText": "2026.09.19(토) 00:00 ~ 06:00",
  "serviceText": "통합금융정보시스템 전체 업무",
  "reasonText": "담당자가 확인한 사유",
  "windowStart": "2026-09-19T00:00:00+09:00",
  "windowEnd": "2026-09-19T06:00:00+09:00",
  "createdAt": "2026-09-17T09:00:00+09:00",
  "updatedAt": "2026-09-18T10:00:00+09:00",
  "deletedAt": null
}
~~~

API의 createdAt/updatedAt/deletedAt은 각각 DB의 crt_dt/up_dt/del_dt에 대응한다. dedup_hash는 내부 컬럼이며 API item에는 포함하지 않는다. 타팀이 DB를 직접 읽는 경우 필요한 컬럼만 SELECT한다.

## 6. 구현할 API

| Method | 경로 | 용도 |
|---|---|---|
| POST | /api/v1/site-check-items:lookup | 사유 제외 5개 값으로 정확한 기존 공지 확인 |
| GET | /api/v1/site-check-items | 일반 목록·같은 일시 후보 조회 |
| GET | /api/v1/site-check-items/{itemId} | 상세·연결 대상 재조회 |
| POST | /api/v1/site-check-items:import | 승인/자동 판정이 끝난 행의 일괄 저장·기존 연결 |
| POST | /api/v1/site-check-items | 검토에서 결정된 한 행 저장·기존 연결 |
| PATCH | /api/v1/site-check-items/{itemId} | 기존 공지의 명시적 수정 |
| DELETE | /api/v1/site-check-items/{itemId} | 소프트 삭제 |

### 6.1 정확한 기존 공지 조회

요청은 items 배열, 각 행은 clientRow와 5개 식별 필드다. 사유·시작·종료·linkedItemId는 받지 않는다.
data.results에 요청 순서대로 clientRow, status, item을 반환한다.
status는 FOUND / DELETED / NOT_FOUND이며 NOT_FOUND의 item은 null이다. 조회는 DB를 변경하지 않는다.
정확한 식별키가 없으면 SiteCheck가 6.2의 같은 구분·기관·일시 후보를 조회한다.

### 6.2 목록·후보 조회

| Query | 기본값 | 의미 |
|---|---|---|
| checkType | 없음 | 점검 구분 |
| institutionCode | 없음 | 식별 정규화한 기관 코드 |
| scheduleText | 없음 | 식별 정규화 후 정확히 같은 일시. 후보 조회에서 사용 |
| includeDeleted | false | true면 삭제 후보도 참고용으로 반환 |
| windowEndFrom | 없음 | 종료 시각 이상(>=) |
| windowEndTo | 없음 | 종료 시각 미만(<) |
| beforeId | 없음 | 이 ID 미만 |
| limit | 50 | 1~200 |

범위 시각 검증은 입력 windowEnd와 같다. windowEndFrom/windowEndTo도 KST 변환 후 0이 아닌 소수 초가 있으면 422로 거부한다. 양쪽이 있으면 From < To여야 한다. 입력 경계는 초 단위 KST DATETIME 값으로 변환해 비교한다. 범위가 있으면 NULL 종료 시각은 제외한다.
정렬은 item_id DESC. data.items, hasNext, nextBeforeId를 반환한다. 같은 필터로 다음 페이지를 읽는다.
일시 필터는 normalize_for_identity로 비교하며 일치하는 행을 모아 페이지를 구성한다. 구분·기관코드 사전 조회도 같은 규칙으로 최종 확인한다. 내부 스캔에 별도 상한을 두어 결과를 잘라놓고 hasNext=false를 반환하면 안 된다.
후보 조회는 checkType/institutionCode/scheduleText를 모두 보내고 includeDeleted=true로 조회한다. 삭제된 행은 선택 불가로 표시한다. 이 조회는 후보 지원 기능이지 동일 공지를 자동 확정하는 로직이 아니다.

상세는 data에 공통 item을 반환한다. 삭제 건은 기본 404, includeDeleted=true이면 반환. 존재하지 않는 ID는 항상 404다.

### 6.3 신규 저장·기존 공지 연결

SiteCheck는 정확한 기존 공지와 로컬 연결을 우선 확인한다. 같은 일시에 모호한 후보가 있으면 사람의 결정을 받은 행만 저장 API로 전달한다. 보류 항목은 전송하지 않는다. API의 신규 저장 요청 자체는 호출부에서 신규 등록을 승인한 명령이며, 후보 존재를 이유로 거부하지 않는다. 정확한 중복 검사는 항상 다시 수행한다.

~~~json
{
  "items": [
    {
      "clientRow": 3,
      "checkType": "일반점검",
      "institutionCode": "KRBK0101",
      "institutionName": "저축은행중앙회",
      "scheduleText": "2026.09.19(토) 00:00 ~ 06:00",
      "serviceText": "통합금융정보시스템 전체 업무",
      "reasonText": "수집된 사유",
      "windowStart": "2026-09-19T00:00:00+09:00",
      "windowEnd": "2026-09-19T06:00:00+09:00",
      "linkedItemId": "101"
    }
  ]
}
~~~

linkedItemId가 없으면 정확한 식별키로만 저장/조회한다. 있으면 사람이 선택했거나 기존 로컬 연결에 기록된 대상이다. 새 행을 만들거나 기존 내용을 수정하지 않고 선택한 공지를 사용한다.

| 성공 행별 status | 처리 |
|---|---|
| INSERTED | 처음 보는 식별키를 저장 |
| EXISTING | 같은 5개 값·식별 정규화 사유. 기존 전체 값 사용 |
| REASON_PRESERVED | 같은 5개 값·식별 정규화해도 다른 사유. 기존 전체 값 사용 |
| LINKED_EXISTING | 다른 5개 키이지만 검증된 linkedItemId의 기존 전체 값 사용 |
| SUPPRESSED_DELETED | 정확히 같은 키 또는 유효한 연결 대상이 삭제됨. 생성·복원 금지 |

입력 시각이나 사유가 달라도 기존 행을 자동 갱신하지 않는다. 같은 요청 내부의 동일 키는 첫 저장 행을 기준으로 뒤의 행들을 판정한다.
data.results는 clientRow/status/item을 입력 순서대로 반환한다. data.summary는 received, inserted, existing, reasonPreserved, linkedExisting, suppressedDeleted를 포함하며 다섯 상태 합계가 received와 같다.

연결 검증:
1. 대상 ID와 기존 정확한 해시 행을 현재 DB 상태로 확인한다.
   해시가 발견되면 먼저 normalize_for_identity 기준 5개 값의 일치를 확인한다. 다르면 연결 귀속 판정보다 HASH_COLLISION을 우선한다.
2. 정확한 해시가 다른 ID에 이미 귀속돼 있으면 409 IDENTITY_ALREADY_ASSIGNED. 기존 공지를 다른 대상으로 옮기지 않는다.
3. 대상의 normalize_for_identity 기준 구분·기관코드·일시가 입력과 모두 같아야 한다. 다르면 409 LINK_TARGET_CHANGED다. 다른 일시는 연결 불가다.
4. 대상 미존재는 404, 삭제된 유효 대상은 SUPPRESSED_DELETED다. 수동 복원 이후에는 같은 연결로 정상 사용 가능하다.
5. 응답 item은 연결 대상의 실제 저장값이다. caller가 보낸 원문을 저장한 것으로 표시하지 않는다.

모든 행을 사전 검증한 뒤 한 DB 트랜잭션으로 반영한다. 연결 검증·입력·DB 오류나 해시 충돌이면 전체 롤백한다. CONFLICT 행별 결과는 없다. linked 대상과 정확한 키의 행을 잠금 재확인하며 동시 신규는 UNIQUE로 보호한다. 데드락은 전체 롤백 확인 후 제한적으로 재시도한다. 삭제·PATCH와 경합해도 삭제된 행을 되살리지 않는다.

빈 배열은 아무 변경 없는 성공이다. 요청에 없는 과거 행을 삭제하지 않는다.
단건 POST는 같은 필드/규칙이며 clientRow 없이 보낸다. 응답 data는 status/item. INSERTED는 201, 나머지는 200이다.

### 6.4 PATCH

~~~json
{
  "reasonText": "담당자가 수정한 사유"
}
~~~

- version 없이 ID로 잠금 조회하고 최신 행에 요청한 필드만 반영한다. 생략 필드는 유지한다. 마지막 성공 DB 반영이 우선이며 클릭·응답 도착 순서는 보장하지 않는다.
- 일시 원문의 normalize_for_identity 값이 달라지면 409 SCHEDULE_CHANGE_REQUIRES_CREATE. 새 공지 생성 후 기존 공지의 삭제 여부를 담당자가 결정한다. 공백/NFC/영문 대소문자만 달라 같은 식별 일시라면 원문 보정은 가능하다.
- 사유와 파싱된 시작·종료 보정은 해시를 유지한다. 기관명·업무·구분·기관코드가 바뀌면 해시를 재계산한다. 다른 ID의 키와 겹치면 삭제 행 포함 409 DUPLICATE_IDENTITY다.
- 최종 6개 값과 두 KST 시각이 같으면 NO_CHANGE, 다르면 UPDATED 및 up_dt 갱신. crt_dt는 유지한다.
- up_dt는 초 단위이므로 같은 초 안에 여러 번 명시적 수정하면 값이 같을 수 있다. 수정 순서나 충돌 판단에 up_dt를 사용하지 않고 DB에 마지막으로 반영된 행의 내용을 현재값으로 사용한다.
- 기간 순서는 잠금 후 병합한 최종 값으로 검증한다. 일정 원문/구분을 보내면 두 시각도 모두 명시한다.
- 삭제된 행은 409 ITEM_DELETED, 미존재는 404다. 응답 200의 data는 status/item이다.
- SiteCheck는 기관명·업무 등 식별 값 수정 성공 후 변경 전 입력을 현재 ID에 연결하는 로컬 기록을 남겨 재수집 때 기존 수정값을 사용한다. 구분·기관코드가 달라진 연결은 자동 적용하지 않고 재검토한다.

### 6.5 DELETE

DELETE /api/v1/site-check-items/101

ID만 사용한다. 미삭제 행을 잠금 조회해 del_dt/up_dt를 같은 현재 KST로 설정한다. 그동안 수정된 공지도 삭제한다.
이미 삭제면 ALREADY_DELETED이며 시각을 바꾸지 않는다. 미존재는 404다.
응답 data.status는 DELETED / ALREADY_DELETED, data.item은 삭제 상태의 공지다.
삭제가 먼저 반영된 뒤 PATCH하면 ITEM_DELETED다. 자동 복원하지 않는다.

## 7. 오류·재시도·권한

| HTTP | code | 의미 |
|---|---|---|
| 400 | INVALID_JSON | JSON 오류 |
| 401/403 | UNAUTHORIZED/FORBIDDEN | 인증·권한 |
| 404 | ITEM_NOT_FOUND | 대상 없음 |
| 409 | HASH_COLLISION | 같은 hash, 다른 정규화 식별 값 |
| 409 | DUPLICATE_IDENTITY | PATCH가 다른 공지의 식별키와 충돌 |
| 409 | IDENTITY_ALREADY_ASSIGNED | 수동 연결 입력 키가 다른 ID에 이미 귀속 |
| 409 | LINK_TARGET_CHANGED | 연결 대상의 구분·기관·일시가 입력과 다름 |
| 409 | SCHEDULE_CHANGE_REQUIRES_CREATE | 일시 변경은 신규 등록 필요 |
| 409 | ITEM_DELETED | 삭제된 대상 수정 |
| 413 | PAYLOAD_TOO_LARGE | 요청 크기 초과 |
| 422 | VALIDATION_ERROR | 필드·형식·기간 검증 실패 |
| 500/503 | INTERNAL_ERROR/SERVICE_UNAVAILABLE | 서버/일시 장애 |

~~~json
{"error":{"code":"LINK_TARGET_CHANGED","message":"연결 대상의 일시 또는 기관이 변경되었습니다.","details":{"itemId":"101"}}}
~~~

import/단건 생성은 동일 payload로 재시도할 수 있다. DB UNIQUE와 연결 재검증은 매번 수행한다.
PATCH 응답 불명은 현재 값을 조회하고 담당자 확인 없이 과거 payload를 지연 자동 재전송하지 않는다. 그 사이 최신 수정을 덮어쓸 수 있다.
DELETE는 복원 작업과 경합하지 않는 동안 같은 ID로 재시도 가능하다. 수동 복원 전에는 미완료 삭제 요청·로컬 대기 작업을 확인하고 재실행되지 않도록 정리한다.
업무 데이터 변경은 저장 API로 통일한다. 타팀에는 SELECT 권한만 부여하고 로컬 연결 및 인증정보는 DB 조회 계정에 제공하지 않는다.

## 8. 구현·테스트·회신 체크리스트

1. 사용자가 직접 전달한 DB 정보로 연결·마이그레이션을 구성한다. 운영 비밀값은 Git/문서에 넣지 않는다.
2. 정규화·해시·시각 검증·soft 삭제 제외 조회·트랜잭션을 공통 서비스로 구현한다.
3. lookup/목록/상세/생성/import/PATCH/DELETE를 구현한다. 기존 수정값 보존과 연결 검증을 같은 서비스에서 재사용한다.
4. 아래 인수 시나리오를 실제 MySQL에서 테스트하고 실행계획·동시성·요청 제한을 확인한다.
5. 테스트 Base URL·인증 전달 방법·접속 조건·배포 버전을 사용자에게 회신한다.
6. SiteCheck 연동 검증 후 운영 URL과 배포 절차를 확정한다. DB 정보는 이미 사용자가 API 프로젝트에 직접 제공하는 흐름이다.

| 시나리오 | 기대 결과 |
|---|---|
| 같은 5개 값 어제·오늘/동시 저장 | DB 한 행 |
| 업무 A사이트 점검 / a사이트 점검 | 같은 해시·같은 ID. 저장된 원문은 자동으로 바뀌지 않음 |
| 사유 A사이트 점검 / a사이트 점검 | 같은 ID, 대소문자 차이만 있으면 EXISTING |
| 일시 원문의 영문 대소문자만 다름 | 같은 공지. PATCH도 같은 일시의 원문 보정으로 처리 |
| 업무 e / é | SQL의 unicode_ci 비교와 별도로 해시 기준에서는 다른 키일 수 있음 |
| 사유 A를 B로 PATCH 후 A 재수집 | 같은 ID·B 보존 |
| 일시 00~06이 00~07로 변경 | 새 행. 기존 행 자동 삭제 없음 |
| 다른 일시로 LINK | LINK_TARGET_CHANGED |
| 같은 구분·기관·일시, 다른 업무 수동 LINK | LINKED_EXISTING, 대상 값 유지 |
| LINK 재수집·프로세스 재시작 | 로컬 기록 + API 재검증으로 같은 ID 사용 |
| 삭제된 정확 키/유효 연결 대상 | SUPPRESSED_DELETED |
| 복원 후 같은 키/연결 | 정상 기존 공지 사용, 새 행 없음 |
| 기존 정확 키가 다른 ID인데 LINK 시도 | IDENTITY_ALREADY_ASSIGNED |
| 변경 전 업무로 재수집 | 로컬 연결이 있으면 담당자 수정값 사용 |
| 명시적인 일시 변경 PATCH | SCHEDULE_CHANGE_REQUIRES_CREATE |
| 두 수정 요청이 같은 필드를 덮어씀 | 마지막 DB 반영 값 유지 |
| 삭제와 PATCH 경합 | 삭제 후 복원 없음 |
| 보류/후보 조회 | DB 변경 없음 |
| 잘못된 한 배치 행·해시 충돌 | 전체 롤백 |
| 시작 > 종료 | 422 |
| 시작·종료 또는 조회 경계의 소수 초가 0이 아님 | 422, DB에서 반올림하지 않음 |
| 시작·종료 입력의 .000000 | 허용하되 저장·응답은 소수점 없는 초 단위 |
| 생성·수정·삭제 시각 응답 | 소수점 없는 +09:00, 같은 초의 다중 수정은 up_dt가 같을 수 있음 |
| 정기점검의 시각 NULL | 저장 가능, 구분 조회에서 표시 |
| +09:00 입력과 같은 시점의 Z 입력 | window_start/window_end에 같은 KST 값 저장, 응답은 +09:00 |
| DB 서버는 KST지만 API 연결 세션이 UTC로 초기화됨 | 연결 시 +09:00 설정 후 CURRENT_TIMESTAMP·삭제 시각이 KST로 저장됨 |
| 종료 범위에 Z 경계 입력 | KST 경계로 변환해 인덱스 대상 window_end와 비교 |
| 일반점검 종료 범위 | 하한 포함·상한 제외·NULL 제외 |
| 같은 원문/시각 PATCH | NO_CHANGE, up_dt 불변 |
| 최초 적재 | 최신 엑셀 한 파일만, 과거 파일 자동 순회 없음 |

기존 공지의 일시 변경 신규 처리, 수동 연결 기록의 백업/원자적 저장, API 재시도 경계는 구현 필수다. 순수 내용 키만으로 모든 변경을 자동 연결할 수 있다고 가정하지 않는다.
