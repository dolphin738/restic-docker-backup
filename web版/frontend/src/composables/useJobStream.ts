/** 任务 SSE 订阅层（§5.1 E15）：日志回放 + 追加续流 + 心跳判定 + 按游标续传。
 *
 * 关键约定：
 * - 仅 `new EventSource('/api/jobs/{id}/stream')`，靠 cookie 鉴权，**不拼 query token**；
 * - 事件 id == 日志行号，续传只带 `?last_event_id=`（浏览器重连时自动带 Last-Event-ID 头）；
 * - 服务端 15s 发一次 `: keep-alive` 注释行，但**注释行不会触发任何 EventSource 事件**，
 *   因此本层以"最后一次收到 message 的时间"判定活性：超过 heartbeatTimeoutMs（默认 45s）
 *   无任何事件 → 判定疑似断链 → close + 回查任务状态 → 仍活跃则按游标续传（最坏情况只是
 *   每 45s 静默期多做一次无害重连）；
 * - 收到 `done`/`error` 终态后立即 close：否则浏览器会带着 Last-Event-ID 无限自动重连，
 *   而服务端重放无新行后又判定终态会再补发一次 done → 无限重连循环；
 * - **开流前先 `GET /api/jobs/{id}` 取 `last_event_id` 作为起始游标**（E14 响应自带该字段）：
 *   任务已终态则根本不开流，直接本地收尾；进行中则只收增量，避免挂载/刷新时整份日志回放。
 */

import { onScopeDispose, ref, shallowRef, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'
import { hasCode } from '@/api/http'
import { getJob } from '@/api/jobs'
import type { JobDonePayload, JobLogPayload, JobOut, JobProgressPayload } from '@/api/types'

export type JobStreamState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

const TERMINAL_STATUSES: readonly string[] = ['success', 'failed', 'interrupted', 'cancelled']
const DEFAULT_HEARTBEAT_TIMEOUT_MS = 45_000
const DEFAULT_BUFFER_LINES = 500
const MAX_RETRY_DELAY_MS = 15_000
const FLUSH_INTERVAL_MS = 200

export interface JobStreamCallbacks {
  onProgress?: (payload: JobProgressPayload) => void
  onLog?: (payload: JobLogPayload) => void
  onDone?: (payload: JobDonePayload) => void
  onFailed?: (payload: JobDonePayload) => void
}

export interface JobStreamOptions extends JobStreamCallbacks {
  /** 超过该时间无任何事件即判定疑似断链（默认 45s）。 */
  heartbeatTimeoutMs?: number
  /** 日志缓冲行数上限（超出丢弃最旧行）。 */
  maxBufferedLines?: number
  /** 拿到 jobId 后自动连接，默认 true。 */
  autoStart?: boolean
}

export interface JobStreamHandle {
  state: Ref<JobStreamState>
  lastEventId: Ref<number>
  /** 只读的日志快照（每 200ms 整体替换，不进入深层响应式树）。 */
  logs: Ref<readonly string[]>
  /** 回查起始游标后开流（已终态则不开流，直接本地收尾）。 */
  start: () => Promise<void>
  stop: () => void
}

function payloadFromEvent(event: MessageEvent): unknown {
  try {
    return JSON.parse(event.data as string) as unknown
  } catch {
    return null
  }
}

function toLine(payload: JobLogPayload): string {
  if (typeof payload.text === 'string') {
    return payload.text
  }
  return payload.path ?? payload.name ?? ''
}

export function useJobStream(
  jobId: MaybeRefOrGetter<string>,
  options: JobStreamOptions = {},
): JobStreamHandle {
  const heartbeatTimeoutMs = options.heartbeatTimeoutMs ?? DEFAULT_HEARTBEAT_TIMEOUT_MS
  const maxBufferedLines = options.maxBufferedLines ?? DEFAULT_BUFFER_LINES

  const state = ref<JobStreamState>('idle')
  const lastEventId = ref(0)
  const logs = shallowRef<readonly string[]>([])

  // 非响应式缓冲：SSE 高频写入不触发依赖
  let buffer: string[] = []
  let source: EventSource | null = null
  let flushTimer: number | null = null
  let heartbeatTimer: number | null = null
  let retryTimer: number | null = null
  let retryDelay = 1_000
  let terminated = false
  let disposed = false

  function scheduleFlush(): void {
    if (flushTimer !== null) {
      return
    }
    flushTimer = window.setTimeout(() => {
      flushTimer = null
      logs.value = buffer.slice()
    }, FLUSH_INTERVAL_MS)
  }

  /** 事件 id == 日志行号，直接作为续传游标（只前进不回退）。 */
  function trackCursor(event: MessageEvent): void {
    if (!event.lastEventId) {
      return
    }
    const value = Number(event.lastEventId)
    if (Number.isFinite(value) && value > lastEventId.value) {
      lastEventId.value = value
    }
  }

  function pushLog(line: string): void {
    if (!line) {
      return
    }
    buffer.push(line)
    if (buffer.length > maxBufferedLines) {
      buffer = buffer.slice(buffer.length - maxBufferedLines)
    }
    scheduleFlush()
  }

  function clearTimers(): void {
    if (heartbeatTimer !== null) {
      window.clearTimeout(heartbeatTimer)
      heartbeatTimer = null
    }
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  function armHeartbeat(): void {
    if (heartbeatTimer !== null) {
      window.clearTimeout(heartbeatTimer)
    }
    heartbeatTimer = window.setTimeout(() => {
      heartbeatTimer = null
      void recoverFromStall()
    }, heartbeatTimeoutMs)
  }

  function closeSource(): void {
    if (source) {
      source.close()
      source = null
    }
  }

  function stop(): void {
    clearTimers()
    closeSource()
    if (!terminated) {
      state.value = 'idle'
    }
  }

  function reset(): void {
    clearTimers()
    closeSource()
    terminated = false
    retryDelay = 1_000
    buffer = []
    lastEventId.value = 0
    logs.value = []
    state.value = 'idle'
  }

  function finishTerminal(payload: JobDonePayload): void {
    terminated = true
    clearTimers()
    closeSource()
    state.value = 'closed'
    if (payload.status === 'failed' || payload.status === 'interrupted') {
      options.onFailed?.(payload)
    } else {
      options.onDone?.(payload)
    }
  }

  function fromJobOut(job: JobOut): JobDonePayload {
    return {
      status: job.status,
      exit_code: job.exit_code,
      snapshot_id: job.snapshot_id,
      skipped_count: job.skipped_count,
      bytes_done: job.bytes_done,
      files_done: job.files_done,
      summary: job.summary,
    }
  }

  /** 心跳超时：close → 回查任务状态 → 仍活跃则按 Last-Event-ID 续传。 */
  async function recoverFromStall(): Promise<void> {
    const id = toValue(jobId)
    if (!id || disposed || terminated) {
      return
    }
    closeSource()
    state.value = 'reconnecting'
    try {
      const job = await getJob(id)
      if (TERMINAL_STATUSES.includes(job.status)) {
        finishTerminal(fromJobOut(job))
        return
      }
      // 从服务端已落盘的行号续传，只收增量
      lastEventId.value = Math.max(lastEventId.value, job.last_event_id)
      open(id, true)
    } catch (error) {
      if (hasCode(error, 'NOT_FOUND')) {
        finishTerminal({
          status: 'interrupted',
          exit_code: null,
          snapshot_id: null,
          skipped_count: 0,
          bytes_done: null,
          files_done: null,
          summary: null,
        })
        return
      }
      if (hasCode(error, 'UNAUTHORIZED') || hasCode(error, 'BOOTSTRAP_REQUIRED')) {
        state.value = 'closed'
        return
      }
      scheduleRetry()
    }
  }

  function scheduleRetry(): void {
    if (disposed || terminated) {
      return
    }
    state.value = 'reconnecting'
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
    }
    retryTimer = window.setTimeout(
      () => {
        retryTimer = null
        const id = toValue(jobId)
        if (id && !disposed && !terminated) {
          open(id, true)
        }
      },
      retryDelay,
    )
    retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY_MS)
  }

  function open(id: string, resuming = false): void {
    closeSource()
    const query = lastEventId.value > 0 ? `?last_event_id=${lastEventId.value}` : ''
    const url = `/api/jobs/${encodeURIComponent(id)}/stream${query}`
    state.value = resuming ? 'reconnecting' : 'connecting'

    const es = new EventSource(url, { withCredentials: true })
    source = es

    es.addEventListener('open', () => {
      state.value = 'open'
      retryDelay = 1_000
      armHeartbeat()
    })

    es.addEventListener('progress', (raw) => {
      const event = raw as MessageEvent
      trackCursor(event)
      armHeartbeat()
      const payload = payloadFromEvent(event) as JobProgressPayload | null
      if (payload) {
        options.onProgress?.(payload)
      }
    })

    es.addEventListener('log', (raw) => {
      const event = raw as MessageEvent
      trackCursor(event)
      armHeartbeat()
      const payload = payloadFromEvent(event) as JobLogPayload | null
      if (payload) {
        pushLog(toLine(payload))
        options.onLog?.(payload)
      }
    })

    es.addEventListener('done', (raw) => {
      const event = raw as MessageEvent
      trackCursor(event)
      const payload = payloadFromEvent(event) as JobDonePayload | null
      if (payload) {
        finishTerminal(payload)
      }
    })

    // 注意：服务端 `event: error`（终态 failed/interrupted）与"连接失败"共用 error 事件名，
    // 用是否为 MessageEvent（带 data）区分。
    es.addEventListener('error', (raw) => {
      if (raw instanceof MessageEvent) {
        trackCursor(raw)
        const payload = payloadFromEvent(raw) as JobDonePayload | null
        if (payload) {
          finishTerminal(payload)
        }
        return
      }
      if (terminated || disposed) {
        return
      }
      if (es.readyState === EventSource.CLOSED) {
        scheduleRetry()
      } else {
        // 浏览器正在自动重连（会自带 Last-Event-ID），仅更新状态
        state.value = 'reconnecting'
      }
    })
  }

  /** 开流前先回查 E14：取 `last_event_id` 作起始游标，并提前识别已终态任务。 */
  async function start(): Promise<void> {
    const id = toValue(jobId)
    if (!id || disposed) {
      return
    }
    reset()
    state.value = 'connecting'
    try {
      const job = await getJob(id)
      if (disposed) {
        return
      }
      if (TERMINAL_STATUSES.includes(job.status)) {
        finishTerminal(fromJobOut(job))
        return
      }
      lastEventId.value = job.last_event_id
      open(id)
    } catch (error) {
      if (disposed) {
        return
      }
      if (hasCode(error, 'NOT_FOUND')) {
        finishTerminal({
          status: 'interrupted',
          exit_code: null,
          snapshot_id: null,
          skipped_count: 0,
          bytes_done: null,
          files_done: null,
          summary: null,
        })
        return
      }
      if (hasCode(error, 'UNAUTHORIZED') || hasCode(error, 'BOOTSTRAP_REQUIRED')) {
        state.value = 'closed'
        return
      }
      // 回查失败不阻塞订阅：退化为从头开流
      open(id)
    }
  }

  watch(
    () => toValue(jobId),
    (id, previous) => {
      if (id === previous) {
        return
      }
      reset()
      if (id && options.autoStart !== false) {
        void start()
      }
    },
    { immediate: true },
  )

  onScopeDispose(() => {
    disposed = true
    clearTimers()
    if (flushTimer !== null) {
      window.clearTimeout(flushTimer)
      flushTimer = null
    }
    closeSource()
  })

  return { state, lastEventId, logs, start, stop }
}
