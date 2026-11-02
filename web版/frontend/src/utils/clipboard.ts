/**
 * 剪贴板复制（全站统一口径：clipboard API + 成败 toast——success/copied、warning/copyFailed）。
 *
 * 单一实现点（S-4）：ConfirmGate（关键词复制）、SecretReveal（一次性明文）、
 * RcloneConfigPanel（找回命令示例）三处原本各写一份相同的 try/catch 样板，收口到此。
 * message 由调用方注入（useMessage 实例）；失败提示是否静默由调用方决定——
 * bootstrap 引导页刻意静默（密码仍可见可手动复制），不经本函数。
 */
export async function copyWithNotify(
  text: string,
  message: { success: (content: string) => void; warning: (content: string) => void },
  copiedKey: string,
  copyFailedKey: string,
): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    message.success(copiedKey)
  } catch {
    message.warning(copyFailedKey)
  }
}
