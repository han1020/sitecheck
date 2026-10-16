-- Source: config/regular_maintenance.yaml (11 regular-maintenance rows).
-- dedup_hash values use src.notice_identity.identity_hash, matching the API contract.
-- Run after GDS.TB_SITE_CHECK exists. This does not restore or overwrite existing notices.
-- Rebuild this file if the YAML identity fields change.
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;
SET SESSION time_zone = '+09:00';

INSERT INTO `GDS`.`TB_SITE_CHECK` (
    `check_type`, `institution_code`, `institution_name`, `schedule_text`,
    `service_text`, `reason_text`, `window_start`, `window_end`, `dedup_hash`
) VALUES
    ('정기점검', 'KRBK0002', '산업은행', '홀수월 둘째 일요일 00:00 ~ 04:00 (4시간)',
     '인터넷뱅킹, 스마트폰뱅킹, 텔레뱅킹, 자동화기기, 체크카드', '시스템 전환 작업', NULL, NULL,
     'a10692868aa67279fd54c1e89392c09131a63fa4ffd4cb229f4a4b55e83ed961'),
    ('정기점검', 'KRBK0004', '국민은행', '매월 셋째주 일요일 00:00 ~ 07:00',
     '일부 서비스 불가', '시스템 조정 작업', NULL, NULL,
     '69bedd5595013b389b8f016305f1a13e8652da8f2004ecc2ec5d176f978c2bf4'),
    ('정기점검', 'KRBK0011', '농협은행', '매월 셋째 일요일 23:55(전일) ~ 04:00 (단, 월요일이 공휴일인 경우 익영업일)',
     '인터넷/스마트뱅킹 등 e금융 로그인 및 공인인증서비스, 자동화기기 등 전 매체 전자금융서비스', '시스템 정기점검', NULL, NULL,
     'd31b67214da6ae4df92471818c7fb5919903c5b403de7923ee2471e01cdff5bb'),
    ('정기점검', 'KRBK0020', '우리은행', '매월 둘째 일요일 02:00 ~ 06:00 (4시간)',
     '홈페이지/스마트앱/모바일웹 일부', '시스템 정기점검', NULL, NULL,
     'fdb2fadd5a919cf3c619d6cede24716cf98257f302d5b20077c30f07db059992'),
    ('정기점검', 'KRBK0088', '신한은행', '매주 목요일 21:00 ~ 익일 02:00',
     '기업인터넷뱅킹 일부 업무 지연', '정기 개선 작업', NULL, NULL,
     'fe2464c1a57a18027c95cb1378b2f5571965c72dbd0a46cb722fbd1c11b7e2a4'),
    ('정기점검', 'KRCK0002', '인터넷 등기소', '매월 첫째 주, 셋째 주 목요일 21:00 ~ 06:00',
     '인터넷등기소 전자신청, 열람/발급 등 주요 서비스', '정기 변경 작업', NULL, NULL,
     'c3b2fdd1a3c9bf8e2d8508f56f94893baf7eefb4e2798fd593d702e8c0d142cc'),
    ('정기점검', 'KRCK0002', '인터넷 등기소', '매월 첫째 주 토요일 23:00 ~ 02:00',
     '인터넷등기소 전자신청, 열람/발급 등 주요 서비스', '전자지불대행업체 정기테스트', NULL, NULL,
     'b82abc012267f68666ea9c243c526fb72628d383cae11d1245af540e49ac184e'),
    ('정기점검', 'KRCK0002', '인터넷 등기소', '매월 둘째 주 토요일 21:00 ~ 09:00',
     '인터넷등기소 전자신청, 열람/발급 등 주요 서비스', '정기 데이터 백업 및 시스템 점검', NULL, NULL,
     '71bab5c25fbc516ecf8b611f93a2c703a464e132ab35560b6f71dd4ad0f2d5e5'),
    ('정기점검', 'KRCK0002', '인터넷 등기소', '매월 셋째 주 토요일 23:00 ~ 07:00',
     '인터넷등기소 전자신청, 열람/발급 등 주요 서비스', '전자지불대행업체 예방점검', NULL, NULL,
     'fcce7ddb85299d6297753f03b7e8ef6ed0158d7fbec1e8c0bcca00fedcbf15c0'),
    ('정기점검', 'KRCK0002', '인터넷 등기소', '매월 둘째 주 일요일 21:00 ~ 07:00',
     '인터넷등기소 전자신청, 열람/발급 등 주요 서비스', '전자 문서 삭제', NULL, NULL,
     '3381cad883cb45a5d3359b48f6f5ee3ebe7c1c8a8ae23a18b50d70461e6e435b'),
    ('정기점검', 'KRPP0002', '국민건강보험', '매월 셋째 주 일요일 01:00 ~ 08:00',
     '징수포털 모든 서비스 제한', '인터넷 시스템 정기 점검', NULL, NULL,
     '6b95538700cc49384810b3ea6b6ab34107623db734393d62cc4a92ecc1b725a3')
ON DUPLICATE KEY UPDATE `item_id` = `item_id`;

SELECT `item_id`, `institution_code`, `institution_name`, `schedule_text`,
       `service_text`, `reason_text`, `window_start`, `window_end`, `del_dt`
FROM `GDS`.`TB_SITE_CHECK`
WHERE `check_type` = '정기점검'
ORDER BY `item_id`;
