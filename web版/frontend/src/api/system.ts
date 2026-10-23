/** 系统时间域数据层（E39）：服务器时间快照 / NTP 对时 / 系统时钟校准。
 *
 * 时区本身的读写走 E06/E07 config 通道（键 `timezone`，本文件不重复造接口）；
 * 这里只承担「时间展示与校准」两个端点。
 */

import { request } from './http'
import type { SystemRuntimeResponse, SystemTimeResponse, TimeSyncResponse } from './types'

/** 部署形态与浏览根（设计方案 §3.2 单一事实源）：源树根 / deny 前缀的唯一取数来源。 */
export function getRuntime(): Promise<SystemRuntimeResponse> {
  return request<SystemRuntimeResponse>('/api/system/runtime')
}

/** E39 服务器时间快照：UTC 时刻 + 生效时区本地时间 + NTP 偏差（探测 30s 服务端缓存）。 */
export function getSystemTime(): Promise<SystemTimeResponse> {
  return request<SystemTimeResponse>('/api/system/time')
}

/** E39 时间同步：按 NTP 结果校准系统时钟（有权限时）。
 * 未应用/失败时响应体仍为 200，ok=false + reason 机器码（禁止静默）。 */
export function syncSystemTime(): Promise<TimeSyncResponse> {
  return request<TimeSyncResponse>('/api/system/time/sync', { method: 'POST' })
}
