/** 多备份任务域数据层（增量设计 §2 / §7-T05）。
 *
 * 覆盖端点：
 * - E34  `GET  /api/repos/{repo_id}/backup-tasks`            任务卡片列表
 * - E34b `GET  /api/repos/{repo_id}/backup-tasks/{id}`       任务详情/编辑预填
 * - E35  `POST /api/repos/{repo_id}/backup-tasks`            创建（tag 服务端生成）
 * - E35b `PUT  /api/repos/{repo_id}/backup-tasks/{id}`       更新（全量语义）
 * - E35c `DELETE /api/repos/{repo_id}/backup-tasks/{id}`     删除（只解绑，不碰快照）
 * - E36  `POST /api/repos/{repo_id}/backup-tasks/{id}/dry-run` 任务域扫描预览
 *
 * 备份源页退役（Q2）后，E05（无状态目录浏览）与 E31（无实体 dry-run 预览）
 * 的唯一消费方即任务向导，故随本模块一并收敛；两端点契约本身不变
 * （E31 定位收窄为「向导内无实体预览」，见设计 §2.2）。
 */

import { passCursor, request } from './http'
import type {
  BackupTask,
  DryRunResponse,
  OkResponse,
  Page,
  SourceBrowseResponse,
  TaskCard,
  TaskDryRunRequest,
  TaskUpsertRequest,
} from './types'

/** repo 作用域（E6）：本批仅 default 一个仓库，其余值后端 404 REPO_NOT_FOUND。 */
export const REPO_ID = 'default'

function taskPath(suffix = ''): string {
  return `/api/repos/${REPO_ID}/backup-tasks${suffix}`
}

/** E34：任务卡片列表（含派生状态/下次运行/运行中 job）。 */
export function listBackupTasks(limit = 50, offset = 0): Promise<Page<TaskCard>> {
  return request<Page<TaskCard>>(taskPath(), { query: { limit, offset } })
}

/** E34b：任务详情/编辑预填（display_* 为展示形态，直接进表单）。 */
export function getBackupTask(taskId: number): Promise<BackupTask> {
  return request<BackupTask>(taskPath(`/${taskId}`))
}

/** E35：创建任务；请求体无 tag（服务端生成 task-<id>）。 */
export function createBackupTask(payload: TaskUpsertRequest): Promise<BackupTask> {
  return request<BackupTask>(taskPath(), { method: 'POST', body: payload })
}

/** E35b：更新任务（全量语义）；sources/excludes 变更会令服务端重置 dry_run_pending。 */
export function updateBackupTask(taskId: number, payload: TaskUpsertRequest): Promise<BackupTask> {
  return request<BackupTask>(taskPath(`/${taskId}`), { method: 'PUT', body: payload })
}

/** E35c：删除任务；运行中/等待中 → 409 TASK_RUNNING（data 带 running_job_id/task_name）。 */
export function deleteBackupTask(taskId: number): Promise<OkResponse> {
  return request<OkResponse>(taskPath(`/${taskId}`), { method: 'DELETE' })
}

/** E36：任务域扫描预览（缺省取任务存量 sources/excludes）；预览成功即清除 dry_run_pending。 */
export function dryRunBackupTask(taskId: number, payload: TaskDryRunRequest = {}): Promise<DryRunResponse> {
  return request<DryRunResponse>(taskPath(`/${taskId}/dry-run`), { method: 'POST', body: payload })
}

// ---------------------------------------------------------------- E05 / E31（向导内源选择与预览）

/** E05 目录浏览（无状态）：入参 path 与响应均为展示形态原路径语义。 */
export function browseSources(
  path?: string,
  cursor?: string | null,
  limit = 200,
): Promise<SourceBrowseResponse> {
  return request<SourceBrowseResponse>('/api/sources', {
    // path 缺省或传 "/" 都落在当前模式根
    query: { path, cursor: passCursor(cursor), limit },
  })
}

/** E31 无实体 dry-run 预览（向导新建流）；命中伪文件系统目录名回 warnings（黄色，不拦截）。 */
export function dryRunSources(sources: string[], excludes: string[]): Promise<DryRunResponse> {
  return request<DryRunResponse>('/api/sources/dry-run', {
    method: 'POST',
    body: { sources, excludes },
  })
}
