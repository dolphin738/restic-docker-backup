/** 统一请求出口：cookie 鉴权、错误码 → AppError、401/403 分流（§5.0 / §8）。 */

import { i18n } from '@/i18n'
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
  const init: RequestInit = {
    // cookie session（HttpOnly + SameSite=Strict）；SSE 与写请求同源策略都依赖它
    credentials: 'include',
    ...rest,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(headers as Record<string, string> | undefined),
    },
  }
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
    applyErrorSideEffects(error, silent)
    throw error
  }

  return payload as T
}

function applyErrorSideEffects(error: AppError, silent: boolean | undefined): void {
  const ui = useUiStore()

  if (error.code === 'UNAUTHORIZED') {
    ui.clearMe()
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
