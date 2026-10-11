/**
 * useJobStream 单元测试（规范 §3.2 G7：改本 composable 必须有 mock EventSource 测试）。
 *
 * 本文件承载断点续传的全部正确性却长期零测试，故本次补齐。重点覆盖：
 * - 游标只前进不回退（事件 id == 日志行号，直接作为 Last-Event-ID）；
 * - 开流前先回查 E14 取起始游标，避免全量回放；
 * - 心跳超时 → close → 回查 → 仍活跃则按游标续传（而非从头开流）；
 * - 终态（done / error 带 data）立即 close，防止浏览器自动重连；
 * - 断链重试的指数退避。
 *
 * mock 要点：happy-dom 不提供 EventSource，故用最小替身记录构造参数与监听器，
 * 由测试显式触发事件——这样能断言「续传时带了 last_event_id」这类外部可观测行为。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import { useJobStream } from '@/composables/useJobStream'
import { AppError } from '@/api/http'
import * as jobsApi from '@/api/jobs'

// ---------------------------------------------------------------- EventSource 替身

interface Listener {
  (raw: unknown): void
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2

  readonly url: string
  readonly withCredentials: boolean | undefined
  readonly listeners = new Map<string, Listener[]>()
  readyState = FakeEventSource.CONNECTING
  closed = false

  constructor(url: string, init?: EventSourceInit) {
    this.url = url
    this.withCredentials = init?.withCredentials
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: Listener): void {
    const bucket = this.listeners.get(type) ?? []
    bucket.push(fn)
    this.listeners.set(type, bucket)
  }

  close(): void {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  /** 触发一个带 lastEventId 的 MessageEvent（服务端格式：id 行号 + event 名 + data）。 */
  emit(type: string, data: unknown, lastEventId?: string): void {
    const event = new MessageEvent(type, {
      data: JSON.stringify(data),
      lastEventId: lastEventId ?? '',
    })
    for (const fn of this.listeners.get(type) ?? []) {
      fn(event)
    }
  }

  /** 触发连接层 error（非 MessageEvent）。 */
  emitTransportError(): void {
    for (const fn of this.listeners.get('error') ?? []) {
      fn(new Event('error'))
    }
  }

  open(): void {
    this.readyState = FakeEventSource.OPEN
    for (const fn of this.listeners.get('open') ?? []) {
      fn(new Event('open'))
    }
  }

  /** 从 URL 上取出 last_event_id 查询参数（续传断言用）。 */
  get cursor(): string | null {
    const match = /[?&]last_event_id=([^&]*)/.exec(this.url)
    return match ? match[1]! : null
  }
}

// 全局注入替身（happy-dom 无 EventSource）
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource

// 静态成员在类体外无法直接访问，这里补上（EventSource 规范要求的三常量）
Object.assign(FakeEventSource, {
  CONNECTING: 0,
  OPEN: 1,
  CLOSED: 2,
})

const latest = (): FakeEventSource => {
  const es = FakeEventSource.instances[FakeEventSource.instances.length - 1]
  if (!es) {
    throw new Error('尚未创建 EventSource')
  }
  return es
}

function jobFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-1',
    type: 'backup',
    status: 'running',
    trigger: 'manual',
    last_event_id: 0,
    exit_code: null,
    snapshot_id: null,
    skipped_count: 0,
    bytes_done: null,
    files_done: null,
    summary: null,
    ...overrides,
  }
}

describe('useJobStream', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.spyOn(jobsApi, 'getJob')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  // ---------------------------------------------------------------- 游标语义

  it('事件游标只前进不回退，忽略非法的 lastEventId', async () => {
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture() as never)
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { autoStart: false })
    })

    await handle.start()
    const es = latest()
    es.open()

    es.emit('progress', { percent_done: 0.5 }, '10')
    expect(handle.lastEventId.value).toBe(10)

    // 乱序到达（网络重排）不应让游标倒退
    es.emit('progress', { percent_done: 0.6 }, '4')
    expect(handle.lastEventId.value).toBe(10)

    // 前进
    es.emit('progress', { percent_done: 0.7 }, '11')
    expect(handle.lastEventId.value).toBe(11)

    // 非数字 lastEventId 应被忽略
    es.emit('progress', { percent_done: 0.8 }, 'not-a-number')
    expect(handle.lastEventId.value).toBe(11)

    scope.stop()
  })

  // ---------------------------------------------------------------- 起始游标

  it('开流前先回查 E14 取起始游标，首帧即带上，避免全量回放', async () => {
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture({ last_event_id: 42 }) as never)
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { autoStart: false })
    })

    await handle.start()

    expect(jobsApi.getJob).toHaveBeenCalledWith('job-1')
    // 首帧就带游标 → 服务端从第 42 行续传，而非从 0 回放
    expect(latest().cursor).toBe('42')
    expect(handle.lastEventId.value).toBe(42)

    scope.stop()
  })

  it('任务已终态时不开流，直接本地收尾', async () => {
    const onDone = vi.fn()
    vi.mocked(jobsApi.getJob).mockResolvedValue(
      jobFixture({ status: 'success', exit_code: 0, last_event_id: 7 }) as never,
    )
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { onDone, autoStart: false })
    })

    await handle.start()

    expect(FakeEventSource.instances).toHaveLength(0)
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(onDone.mock.calls[0]![0]).toMatchObject({ status: 'success', exit_code: 0 })

    scope.stop()
  })

  // ---------------------------------------------------------------- 终态收尾

  it('收到 done 立即关闭连接（防止浏览器自动重连）', async () => {
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture() as never)
    const onDone = vi.fn()
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { onDone, autoStart: false })
    })

    await handle.start()
    const es = latest()
    es.open()
    expect(es.closed).toBe(false)

    es.emit('done', { status: 'success', exit_code: 0 }, '12')

    expect(onDone).toHaveBeenCalledTimes(1)
    expect(es.closed).toBe(true)
    expect(handle.state.value).not.toBe('open')

    scope.stop()
  })

  it('终态 failed 走 error 事件（带 data 的 MessageEvent），与传输层 error 区分', async () => {
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture() as never)
    const onFailed = vi.fn()
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { onFailed, autoStart: false })
    })

    await handle.start()
    const es = latest()
    es.open()

    // 服务端终态 failed：error 事件但携带 data
    es.emit('error', { status: 'failed', exit_code: 2, summary: 'boom' }, '20')

    expect(onFailed).toHaveBeenCalledTimes(1)
    expect(onFailed.mock.calls[0]![0]).toMatchObject({ status: 'failed', exit_code: 2 })
    expect(es.closed).toBe(true)

    scope.stop()
  })

  // ---------------------------------------------------------------- 心跳与续传

  it('心跳超时后回查任务：仍活跃则按 Last-Event-ID 续传，而非从头开流', async () => {
    vi.useFakeTimers()
    vi.mocked(jobsApi.getJob)
      .mockResolvedValueOnce(jobFixture({ last_event_id: 5 }) as never)
      // 心跳超时后的回查：任务仍在跑，服务端已推进到第 30 行
      .mockResolvedValueOnce(jobFixture({ last_event_id: 30 }) as never)

    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { heartbeatTimeoutMs: 1000, autoStart: false })
    })

    await handle.start()
    const first = latest()
    expect(first.cursor).toBe('5')
    first.open() // 触发 open 事件 → armHeartbeat 武装心跳定时器

    // 触发心跳超时
    await vi.advanceTimersByTimeAsync(1500)

    // 应新建连接，且携带服务端已推进的游标
    expect(FakeEventSource.instances.length).toBeGreaterThan(1)
    expect(first.closed).toBe(true)
    const second = latest()
    expect(second.cursor).toBe('30')
    expect(handle.state.value).toBe('reconnecting')

    scope.stop()
  })

  it('心跳超时回查发现已终态 → 收尾且不再开新流', async () => {
    vi.useFakeTimers()
    const onDone = vi.fn()
    vi.mocked(jobsApi.getJob)
      .mockResolvedValueOnce(jobFixture({ last_event_id: 3 }) as never)
      .mockResolvedValueOnce(jobFixture({ status: 'success', exit_code: 0, last_event_id: 9 }) as never)

    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { onDone, heartbeatTimeoutMs: 800, autoStart: false })
    })

    await handle.start()
    latest().open() // 武装心跳定时器（无 open 则心跳不启动）
    const countBefore = FakeEventSource.instances.length

    await vi.advanceTimersByTimeAsync(1200)

    expect(onDone).toHaveBeenCalledTimes(1)
    // 终态下不应再开新流
    expect(FakeEventSource.instances.length).toBe(countBefore)

    scope.stop()
  })

  it('心跳超时回查 404 → 以 interrupted 收尾（走 onFailed 而非 onDone）', async () => {
    vi.useFakeTimers()
    const onDone = vi.fn()
    const onFailed = vi.fn()
    vi.mocked(jobsApi.getJob)
      .mockResolvedValueOnce(jobFixture() as never)
      .mockRejectedValueOnce(new AppError('NOT_FOUND', '任务不存在', 404) as never)

    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), {
        onDone,
        onFailed,
        heartbeatTimeoutMs: 600,
        autoStart: false,
      })
    })

    await handle.start()
    latest().open() // 武装心跳定时器
    await vi.advanceTimersByTimeAsync(900)

    // finishTerminal 按 status 分发：failed/interrupted → onFailed，其余 → onDone。
    // interrupted 属"非正常终止"，故应触发 onFailed。
    expect(onFailed).toHaveBeenCalledTimes(1)
    expect(onFailed.mock.calls[0]![0]).toMatchObject({ status: 'interrupted' })
    expect(onDone).not.toHaveBeenCalled()
    expect(handle.state.value).toBe('closed')

    scope.stop()
  })

  // ---------------------------------------------------------------- 重试退避

  it('连接层 error 且连接已 CLOSED → 指数退避重试（1s → 2s）', async () => {
    vi.useFakeTimers()
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture({ last_event_id: 1 }) as never)

    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { autoStart: false })
    })

    await handle.start()
    const countBefore = FakeEventSource.instances.length

    // 传输层 error 且 readyState=CLOSED
    latest().readyState = FakeEventSource.CLOSED
    latest().emitTransportError()

    // 首次退避 1s —— 注意不能用 runAllTimersAsync：它会强制跑完所有待定定时器，
    // 那样第二次退避也会被立即触发，退避语义就测不出来了。
    await vi.advanceTimersByTimeAsync(1100)
    expect(FakeEventSource.instances.length).toBe(countBefore + 1)

    // 第二次退避应延长到 2s：只推进 1s 时不应触发
    const countAfterFirst = FakeEventSource.instances.length
    latest().readyState = FakeEventSource.CLOSED
    latest().emitTransportError()
    await vi.advanceTimersByTimeAsync(1100)
    expect(FakeEventSource.instances.length).toBe(countAfterFirst)

    await vi.advanceTimersByTimeAsync(1000)
    expect(FakeEventSource.instances.length).toBe(countAfterFirst + 1)

    scope.stop()
  })

  // ---------------------------------------------------------------- 日志缓冲

  it('日志按 200ms 节流整体替换快照，且受缓冲上限约束', async () => {
    vi.useFakeTimers()
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture() as never)
    const onLog = vi.fn()
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { onLog, maxBufferedLines: 3, autoStart: false })
    })

    await handle.start()
    const es = latest()
    es.open()

    // 连发 5 条（超过上限 3）
    for (let i = 1; i <= 5; i++) {
      es.emit('log', { text: `line-${i}` }, String(i))
    }
    // 回调逐条即时触发
    expect(onLog).toHaveBeenCalledTimes(5)
    // 快照尚未刷新（节流窗口内）
    expect(handle.logs.value).toHaveLength(0)

    await vi.advanceTimersByTimeAsync(250)
    // 只保留最近 3 条
    expect(handle.logs.value).toHaveLength(3)
    expect(handle.logs.value[2]).toContain('line-5')

    scope.stop()
  })

  it('stop 后不再开流，且状态回到 idle', async () => {
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture() as never)
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { autoStart: false })
    })

    await handle.start()
    const es = latest()
    es.open()

    handle.stop()
    expect(es.closed).toBe(true)
    expect(handle.state.value).toBe('idle')

    scope.stop()
  })

  it('autoStart:false 时不自动连接，需显式 start', async () => {
    vi.mocked(jobsApi.getJob).mockResolvedValue(jobFixture() as never)
    const scope = effectScope()
    let handle!: ReturnType<typeof useJobStream>
    scope.run(() => {
      handle = useJobStream(ref('job-1'), { autoStart: false })
    })

    await nextTick()
    expect(FakeEventSource.instances).toHaveLength(0)

    await handle.start()
    expect(FakeEventSource.instances.length).toBe(1)

    scope.stop()
  })
})
