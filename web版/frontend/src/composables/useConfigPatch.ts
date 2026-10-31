/**
 * 配置卡的保存链（候选 6 方案 B）：把「拼 patch → 提交 → 提示 → 刷新 → loading」收成一处。
 *
 * 设置页此前每张配置卡手抄这五步（dump/behavior/credential/metrics/timezone/system 各一遍），
 * 六份副本之间已经漂移过：有的记得在 submit 前判 dirty，有的忘了 loading 复位。
 *
 * **刻意的边界**：只收「保存动作」这一条链。**不收**回填、不收校验、不收控件渲染——
 * 6 张卡的字段语义差异太大（密码三态、旧键回退、env 锁定粒度各不相同），
 * 把它们的差异塞进一个 schema 会造出一门只有 6 个消费者的小语言。
 * 实测结论见 docs/讨论记录：schema 方案净增 280 行且 6 张卡里 5 张要逃生钩子。
 */

import { ref, type Ref } from 'vue'

/** 提交函数签名（E07 patch 通道）。 */
export type ConfigSubmit = (patch: Record<string, string | null>) => Promise<unknown>

export interface UseConfigPatchOptions {
  /** 提交函数，通常是 ``saveDumpBehaviorConfig``。 */
  submit: ConfigSubmit
  /** 成功提示文案（已 i18n 化）。 */
  successMessage: () => string
  /** 成功提示出口，通常是 ``useMessage().success``。 */
  notify: (text: string) => void
  /** 提交后刷新配置查询。 */
  refetch: () => Promise<unknown>
  /** 保存成功后的副作用（如时区保存后改全站展示时区 + 刷新时间快照）。 */
  afterSave?: () => void | Promise<void>
  /** 提交前是否要求「有改动」（metrics 走 dirty 门，其余卡靠 disabled 门）。 */
  guard?: () => boolean
}

export interface ConfigPatchRuntime {
  /** 保存是否进行中（绑 ``:loading``）。 */
  saving: Ref<boolean>
  /**
   * 执行一次保存。
   *
   * @param patch 键值补丁（``null`` = 清除该键，``''`` = 写入空串——两者语义不同，见 dump 密码）。
   * @returns 是否真的提交并成功（被 guard 拦下或抛错均为 false）。
   */
  save: (patch: Record<string, string | null>) => Promise<boolean>
}

/**
 * 组装一条保存链。
 *
 * 错误（422 键级校验 / ENV_OVERRIDDEN）**不在这里提示**——由 ``api/http.ts`` 的全局拦截器
 * 统一弹出，页面只负责成功路径与 loading 复位。这是全站既有口径，勿在本模块重复提示。
 */
export function useConfigPatch(options: UseConfigPatchOptions): ConfigPatchRuntime {
  const saving = ref(false)

  async function save(patch: Record<string, string | null>): Promise<boolean> {
    if (saving.value) {
      return false
    }
    if (options.guard && !options.guard()) {
      return false
    }
    saving.value = true
    try {
      await options.submit(patch)
      options.notify(options.successMessage())
      await options.refetch()
      await options.afterSave?.()
      return true
    } catch {
      // 错误由 http 层全局提示；这里只保证 loading 复位
      return false
    } finally {
      saving.value = false
    }
  }

  return { saving, save }
}
