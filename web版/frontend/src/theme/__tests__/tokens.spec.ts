/**
 * P5 令牌层护栏（UI全量改造方案 §4.6.4 彻底档 / §4.7 P5）。
 *
 * 机械断言防静默漂移（与 i18n-parity / naive-imports 同思路）：
 * ① 批次数值登记——圆角 / 密度 / 标签形态 / 表格行高逐项锁定，改值必须改测试；
 * ② 亮/暗结构同批——两份 GlobalThemeOverrides 的组件级键集（含各组件内部键）必须一致，
 *    防「只调亮色」漏暗色（§4.6.5 S-3）；
 * ③ 跨文件同源——skin.css 的间距变量/工具类必须与 tokens.ts 的 spacing 常量一致；
 * ④ 阴影单一文件——三档阴影只允许定义在 skin.css（:root + .rw-dark 各一套，共 6 处）；
 * ⑤ Select 族圆角对齐——skin.css 的 --rw-radius-control 与 Input 圆角跨文件同源，
 *    且只允许命中 .n-base-selection 根选择器（不改 Naive 内部结构，§4.6.3）；
 * ⑥ P6 表面变量——普通 div 形态（行卡片/状态条/chip）取不到 naive 的 --n-* 变量
 *    （只存在于 naive 组件根），故 skin.css 自持 --rw-border/surface/surface-hover/focus
 *    亮暗各一套；--rw-border 与 tokens.ts common.borderColor 跨文件同源，形态类禁裸 --n-*。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { darkThemeOverrides, lightThemeOverrides, spacing } from '../tokens'

// 归一化换行符：skin.css 在 Windows 经 .gitattributes eol 过滤签出为 CRLF，
// 而多行 toContain 断言用 \n；归一化后跨 Windows(CRLF)/WSL(LF) 均稳定，不依赖环境换行策略。
const SKIN_CSS = readFileSync(join(process.cwd(), 'src/theme/skin.css'), 'utf8').replace(/\r\n/g, '\n')

/** 取组件级覆盖（排除 common），用于亮/暗键集比对。 */
function componentKeys(theme: typeof lightThemeOverrides): string[] {
  return Object.keys(theme)
    .filter((key) => key !== 'common')
    .sort()
}

describe('P5 令牌层：圆角档（§4.6.4 彻底档）', () => {
  it('common 圆角 12 / small 8（亮暗同值）', () => {
    expect(lightThemeOverrides.common?.borderRadius).toBe('12px')
    expect(lightThemeOverrides.common?.borderRadiusSmall).toBe('8px')
    expect(darkThemeOverrides.common?.borderRadius).toBe('12px')
    expect(darkThemeOverrides.common?.borderRadiusSmall).toBe('8px')
  })

  it('卡片圆角 16（亮暗同值；n-modal preset=card 内层卡片随此）', () => {
    expect(lightThemeOverrides.Card?.borderRadius).toBe('16px')
    expect(darkThemeOverrides.Card?.borderRadius).toBe('16px')
  })

  it('Dialog 圆角 16（亮暗同值）', () => {
    expect(lightThemeOverrides.Dialog?.borderRadius).toBe('16px')
    expect(darkThemeOverrides.Dialog?.borderRadius).toBe('16px')
  })

  it('按钮圆角四档统一 10（亮暗同值）', () => {
    for (const theme of [lightThemeOverrides, darkThemeOverrides]) {
      expect(theme.Button?.borderRadiusTiny).toBe('10px')
      expect(theme.Button?.borderRadiusSmall).toBe('10px')
      expect(theme.Button?.borderRadiusMedium).toBe('10px')
      expect(theme.Button?.borderRadiusLarge).toBe('10px')
    }
  })

  it('输入圆角 10（亮暗同值；Select 族经 skin.css --rw-radius-control 对齐，见下一节护栏）', () => {
    expect(lightThemeOverrides.Input?.borderRadius).toBe('10px')
    expect(darkThemeOverrides.Input?.borderRadius).toBe('10px')
  })

  it('标签药丸圆角 999（亮暗同值）', () => {
    expect(lightThemeOverrides.Tag?.borderRadius).toBe('999px')
    expect(darkThemeOverrides.Tag?.borderRadius).toBe('999px')
  })
})

describe('P5 令牌层：控件密度与表格行高', () => {
  it('按钮行高四档 28/32/36/40（亮暗同值）', () => {
    for (const theme of [lightThemeOverrides, darkThemeOverrides]) {
      expect(theme.Button?.heightTiny).toBe('28px')
      expect(theme.Button?.heightSmall).toBe('32px')
      expect(theme.Button?.heightMedium).toBe('36px')
      expect(theme.Button?.heightLarge).toBe('40px')
    }
  })

  it('输入行高四档 28/32/36/40（亮暗同值）', () => {
    for (const theme of [lightThemeOverrides, darkThemeOverrides]) {
      expect(theme.Input?.heightTiny).toBe('28px')
      expect(theme.Input?.heightSmall).toBe('32px')
      expect(theme.Input?.heightMedium).toBe('36px')
      expect(theme.Input?.heightLarge).toBe('40px')
    }
  })

  it('表格行高 40：lineHeight 1.5 + 三档单值 padding 9px（亮暗同值）', () => {
    // 行高推导：9 + 1.5×14 + 9 + 1（分隔线）= 40；thPadding 必须单值（naive 两处 calc 依赖）
    for (const theme of [lightThemeOverrides, darkThemeOverrides]) {
      expect(theme.DataTable?.lineHeight).toBe('1.5')
      expect(theme.DataTable?.thPaddingSmall).toBe('9px')
      expect(theme.DataTable?.thPaddingMedium).toBe('9px')
      expect(theme.DataTable?.thPaddingLarge).toBe('9px')
      expect(theme.DataTable?.tdPaddingSmall).toBe('9px')
      expect(theme.DataTable?.tdPaddingMedium).toBe('9px')
      expect(theme.DataTable?.tdPaddingLarge).toBe('9px')
    }
  })
})

describe('P5 令牌层：标签无边框实心五态', () => {
  it('默认态 + 五语义态边框全部置无（亮暗同值）', () => {
    for (const theme of [lightThemeOverrides, darkThemeOverrides]) {
      expect(theme.Tag?.border).toBe('none')
      expect(theme.Tag?.borderPrimary).toBe('none')
      expect(theme.Tag?.borderInfo).toBe('none')
      expect(theme.Tag?.borderSuccess).toBe('none')
      expect(theme.Tag?.borderWarning).toBe('none')
      expect(theme.Tag?.borderError).toBe('none')
    }
  })
})

describe('P5 令牌层：亮/暗同批（S-3 护栏）', () => {
  it('两份覆盖的组件级键集一致', () => {
    expect(componentKeys(darkThemeOverrides)).toEqual(componentKeys(lightThemeOverrides))
  })

  it('各组件级覆盖的内部键集一致', () => {
    for (const key of componentKeys(lightThemeOverrides)) {
      const light = lightThemeOverrides[key as keyof typeof lightThemeOverrides] as Record<string, unknown>
      const dark = darkThemeOverrides[key as keyof typeof darkThemeOverrides] as Record<string, unknown>
      expect(Object.keys(dark ?? {}).sort(), `组件 ${key} 亮暗键集不一致`).toEqual(
        Object.keys(light ?? {}).sort(),
      )
    }
  })
})

describe('P5 Select 族圆角对齐：tokens.ts ↔ skin.css 跨文件同源（owner 2026-10-22 方案 A）', () => {
  it('skin.css 定义 --rw-radius-control 且与 Input 圆角（亮暗两份）相等', () => {
    expect(SKIN_CSS).toContain('--rw-radius-control: 10px;')
    expect(lightThemeOverrides.Input?.borderRadius).toBe('10px')
    expect(darkThemeOverrides.Input?.borderRadius).toBe('10px')
  })

  it('.n-base-selection 单类规则消费该变量（dev + prod 双环境实证生效）', () => {
    expect(SKIN_CSS).toContain('.n-base-selection {\n  border-radius: var(--rw-radius-control);\n}')
  })

  it('不写内部结构覆盖：规则只允许命中 .n-base-selection 根选择器', () => {
    // §4.6.3 纪律：不改 Naive 内部结构——禁止 __border 等内部类选择器混入
    expect(SKIN_CSS).not.toContain('.n-base-selection__')
  })
})

describe('P5 间距节奏：tokens.ts ↔ skin.css 同源', () => {
  it('spacing 常量为六档 4/8/12/16/24/32', () => {
    expect(Object.keys(spacing)).toEqual(['1', '2', '3', '4', '5', '6'])
    expect(Object.values(spacing)).toEqual([4, 8, 12, 16, 24, 32])
  })

  it('skin.css 的 --rw-space-1..6 与 spacing 常量逐一相等', () => {
    for (const [step, px] of Object.entries(spacing)) {
      expect(SKIN_CSS, `--rw-space-${step} 与 spacing 漂移`).toContain(`--rw-space-${step}: ${px}px;`)
    }
  })

  it('六档工具类齐全（gap / p / px / py / mt / mb × 6）', () => {
    for (const step of Object.keys(spacing)) {
      for (const family of ['gap', 'p', 'px', 'py', 'mt', 'mb']) {
        expect(SKIN_CSS, `缺少 .rw-${family}-${step}`).toContain(`.rw-${family}-${step} {`)
      }
    }
  })
})

describe('P5 卡片三档阴影：skin.css 唯一定义处 + 亮暗各一套', () => {
  it('三档变量在 :root 与 .rw-dark 各出现一次（共 6 处，tokens.ts 不重复定义）', () => {
    const matches = SKIN_CSS.match(/--rw-shadow-(?:rest|hover|pressed):/g) ?? []
    expect(matches.length).toBe(6)
  })

  it('亮色 rest 档取 UI全量改造方案 §6（原《UI界面设计方案》） §3.3 卡片值', () => {
    expect(SKIN_CSS).toContain('--rw-shadow-rest: 0 1px 3px rgba(15, 23, 42, 0.08);')
  })

  it('hover / pressed 为内缩档（模糊半径递减：3 → 2 → 1）', () => {
    expect(SKIN_CSS).toContain('--rw-shadow-hover: 0 1px 2px rgba(15, 23, 42, 0.12);')
    expect(SKIN_CSS).toContain('--rw-shadow-pressed: 0 1px 1px rgba(15, 23, 42, 0.16);')
  })

  it('暗色三档同批给出（低透明度黑，防暗底黑边/发灰）', () => {
    expect(SKIN_CSS).toContain('--rw-shadow-rest: 0 1px 3px rgba(0, 0, 0, 0.32);')
    expect(SKIN_CSS).toContain('--rw-shadow-hover: 0 1px 2px rgba(0, 0, 0, 0.4);')
    expect(SKIN_CSS).toContain('--rw-shadow-pressed: 0 1px 1px rgba(0, 0, 0, 0.48);')
  })

  it('rest 档作用于 .n-card、hover 档双类覆盖 naive hoverable 规则、pressed 档有挂载类', () => {
    expect(SKIN_CSS).toContain('.n-card {\n  box-shadow: var(--rw-shadow-rest);')
    expect(SKIN_CSS).toContain('.n-card.n-card--hoverable:hover {')
    expect(SKIN_CSS).toContain('.rw-card--pressed {')
  })

  it('hover 阴影过渡 100ms（双类覆盖 naive 基础 .3s）', () => {
    expect(SKIN_CSS).toContain('box-shadow 100ms var(--rw-ease)')
  })
})

describe('P6 表面变量：skin.css 自持 + 与 tokens.ts borderColor 同源（S-4 单一 skin 文件）', () => {
  it('--rw-border 亮暗值与 tokens.ts common.borderColor 相等（大小写不敏感）', () => {
    // 跨文件同源：形态层边框色 = 令牌层边框色；skin.css 小写、tokens.ts 大写
    expect(SKIN_CSS).toContain('--rw-border: #e2e5ee;')
    expect(SKIN_CSS).toContain('--rw-border: #31364a;')
    expect(lightThemeOverrides.common?.borderColor?.toLowerCase()).toBe('#e2e5ee')
    expect(darkThemeOverrides.common?.borderColor?.toLowerCase()).toBe('#31364a')
  })

  it('surface / surface-hover / focus 亮暗四对登记（改值必须改测试）', () => {
    // 亮色面：白底 / 浅灰 hover / 主色 focus（focus 与 StatusTag info 语义色 #3D5BD9 同源）
    expect(SKIN_CSS).toContain('--rw-surface: #ffffff;')
    expect(SKIN_CSS).toContain('--rw-surface-hover: #f5f6fa;')
    expect(SKIN_CSS).toContain('--rw-focus: #3d5bd9;')
    // 暗色面：深底 / 提亮一档 hover / 亮一档 focus（暗底上原主色对比不足）
    expect(SKIN_CSS).toContain('--rw-surface: #1c1f27;')
    expect(SKIN_CSS).toContain('--rw-surface-hover: #232733;')
    expect(SKIN_CSS).toContain('--rw-focus: #7c93f5;')
  })

  it('危险文本色 --rw-danger 亮暗值与 tokens.ts common.errorColor 相等（跨文件同源）', () => {
    expect(SKIN_CSS).toContain('--rw-danger: #b91c1c;')
    expect(SKIN_CSS).toContain('--rw-danger: #e06c6c;')
    expect(lightThemeOverrides.common?.errorColor?.toLowerCase()).toBe('#b91c1c')
    expect(darkThemeOverrides.common?.errorColor?.toLowerCase()).toBe('#e06c6c')
  })

  it('五枚表面变量各定义两处（:root + .rw-dark，tokens.ts 不重复定义）', () => {
    for (const name of ['--rw-border', '--rw-surface', '--rw-surface-hover', '--rw-focus', '--rw-danger']) {
      const matches = SKIN_CSS.match(new RegExp(`${name}:`, 'g')) ?? []
      expect(matches.length, `${name} 应仅在 :root 与 .rw-dark 各定义一次`).toBe(2)
    }
  })

  it('P6 形态类齐全（action-card / row-card / chip / segmented / status-bar 均在 skin.css）', () => {
    for (const cls of [
      '.rw-action-card {',
      '.rw-row-card {',
      '.rw-chip {',
      '.rw-segmented {',
      '.rw-status-bar {',
    ]) {
      expect(SKIN_CSS, `形态类 ${cls} 缺失（S-4：禁逐页各写一份）`).toContain(cls)
    }
  })

  it('形态类禁裸 --n-* 引用（普通 div 取不到 naive 变量，须消费 --rw-*）', () => {
    // 背景：--n-color/--n-border-color 只存在于 naive 组件根（n-card/n-config-provider），
    // body/#app 直下及普通 div 解析为空；--n-color-hover/--n-text-color-2 名称根本不存在。
    const blocks = SKIN_CSS.match(/\.rw-(?:action-card|row-card|status-bar|chip|segmented)[a-z_-]*\s*\{[^}]*\}/g) ?? []
    expect(blocks.length, '未提取到形态类规则块').toBeGreaterThanOrEqual(10)
    for (const block of blocks) {
      expect(block, `形态类规则引用了不存在的 naive 变量：\n${block}`).not.toMatch(/var\(--n-/)
    }
  })
})

describe('P20 步骤条对比度令牌（暗色文案可读性）', () => {
  const STEPPER_CSS = readFileSync(join(process.cwd(), 'src/components/WizardStepper.vue'), 'utf8')
    .replace(/\r\n/g, '\n')

  /** 取 skin.css 中某变量在 :root 与 .rw-dark 两处的值（小写）。
   *  ⚠️ 定位必须用 `.rw-dark {` 带花括号——文件头注释里也出现了 `.rw-dark` 字样，
   *  裸 indexOf 会把 :root 块从注释处斩断（实测：取到空串）。 */
  function skinVar(name: string): { light: string; dark: string } {
    const darkAt = SKIN_CSS.indexOf('.rw-dark {')
    const root = SKIN_CSS.slice(0, darkAt)
    const dark = SKIN_CSS.slice(darkAt)
    const pick = (scope: string): string => {
      const m = scope.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))
      expect(m, `skin.css 未定义 ${name}`).toBeTruthy()
      return m![1].trim().toLowerCase()
    }
    return { light: pick(root), dark: pick(dark) }
  }

  const hex = (value: string): string => (value.startsWith('#') ? value : `#${value}`)

  it('文本三档与 tokens.ts common.textColor1/2/3 跨文件同源（亮暗各一套）', () => {
    for (const [name, key] of [
      ['--rw-text-1', 'textColor1'],
      ['--rw-text-2', 'textColor2'],
      ['--rw-text-3', 'textColor3'],
    ] as const) {
      const v = skinVar(name)
      expect(hex(v.light)).toBe((lightThemeOverrides.common as Record<string, string>)[key].toLowerCase())
      expect(hex(v.dark)).toBe((darkThemeOverrides.common as Record<string, string>)[key].toLowerCase())
    }
  })

  it('强调面主色/成功色与 tokens.ts primaryColor / successColor 同源', () => {
    const primary = skinVar('--rw-primary')
    const success = skinVar('--rw-success')
    expect(hex(primary.light)).toBe(lightThemeOverrides.common?.primaryColor?.toLowerCase())
    expect(hex(primary.dark)).toBe(darkThemeOverrides.common?.primaryColor?.toLowerCase())
    expect(hex(success.light)).toBe(lightThemeOverrides.common?.successColor?.toLowerCase())
    expect(hex(success.dark)).toBe(darkThemeOverrides.common?.successColor?.toLowerCase())
  })

  it('强调面上的文本色亮暗相反（暗色主色/成功色是高亮色，压白字不过 AA）', () => {
    const onAccent = skinVar('--rw-on-accent')
    expect(hex(onAccent.light)).toBe('#ffffff')
    // 暗色强调面压深色文本（= dark bodyColor）：#7C93F5 压 #14161C = 6.46，压白字仅 2.86
    expect(hex(onAccent.dark)).toBe(darkThemeOverrides.common?.bodyColor?.toLowerCase())
  })

  it('步骤条显式取 --rw-*，禁裸 --n-* 与 font:inherit 了事（buttontext 恒黑）', () => {
    // 真因（2026-10-26 实测 .e2e/p20-stepper-contrast.cjs）：裸 <button> 的 UA 默认
    // color:buttontext 恒为黑，暗色主题下标题对比度仅 1.16:1。故必须显式声明文本色。
    // 只扫 <style> 块——脚本区注释里会提到旧实现的弱化手法，会误命中。
    const style = STEPPER_CSS.slice(STEPPER_CSS.indexOf('<style'))
    expect(style, '未显式声明文本色（会退回 buttontext 黑字）').toMatch(/color:\s*var\(--rw-text-/)
    expect(style, '引用了不存在的 naive 变量').not.toMatch(/var\(--n-/)
    // 不可跳步用降级文本色表达，不用透明度（透明度在暗色下同样掉出 AA，且对比度不可控）
    expect(style.match(/opacity\s*:/g) ?? [], '步骤条禁止用透明度弱化').toHaveLength(0)
  })
})
