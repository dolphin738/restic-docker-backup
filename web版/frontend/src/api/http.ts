/** 统一请求出口：cookie 鉴权、错误码 → AppError、401/403 分流（§5.0 / §8）。 */

import { i18n } from '@/i18n'
import { getActivePinia } from 'pinia'
import { useUiStore } from '@/stores/ui'
import type { ErrorBody } from './types'

export const API_BASE = '/api'

/** 游标只透传：前端不解析、不自造、不缓存结构，服务端给什么原样回传什么。 */
export function passCursor(cursor: string | null | undefined): string | undefined {
  return cursor ?? undefined
}

/** 业务错误：code 与后端错误码表一一对应。 */
export class AppError extends Error {
  readonly code: string
  readonly status: number
  readonly data: Record<string, unknown> | null

  constructor(code: string, message: string, status: number, data: Record<string, unknown> | null = null) {
    super(message)
    this.name = 'AppError'
    this.code = code
    this.status = status
    this.data = data
  }
}

export function isAppError(error: unknown): error is AppError {
  return error instanceof AppError
}

export function hasCode(error: unknown, code: string): boolean {
  return isAppError(error) && error.code === code
}

export interface AuthRedirectHandlers {
  onUnauthorized?: () => void
  onBootstrapRequired?: (steps: string[]) => void
}

let redirectHandlers: AuthRedirectHandlers = {}
let lastRedirectAt = 0

/** 由 main.ts 注入路由跳转实现，避免 http 层反向依赖 router 造成循环引用。 */
export function setAuthHandlers(handlers: AuthRedirectHandlers): void {
  redirectHandlers = handlers
}

/** 1 秒内只跳一次：并发请求同时 401 时只触发一次跳转，杜绝抖动与死循环。 */
function redirectOnce(run: () => void): void {
  const now = Date.now()
  if (now - lastRedirectAt < 1000) return
  lastRedirectAt = now
  run()
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** 对象体自动 JSON 序列化；undefined 表示无请求体。 */
  body?: unknown
  query?: Record<string, string | number | undefined>
  /** true 时不弹全局提示（表单内联展示错误的场景）。 */
  silent?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, silent, headers, ...rest } = options
  // R-023：当前仓库 id 经 X-Repo-Id 头注入，服务端据此路由多仓库读写；
  // 无激活 Pinia（极端初始化态）时回落 default，避免崩溃。
  let repoId = 'default'
  if (getActivePinia()) {
    repoId = useUiStore().currentRepoId || 'default'
  }
  const init: RequestInit = {
    // cookie session（HttpOnly + SameSite=Strict）；SSE 与写请求同源策略都依赖它
    credentials: 'include',
    ...rest,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      // R-023：默认注入当前仓库路由头（驱动后端 current_repo_id 依赖）。
      // 必须置于 ...headers 之前：调用方（如快照页「查看归档仓快照」）可在 headers 里
      // 显式传 X-Repo-Id 覆盖当前仓——此前该行写在展开之后，归档仓 id 被当前仓 id 覆盖，
      // 导致快照页跳归档仓只回显当前仓数据（缺陷 issue 1）。
      'X-Repo-Id': repoId,
      ...(headers as Record<string, string> | undefined),
    },
  }
  // 实际生效的仓库路由头（含调用方覆盖）：REPO_NOT_FOUND 兜底用它判断失效的是不是当前仓——
  // 不能用上面的 repoId 默认值，否则显式覆盖（看归档仓）的 404 会被误判成当前仓失效。
  const effectiveRepoId = (init.headers as Record<string, string>)['X-Repo-Id'] ?? repoId
  if (body !== undefined) {
    init.body = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), init)
  } catch {
    const networkError = new AppError('NETWORK_ERROR', i18n.global.t('errors.NETWORK_ERROR'), 0)
    if (!silent) {
      useUiStore().notifyError(networkError.code, networkError.message)
    }
    throw networkError
  }

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text) as unknown
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    const errorBody = (payload ?? {}) as Partial<ErrorBody>
    const code = errorBody.code ?? `HTTP_${response.status}`
    const error = new AppError(
      code,
      errorBody.message ?? i18n.global.t('errors.UNKNOWN'),
      response.status,
      errorBody.data ?? null,
    )
    applyErrorSideEffects(error, silent, effectiveRepoId)
    throw error
  }

  return payload as T
}

function applyErrorSideEffects(error: AppError, silent: boolean | undefined, requestRepoId = 'default'): void {
  const ui = useUiStore()

  if (error.code === 'UNAUTHORIZED') {
    ui.clearMe()
    // 抑制位（默认 false）：全站备份包导入成功后服务端已清空 sessions，本会话作废，
    // 但导入报告（含回滚点目录名）必须留在屏上——顶栏 15s 轮询拿到的 401 不得把人带走。
    // 清除本地会话态照做（否则界面仍显示「已登录」），只跳过跳转这一半。
    if (ui.suppressAuthRedirect) {
      return
    }
    redirectOnce(() => redirectHandlers.onUnauthorized?.())
    return
  }
  if (error.code === 'BOOTSTRAP_REQUIRED') {
    const raw = error.data?.['steps']
    const steps = Array.isArray(raw) ? raw.filter((item): item is string => typeof item === 'string') : []
    ui.setBootstrapSteps(steps)
    redirectOnce(() => redirectHandlers.onBootstrapRequired?.(steps))
    return
  }
  // 当前仓失效兜底（2026-11-02）：删除/归档/释放当前仓后，localStorage 里残留的 id 会让
  // 每个带 X-Repo-Id 的请求 404「仓库 'xxx' 不存在」刷屏。仅当失效的正是当前仓时自动切回
  // default（调用方显式传的 X-Repo-Id 覆盖不在此列——那是快照页看归档仓的合法路径，仓库
  // 归档后仍应可见）。持久化在 setCurrentRepoId 内完成，全局查询由仓库页切换语义失效。
  if (error.code === 'REPO_NOT_FOUND' && error.status === 404 && requestRepoId !== 'default' && ui.currentRepoId === requestRepoId) {
    ui.setCurrentRepoId('default')
  }
  if (!silent) {
    ui.notifyError(error.code, error.message)
  }
}

function buildUrl(path: string, query?: Record<string, string | number | undefined>): string {
  const url = path.startsWith('/') ? path : `${API_BASE}/${path}`
  if (!query) {
    return url
  }
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') {
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  return qs ? `${url}?${qs}` : url
}
