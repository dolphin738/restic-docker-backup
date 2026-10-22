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

const SKIN_CSS = readFileSync(join(process.cwd(), 'src/theme/skin.css'), 'utf8')

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

  it('亮色 rest 档取 UI界面设计方案 §3.3 卡片值', () => {
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

