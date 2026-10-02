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
