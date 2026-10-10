/** 认证相关端点：E01 登录 / E02 登出 / E03 me / 首启随机密码 / E28 改密。 */

import { request } from './http'
import type { BootstrapResponse, MeResponse, OkResponse } from './types'

/** 登录失败文案由登录页内联展示，故 silent 关闭全局提示。 */
export function login(password: string): Promise<OkResponse> {
  return request<OkResponse>('/api/auth/login', { method: 'POST', body: { password }, silent: true })
}

export function logout(): Promise<OkResponse> {
  return request<OkResponse>('/api/auth/logout', { method: 'POST' })
}

export function fetchMe(): Promise<MeResponse> {
  return request<MeResponse>('/api/auth/me')
}

/** 一次性随机密码（再取即 410 BOOTSTRAP_SHOWN）。 */
export function fetchBootstrapPassword(): Promise<BootstrapResponse> {
  return request<BootstrapResponse>('/api/auth/bootstrap', { silent: true })
}

export function changePassword(oldPassword: string, newPassword: string): Promise<OkResponse> {
  return request<OkResponse>('/api/admin/password', {
    method: 'PUT',
    body: { old_password: oldPassword, new_password: newPassword },
    silent: true,
  })
}
