/**
 * 随机密码生成（**项目唯一生成器**，S-4 单一实现）。
 *
 * 2026-10-27 合并：此前有两个生成器——本文件（32 位、含易混字符 `0O1lI()`）与
 * `utils/passwordStrength.ts` 的 `generateStrongPassword`（24 位、剔除易混字符）。
 * 两套并存导致「同一个系统里生成的密码位数/字符集不一致」，用户手抄时还容易看错。
 * 现统一为本实现，全站生成密码的位数恒为 {@link PASSWORD_LENGTH}（32）。
 *
 * 设计取舍：
 * - **客户端生成**：密码不额外经服务端流转（提交路径与手填一致，不多开暴露面）；
 * - **Web Crypto**：`crypto.getRandomValues` 取 CSPRNG，不用 `Math.random`（可预测）；
 * - **剔除易混字符**：`0/O`、`1/l/I` 不参与生成——密码要手抄到纸上/密码管理器，
 *   形近字符是实际出错源；
 * - **每类至少取一个再整体洗牌**：纯随机 32 位有极小概率漏掉某一类（符号类占比小），
 *   会让「生成即强档」变成偶发不达标（强度条掉档、测试 flaky）；
 * - **拒绝采样**：`rand % len` 会产生模偏置，越界值丢弃重采（期望重试 <1 次）。
 */

/** 默认生成长度（全站统一；后端 `constants.REPO_PASSWORD_LENGTH` 必须同值）。 */
export const PASSWORD_LENGTH = 32

/** 四类字符集，均剔除易混淆字符（0/O、1/l/I）。顺序不影响结果（会洗牌）。 */
export const PASSWORD_CHAR_CLASSES: readonly string[] = [
  'abcdefghjkmnpqrstuvwxyz',
  'ABCDEFGHJKMNPQRSTUVWXYZ',
  '23456789',
  '!@#$%^&*-_=+',
]

/** 默认字符集全集（四类拼接）。 */
export const PASSWORD_CHARS = PASSWORD_CHAR_CLASSES.join('')

/** 无偏随机下标（rejection sampling）：`getRandomValues % bound` 在 2^32 不整除 bound
 *  时头部下标命中率略高——偏差极小，但拒绝采样代价几乎为零，直接做对。 */
function randomIndex(bound: number): number {
  if (bound <= 1) {
    return 0
  }
  const cryptoObj = globalThis.crypto
  if (!cryptoObj?.getRandomValues) {
    // 非安全上下文（http 直连且非 localhost）无 Web Crypto——明确报错而非退回弱随机
    throw new Error('当前环境不支持 Web Crypto（getRandomValues），无法生成安全随机密码')
  }
  const buf = new Uint32Array(1)
  const limit = Math.floor(0x100000000 / bound) * bound
  for (;;) {
    cryptoObj.getRandomValues(buf)
    const value = buf[0] ?? 0
    if (value < limit) {
      return value % bound
    }
  }
}

/**
 * 生成随机密码。
 *
 * @param length 长度（默认 {@link PASSWORD_LENGTH}；`<=0` 返回空串）。
 * @param chars 自定义字符集（省略 = 默认四类字符集，此时**保证每类至少一个**；
 *              显式传入则按其内容纯采样，不再做类别保证——单测用）。
 * @returns 由 CSPRNG 采样的密码串。
 */
export function generatePassword(length: number = PASSWORD_LENGTH, chars?: string): string {
  const pool = chars ?? PASSWORD_CHARS
  if (length <= 0 || pool.length === 0) {
    return ''
  }

  const out: string[] = []
  if (chars === undefined) {
    // 默认字符集：每类先取一个（length 很小时按类轮取，不超长）
    for (let i = 0; i < Math.min(length, PASSWORD_CHAR_CLASSES.length); i += 1) {
      const cls = PASSWORD_CHAR_CLASSES[i]!
      out.push(cls[randomIndex(cls.length)]!)
    }
  }
  while (out.length < length) {
    out.push(pool[randomIndex(pool.length)]!)
  }

  // Fisher-Yates 洗牌：把「每类至少一个」的前置字符打散，避免开头恒为固定类别
  // （不能用 sort(() => Math.random() - 0.5)，分布不均匀）
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = randomIndex(i + 1)
    const tmp = out[i]!
    out[i] = out[j]!
    out[j] = tmp
  }
  return out.join('')
}
