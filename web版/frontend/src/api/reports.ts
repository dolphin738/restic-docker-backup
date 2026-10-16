/** R-024 备份成功率报表（E38）。 */

import { request } from './http'
import type { BackupSuccessReport } from './types'

/** 时间窗天数（1..90，与后端 REPORTS_MAX_DAYS 对齐；越界 422）。 */
export function getBackupSuccessReport(days = 30): Promise<BackupSuccessReport> {
  return request<BackupSuccessReport>('/api/reports/backup-success', { query: { days } })
}
