/** 配置迁移数据层（批次 E / D4：导出含口令加密凭据段；导入带覆盖开关与报告）。
 *
 * 导出响应即导出包 JSON——前端拿到对象后本地生成 Blob 下载（文件名由页面层定），
 * 服务端 Content-Disposition 仅作参考。
 */

import { request } from './http'
import type { ConfigExportPack, ConfigExportRequest, ConfigImportReport, ConfigImportRequest } from './types'

/** POST /api/config/export：导出包（凭据经导出口令 PBKDF2-HMAC-SHA256 600k 迭代 + AES-256-GCM 加密）。 */
export function exportConfig(body: ConfigExportRequest): Promise<ConfigExportPack> {
  return request<ConfigExportPack>('/api/config/export', { method: 'POST', body })
}

/** POST /api/config/import：导入（冲突默认跳过；replace_existing=true 整体替换，需 ConfirmGate）。 */
export function importConfig(body: ConfigImportRequest): Promise<ConfigImportReport> {
  return request<ConfigImportReport>('/api/config/import', { method: 'POST', body })
}
