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

/** 字符类别数（小写 / 大写 / 数字 / 符号，各计 1）——类别定义的唯一出处；
 *  estimatePasswordStrength 与业务侧准入校验（bootstrap strengthOk）共用，
 *  改类别口径只改这一处。 */
export function countCharClasses(password: string): number {
  return (
    (/[a-z]/.test(password) ? 1 : 0) +
    (/[A-Z]/.test(password) ? 1 : 0) +
    (/\d/.test(password) ? 1 : 0) +
    (/[^A-Za-z0-9]/.test(password) ? 1 : 0)
  )
}

export function estimatePasswordStrength(password: string): PasswordStrength {
  const length = password.length
  if (length === 0) {
    return { score: 0, classes: 0, length }
  }

  const classes = countCharClasses(password)

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
/*
 * 生成器已收口（2026-10-27）：`generateStrongPassword` / `REPO_PASSWORD_LENGTH` /
 * 私有字符集全部删除——全站生成密码统一走 `utils/password.ts` 的
 * `generatePassword()`（32 位、四类字符剔除易混、每类至少一个 + Fisher-Yates）。
 * 本文件只保留「强度估算」这一件事（纯函数，供导出口令强度条使用）。
 */
