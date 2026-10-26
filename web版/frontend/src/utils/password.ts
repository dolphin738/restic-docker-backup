/**
 * 随机密码生成（rclone 配置生成工具：Crypt/WebDAV 密码字段的「生成」按钮）。
 *
 * 设计取舍：
 * - **客户端生成**：密码不额外经服务端流转（提交路径与手填一致，不多开暴露面），
 *   也无需后端端点；
 * - **Web Crypto**：`crypto.getRandomValues` 取 CSPRNG，不用 `Math.random`（可预测）；
 * - **掩码采样**：字符集 62 项时用 6 bit 掩码（64 槽）采样并**丢弃越界值**——
 *   直接 `rand % len` 会产生模偏置（前 2 个字符概率偏高）。
 */

/** 默认字符集：大小写字母 + 数字 + 安全符号（排除引号/反斜杠/空白/INI 敏感字符）。 */
export const PASSWORD_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()-_=+'

/** rclone crypt 密码默认长度（裁决 5：32 位）。 */
export const PASSWORD_LENGTH = 32

/**
 * 生成随机密码。
 *
 * @param length 长度（默认 32）。
 * @param chars 字符集（默认 {@link PASSWORD_CHARS}）。
 * @returns 由 CSPRNG 采样的密码串。
 */
export function generatePassword(length: number = PASSWORD_LENGTH, chars: string = PASSWORD_CHARS): string {
  if (length <= 0 || chars.length === 0) {
    return ''
  }

  const cryptoObj = globalThis.crypto
  if (!cryptoObj?.getRandomValues) {
    // 非安全上下文（http 直连且非 localhost）无 Web Crypto——明确报错而非退回弱随机
    throw new Error('当前环境不支持 Web Crypto（getRandomValues），无法生成安全随机密码')
  }

  // 拒绝采样：只接受 [0, accept) 区间的字节（accept 是字符集长度的最大整数倍），
  // 越界即丢弃重采——保证每个字符等概率，无模偏置。
  const accept = Math.floor(256 / chars.length) * chars.length
  const out: string[] = []
  const buf = new Uint8Array(Math.max(1, length * 2))
  while (out.length < length) {
    cryptoObj.getRandomValues(buf)
    for (const byte of buf) {
      if (byte >= accept) {
        continue
      }
      out.push(chars[byte % chars.length]!)
      if (out.length === length) {
        break
      }
    }
  }
  return out.join('')
}
