/**
 * repoUrl 纯函数护栏（审查🟡：自 repo/index.vue 抽出的 URL 组译/解析逻辑）。
 *
 * 锁定语义：
 * - **compose ↔ parse 严格互逆**：组译出的 URL 必须解析回同一组字段（无假脏的关键——
 *   formDirty 用「组译 URL ≠ savedUrl」判定，不互逆就会出现没改也脏/改了不脏）；
 * - **s3 三种方言**（裸 endpoint / `s3://` / 带协议头）都能解析，且 `s3://` 归一成裸 host；
 * - **非法输入一律 null**（调用方落 raw 原文回退）：旧方言残串、缺 bucket、未知 scheme；
 * - **isS3Endpoint 拒绝 `cn-north-1:mybucket:` 形残串**（restic 0.19.1 + minio-go 域名校验口径）。
 */

import { describe, expect, it } from 'vitest'

import {
  composeRepoUrl,
  isS3Endpoint,
  parseRepoUrl,
  parseS3Url,
  type RepoTypeKey,
  type RepoUrlFields,
} from '@/utils/repoUrl'

function fields(over: Partial<RepoUrlFields>): RepoUrlFields {
  return {
    type: '',
    path: '',
    endpoint: '',
    bucket: '',
    user: '',
    server: '',
    url: '',
    remote: '',
    raw: '',
    ...over,
  }
}

describe('composeRepoUrl', () => {
  it('八种类型按 restic 方言组译', () => {
    expect(composeRepoUrl(fields({ type: 'local', path: '/srv/repo' }))).toBe('/srv/repo')
    expect(composeRepoUrl(fields({ type: 's3', endpoint: 's3.eu-central-1.amazonaws.com', bucket: 'bkt', path: 'prefix' })))
      .toBe('s3:s3.eu-central-1.amazonaws.com/bkt/prefix')
    expect(composeRepoUrl(fields({ type: 's3', endpoint: 's3.eu-central-1.amazonaws.com', bucket: 'bkt' })))
      .toBe('s3:s3.eu-central-1.amazonaws.com/bkt')
    expect(composeRepoUrl(fields({ type: 'b2', bucket: 'my-bucket', path: '/repo' }))).toBe('b2:my-bucket:/repo')
    expect(composeRepoUrl(fields({ type: 'gcs', bucket: 'my-bucket', path: '/repo' }))).toBe('gs:my-bucket:/repo')
    expect(composeRepoUrl(fields({ type: 'azure', bucket: 'container', path: '/repo' }))).toBe('azure:container:/repo')
    expect(composeRepoUrl(fields({ type: 'sftp', user: 'u1', server: 'h1', path: '/srv' }))).toBe('sftp:u1@h1:/srv')
    expect(composeRepoUrl(fields({ type: 'rest', url: 'https://h:8000/' }))).toBe('https://h:8000/')
    expect(composeRepoUrl(fields({ type: 'rclone', remote: 'gdrive', path: 'backup' }))).toBe('rclone:gdrive:backup')
  })

  it('path 的前导斜杠被规整（b2/gcs/azure/sftp/rclone 单冒号方言不容双斜杠）', () => {
    expect(composeRepoUrl(fields({ type: 'b2', bucket: 'b', path: '//repo' }))).toBe('b2:b:/repo')
    expect(composeRepoUrl(fields({ type: 'rclone', remote: 'r', path: '/x' }))).toBe('rclone:r:x')
  })

  it('type 为空回落 raw 原文', () => {
    expect(composeRepoUrl(fields({ raw: 'whatever:legacy' }))).toBe('whatever:legacy')
  })
})

describe('parseRepoUrl（与 compose 互逆）', () => {
  const cases: Array<[RepoUrlFields, string]> = [
    [fields({ type: 'local', path: '/srv/repo' }), '/srv/repo'],
    [fields({ type: 's3', endpoint: 's3.eu-central-1.amazonaws.com', bucket: 'bkt', path: 'prefix/sub' }), ''],
    [fields({ type: 'b2', bucket: 'my-bucket', path: 'repo' }), ''],
    [fields({ type: 'gcs', bucket: 'my-bucket', path: 'a/b' }), ''],
    [fields({ type: 'azure', bucket: 'container', path: 'repo' }), ''],
    [fields({ type: 'sftp', user: 'u1', server: 'h1', path: 'srv' }), ''],
    [fields({ type: 'rest', url: 'https://h:8000/' }), ''],
    [fields({ type: 'rclone', remote: 'gdrive', path: 'backup/x' }), ''],
  ]
  for (const [f] of cases) {
    const composed = composeRepoUrl(f)
    it(`compose → parse 互逆：${composed}`, () => {
      const parsed = parseRepoUrl(f.type as RepoTypeKey, composed)
      expect(parsed).not.toBeNull()
      for (const [k, v] of Object.entries(parsed ?? {})) {
        expect(f[k as keyof RepoUrlFields]).toBe(v)
      }
      // 再组译一次仍是同一 URL（无假脏闭环）
      expect(composeRepoUrl({ ...f, ...parsed } as RepoUrlFields)).toBe(composed)
    })
  }

  it('s3:// 形态解析并归一成裸 host', () => {
    expect(parseRepoUrl('s3', 's3://s3.amazonaws.com/bkt/prefix')).toEqual({
      endpoint: 's3.amazonaws.com',
      bucket: 'bkt',
      path: 'prefix',
    })
  })

  it('s3 带协议头形态：endpoint 保留 scheme（UseHTTP 语义）', () => {
    expect(parseS3Url('s3:http://127.0.0.1:9000/bkt')).toEqual({
      endpoint: 'http://127.0.0.1:9000',
      bucket: 'bkt',
      path: '',
    })
    expect(parseS3Url('s3:https://minio.example.com/bkt/a/b')).toEqual({
      endpoint: 'https://minio.example.com',
      bucket: 'bkt',
      path: 'a/b',
    })
  })

  it('非法输入一律 null（raw 回退）', () => {
    expect(parseRepoUrl('s3', 's3:cn-north-1:mybucket:')).toBeNull() // 旧方言残串
    expect(parseRepoUrl('s3', 's3:host/')).toBeNull() // 缺 bucket
    expect(parseRepoUrl('b2', 'no-colon-here')).toBeNull()
    expect(parseRepoUrl('sftp', 'no-at-sign')).toBeNull()
    expect(parseRepoUrl('rclone', 'no-colon')).toBeNull()
  })

  it('sftp 方言不含端口：server 带冒号时端口段落入 path（dialect 限制，非回归）', () => {
    // restic sftp 后端 URL 为 user@host:path，host 无端口位——带端口的 server 无法
    // 经 URL roundtrip（如需端口走 --option sftp.args 注入），此处固化该已知限制
    expect(parseRepoUrl('sftp', 'sftp:u1@h1:2222:/srv')).toEqual({
      user: 'u1',
      server: 'h1',
      path: '2222:/srv',
    })
  })

  it('local 的 local: 前缀按裸路径回显', () => {
    expect(parseRepoUrl('local', 'local:/srv/repo')).toEqual({ path: '/srv/repo' })
    expect(parseRepoUrl('local', '/srv/repo')).toEqual({ path: '/srv/repo' })
  })
})

describe('isS3Endpoint', () => {
  it('接受裸 host / host:port / IPv6 / 带协议头', () => {
    expect(isS3Endpoint('s3.amazonaws.com')).toBe(true)
    expect(isS3Endpoint('127.0.0.1:9000')).toBe(true)
    expect(isS3Endpoint('[::1]:9000')).toBe(true)
    expect(isS3Endpoint('https://minio.example.com')).toBe(true)
  })

  it('拒绝旧方言残串与带路径的串', () => {
    expect(isS3Endpoint('cn-north-1:mybucket:')).toBe(false)
    expect(isS3Endpoint('host/path')).toBe(false)
  })
})
