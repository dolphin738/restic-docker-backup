/**
 * 口令强度估算（G4：导出口令强度条的数据源；纯函数、无副作用、可单测）。
 *
 * 口径说明（刻意保守，不做「强度打分 API」式承诺）：
 * 只统计**可本地判定**的客观特征——长度与字符类别数；不查常见口令字典
 * （前端无字典、也不该把用户口令送去任何服务）。因此它回答的是「这个口令有多难被
 * 暴力/规则猜测」，而非「这个口令一定安全」。导出的最低门槛（≥10 位）由业务校验负责，
 * 本函数只做可视化分级。
 */

/** 0 = 太短（不可用）；1–4 = 弱 → 强。 */
export type StrengthScore = 0 | 1 | 2 | 3 | 4

export interface PasswordStrength {
  score: StrengthScore
  /** 命中的字符类别数（小写 / 大写 / 数字 / 符号，各计 1）。 */
  classes: number
  length: number
}

export function estimatePasswordStrength(password: string): PasswordStrength {
  const length = password.length
  if (length === 0) {
    return { score: 0, classes: 0, length }
  }

  const classes =
    (/[a-z]/.test(password) ? 1 : 0) +
    (/[A-Z]/.test(password) ? 1 : 0) +
    (/\d/.test(password) ? 1 : 0) +
    (/[^A-Za-z0-9]/.test(password) ? 1 : 0)

  if (length < 10) {
    // 未达导出最低门槛：一律计 0（长度是决定性因素，类别再多也不放行）
    return { score: 0, classes, length }
  }

  // 已达标：长度 10–13 → 基线 1；≥14 → 基线 2；再按字符类别叠加，封顶 4
  let score = length >= 14 ? 2 : 1
  if (classes >= 3) {
    score += 1
  }
  if (classes === 4 && length >= 12) {
    score += 1
  }
  return { score: Math.min(4, score) as StrengthScore, classes, length }
}

/** 字符集（剔除易混淆字符 0/O、1/l/I）：小写 / 大写 / 数字 / 符号四类。 */
const CHAR_CLASSES = [
  'abcdefghjkmnpqrstuvwxyz',
  'ABCDEFGHJKMNPQRSTUVWXYZ',
  '23456789',
  '!@#$%^&*-_=+',
]
const ALL_CHARS = CHAR_CLASSES.join('')

function randomChar(alphabet: string): string {
  const buffer = new Uint32Array(1)
  crypto.getRandomValues(buffer)
  return alphabet[buffer[0] % alphabet.length]
}

/** 生成强随机口令（G4：与强度条配套的「生成」按钮）。
 *
 * 两条硬要求决定了实现方式：
 * - 随机源必须是 `crypto.getRandomValues`（Math.random 不可用于凭据）；
 * - **每类字符至少取一个再整体洗牌**——纯随机取 20 位有约百分之几的概率漏掉某一类
 *   （符号类字符占比小），会让「生成即强档」变成偶发不达标（测试也会 flaky）。
 *   洗牌用 Fisher-Yates（不是 `sort(() => Math.random() - 0.5)`，后者分布不均匀）。
 */
export function generateStrongPassword(length = 20): string {
  const size = Math.max(length, CHAR_CLASSES.length)
  const chars: string[] = CHAR_CLASSES.map((cls) => randomChar(cls))
  while (chars.length < size) {
    chars.push(randomChar(ALL_CHARS))
  }
  for (let i = chars.length - 1; i > 0; i -= 1) {
    const buffer = new Uint32Array(1)
    crypto.getRandomValues(buffer)
    const j = buffer[0] % (i + 1)
    const tmp = chars[i]
    chars[i] = chars[j]
    chars[j] = tmp
  }
  return chars.join('')
}
