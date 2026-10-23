/**
 * useCountUp 契约护栏（UI全量改造方案 §6（原《UI界面设计方案》） §11 AB-2；审查 🟡 补测）。
 *
 * 锁定语义：
 * - **首帧不占「一次性」名额**：immediate 首触只落初始值——否则目标数据后到就不播了；
 * - **一次性**：目标值首次变化滚动到位（400ms easeOutCubic），此后变化直切不重放
 *   （15s 轮询叠加 count-up 会造成数字持续跳动）；
 * - **prefers-reduced-motion: reduce** 时任何变化都直切，零插值。
 *
 * 动画用真实 rAF + 450ms 等待落点断言（时长 400ms）；直切场景 await nextTick 即可断言。
 */

import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref, type Ref } from 'vue'

import { useCountUp } from '@/composables/useCountUp'

/** 在独立 effectScope 中挂 composable（onScopeDispose 依赖 scope，无组件上下文时用它）。 */
function setup(target: Ref<number>) {
  const scope = effectScope()
  let display!: Ref<number>
  scope.run(() => {
    display = useCountUp(target)
  })
  return {
    display,
    dispose: () => scope.stop(),
  }
}

describe('useCountUp', () => {
  it('首帧 immediate 只落值，不占「一次性」名额；首次数据到位仍会动画到落点', async () => {
    const target = ref(0)
    const { display, dispose } = setup(target)
    expect(display.value).toBe(0) // 首帧直落，未动画

    target.value = 100 // 数据到位 → 唯一一次动画
    await nextTick()
    expect(display.value).toBeLessThan(100) // 动画进行中（未直切到终值）
    await new Promise((r) => setTimeout(r, 500)) // DURATION_MS=400 + 余量
    expect(display.value).toBe(100)
    dispose()
  })

  it('一次性：第二次变化直切不重放动画', async () => {
    const target = ref(0)
    const { display, dispose } = setup(target)
    target.value = 10
    await new Promise((r) => setTimeout(r, 500)) // 烧掉唯一一次动画
    expect(display.value).toBe(10)

    target.value = 99
    await nextTick()
    expect(display.value).toBe(99) // 立即到位，无 400ms 插值
    dispose()
  })

  it('prefers-reduced-motion: reduce → 变化直切，零插值', async () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList)
    const target = ref(0)
    const { display, dispose } = setup(target)
    target.value = 42
    await nextTick()
    expect(display.value).toBe(42)
    dispose()
    vi.mocked(window.matchMedia).mockRestore()
  })
})
