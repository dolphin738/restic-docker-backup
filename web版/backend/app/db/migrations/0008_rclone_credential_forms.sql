-- 0008_rclone_credential_forms.sql —— rclone conf 凭据「落盘形态」记录（裁决 15 方案 A）。
--
-- 背景：rclone 的 `reveal` **无认证校验**（v1.60.1 真机实证：任意足够长的 base64 可解码串
-- 都能「解出」一串垃圾且 exit 0），因此「这个 conf 值是不是混淆产物」**无法事后判定**——
-- 形态必须在**写入时**记录（那次我们显式传了 --obscure / --no-obscure，真值即在）。
--
-- 表内容（**不含任何凭据明文**，不破 S3 零回显）：
--   remote       conf 段名（远端名）
--   field        凭据键（password / password2 / pass …）
--   obscured     1 = 该值落盘形态是混淆产物；0 = 明文
--   value_sha256 conf 现值的 SHA-256 —— **外部改动检测**：conf 被宿主机手工改过则哈希对不上，
--                形态判「未知」，就地转换跳过（宁缺勿错，绝不拿猜测的形态去重写密码）
--   updated_at   记录更新时间（ISO 8601 UTC）
--
-- 生命周期：
--   - 删除配置段 → 同事务清掉该 remote 的全部行（不留垃圾）；
--   - 宿主机在外部删段/改值 → 记录成孤儿，由 list_configs 的惰性对齐清理（conf 中已不存在的
--     remote 一律删除），孤儿记录永不被当作「已知形态」使用（哈希校验先拦）。
--
-- 幂等：IF NOT EXISTS + INSERT OR REPLACE，可重复执行无副作用（迁移器按 user_version 守卫）。

CREATE TABLE IF NOT EXISTS rclone_credential_forms (
    remote       TEXT NOT NULL,
    field        TEXT NOT NULL,
    obscured     INTEGER NOT NULL,
    value_sha256 TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (remote, field)
);

CREATE INDEX IF NOT EXISTS idx_rclone_credential_forms_remote
    ON rclone_credential_forms (remote);

PRAGMA user_version = 8;
