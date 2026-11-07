/** 全站数据备份 / 恢复数据层（方案 v1.5 §3.2 / §4.2 / §6.2；W5 前端）。
 *
 * 三端点（E69 / E70 / E71）**统一走 http.ts::request 唯一出口**——S4/S5 要求三个写方法
 * 都过 cookie 鉴权与 Origin 同源校验（服务端 `_PROTECTED` + `CSRFOriginMiddleware`），
 * 因此这里不另起 `fetch`、不持有 EventSource、不加 query 凭据。
 *
 * S3：口令只经请求体往返，不落 localStorage / sessionStorage、不进 URL、不进日志。
 */

import { request } from './http'
import type {
  SiteBackupPack,
  SiteExportRequest,
  SiteImportReport,
  SiteImportRequest,
  SiteInspectReport,
  SiteInspectRequest,
} from './types'

/** 全站备份包的 `format` 标识（§2.2，与旧配置迁移包 `restic-web-config-export` 区分）。
 *
 * 前端只做**选文件时的第一道形态过滤**（非本系统包不发请求；旧配置迁移包给出「已停止支持」
 * 的指路文案，§2.4 / §4.7）；结构 / 版本 / AAD / GCM 等真正的校验一律在服务端。
 */
export const SITE_PACK_FORMAT = 'restic-web-site-backup'

/** 旧「配置迁移」包标识：用户拿错文件时才有用的区分（D6：不做兼容，明确拒绝）。 */
export const LEGACY_CONFIG_PACK_FORMAT = 'restic-web-config-export'

/** POST /api/site-backup/export：导出全站备份包（强制加密，不提供「无口令导出」）。
 *
 * 服务端以 `application/json` 附件返回包原文；这里返回**包对象**（含 `created_at`），
 * 由页面层拼文件名后落盘——与 configTransfer 同形态：包内容不经前端解释，只透传。
 */
export function exportSiteBackup(body: SiteExportRequest): Promise<SiteBackupPack> {
  return request<SiteBackupPack>('/api/site-backup/export', { method: 'POST', body })
}

/** POST /api/site-backup/inspect：只读预检（**零业务写入**）→ 包摘要 + 目标现状 + 阻碍项。
 *
 * `blockers` 与 import 侧 422 的 `data.blockers` 同源（同一服务端纯函数），
 * 因此「预览说能导、导却被拒」不会发生。
 */
export function inspectSiteBackup(body: SiteInspectRequest): Promise<SiteInspectReport> {
  return request<SiteInspectReport>('/api/site-backup/inspect', { method: 'POST', body })
}

/** POST /api/site-backup/import：整机替换导入（唯一语义 `replace` + 二次确认串 `acknowledge`）。
 *
 * 成功后服务端已清空 `sessions`（`report.logged_out` 恒真），调用方须在展示报告后
 * 引导用户重新登录（§4.6）。
 */
export function importSiteBackup(body: SiteImportRequest): Promise<SiteImportReport> {
  return request<SiteImportReport>('/api/site-backup/import', { method: 'POST', body })
}
