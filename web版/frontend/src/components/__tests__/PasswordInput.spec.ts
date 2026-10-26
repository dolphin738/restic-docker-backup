/**
 * 统一密码输入组件（`components/PasswordInput.vue`）单元测试 + **单一实现护栏**。
 *
 * 背景（2026-10-27）：项目内 18 处密码框各自写 `n-input type="password"`，宽度行为
 * 完全不受控——`n-form-item` 里被拉成一行超长输入框，`n-space` 里塌缩到几十像素
 * （32 位随机密码只看得见前几位）。统一收口到本组件后，用「全项目零裸 password
 * 输入」的护栏防止再散回去。
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { NButton, NInput } from 'naive-ui'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import PasswordInput from '@/components/PasswordInput.vue'

const SRC_DIR = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      walk(full, out)
    } else if (/\.vue$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

describe('PasswordInput', () => {
  it('渲染密码输入并支持 v-model 双向绑定', async () => {
    const w = mount(PasswordInput, { props: { modelValue: 'abc' } })
    const input = w.element.querySelector('input') as HTMLInputElement
    expect(input.type).toBe('password')
    expect(input.value).toBe('abc')

    await w.findComponent(NInput).vm.$emit('update:value', 'xyz')
    expect(w.emitted('update:modelValue')?.[0]).toEqual(['xyz'])
  })

  it('点击眼睛图标可切明文（show-password-on=click，非 hover）', () => {
    const w = mount(PasswordInput, { props: { modelValue: 'abc' } })
    // naive 的明文切换入口是输入框内的 suffix 图标，hover 预览会频繁误触发
    const toggle = w.element.querySelector('.n-input__suffix .n-base-icon')
    expect(toggle).toBeTruthy()
  })

  it('宽度由 width 决定（默认 320px，可传 100% 撑满容器）', () => {
    const def = mount(PasswordInput, { props: { modelValue: '' } })
    expect((def.element as HTMLElement).style.width).toBe('320px')

    const full = mount(PasswordInput, { props: { modelValue: '', width: '100%' } })
    expect((full.element as HTMLElement).style.width).toBe('100%')
  })

  it('generate-label 存在时渲染生成按钮并 emit generate', async () => {
    const w = mount(PasswordInput, { props: { modelValue: '', generateLabel: '生成' } })
    const btn = w.findComponent(NButton)
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('生成')
    btn.vm.$emit('click')
    expect(w.emitted('generate')).toHaveLength(1)
  })

  it('未传 generate-label 时不渲染按钮（普通密码框场景）', () => {
    const w = mount(PasswordInput, { props: { modelValue: '' } })
    expect(w.findComponent(NButton).exists()).toBe(false)
  })

  it('未声明属性透传到内部输入（maxlength / placeholder 等原生属性）', () => {
    // 用途：导出密码框传 maxlength=256、通知页传动态 placeholder——
    // 组件不逐个声明这些属性，靠 $attrs 落到内部 n-input 的原生 input 上。
    const w = mount(PasswordInput, {
      props: { modelValue: '' },
      attrs: { maxlength: 256 },
    })
    const input = w.element.querySelector('input') as HTMLInputElement
    expect(input.getAttribute('maxlength')).toBe('256')
  })

  it('disabled 同时禁用输入与生成按钮', () => {
    const w = mount(PasswordInput, { props: { modelValue: '', disabled: true, generateLabel: '生成' } })
    expect((w.element.querySelector('input') as HTMLInputElement).disabled).toBe(true)
    expect(w.findComponent(NButton).props('disabled')).toBe(true)
  })
})

describe('护栏：密码输入单一实现', () => {
  it('除 PasswordInput.vue 外，src 下不得出现裸 password 输入', () => {
    const offenders = walk(SRC_DIR)
      .filter((file) => !file.endsWith(join('components', 'PasswordInput.vue')))
      .filter((file) => /type="password"|type:\s*'password'/.test(readFileSync(file, 'utf8')))
      .map((file) => file.slice(SRC_DIR.length + 1))

    expect(offenders).toEqual([])
  })
})
