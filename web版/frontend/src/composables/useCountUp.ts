/**
 * KPI 数字 count-up（UI全量改造方案 §6（原《UI界面设计方案》） §7 / §11 AB-2，P4 仪表盘批次交付）。
 *
 * 语义（AB-2 边界）：
 * - **一次性**：目标值首次到位时滚动到位；此后轮询刷新只更新目标值、**不重放动画**
 *   （15s 轮询叠加 count-up 会造成数字持续跳动的视觉噪声）；
 * - `prefers-reduced-motion` 时直切，不做任何插值；
 * - 不阻塞首屏：动画在 rAF 中进行，首帧直接渲染起始值。
 */

import { onScopeDispose, ref, watch, type Ref } from 'vue'

/** 滚动时长（方案 §7：页面级动效 250–400ms 量级，取 400ms 稳重档）。 */
const DURATION_MS = 400

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/** easeOutCubic：先快后慢，落点稳。 */
function ease(p: number): number {
  return 1 - (1 - p) ** 3
}

/**
 * @param target 目标值（响应式）；初始为 0/占位、数据到位后触发首次动画
 * @returns 展示值（响应式）；直接渲染，调用方自行格式化
 */
export function useCountUp(target: Ref<number>): Ref<number> {
  const display = ref(target.value)
  const played = ref(false)
  /** 首帧（immediate 触发）只落初始值、不占「一次性」名额——否则数据后到就不播了。 */
  let firstRun = true
  let raf = 0

  function stop(): void {
    if (raf) {
      cancelAnimationFrame(raf)
      raf = 0
    }
  }

  watch(
    target,
    (to) => {
      stop()
      if (firstRun) {
        firstRun = false
        display.value = to
        return
      }
      if (played.value || prefersReducedMotion()) {
        display.value = to
        return
      }
      played.value = true
      const from = display.value
      if (from === to) {
        return
      }
      const start = performance.now()
      const tick = (now: number): void => {
        const p = Math.min(1, (now - start) / DURATION_MS)
        display.value = Math.round(from + (to - from) * ease(p))
        if (p < 1) {
          raf = requestAnimationFrame(tick)
        } else {
          raf = 0
        }
      }
      raf = requestAnimationFrame(tick)
    },
    { immediate: true },
  )
  onScopeDispose(stop)

  return display
}
