/**
 * restic 仓库 URL 组译/解析（审查🟡：自 repo/index.vue 抽出为纯函数以便单测）。
 *
 * 字段模型对齐 Duplicati destination.config（file/s3/b2/ssh/azure/gcs/rclone 的
 * customFields + dynamicFields），适配 restic URL 构型（零后端改动：类型与地址仍由
 * 服务端从 url 派生）。各类型 URL 方言逐一核验自 restic 0.19.1 对应后端 ParseConfig 源码
 * （2026-10-19）：
 *   local  /srv/repo（location.Parse 对无前缀裸路径自动补 local:）   s3  s3:<endpoint>/<bucket>/<path>
 *   b2    b2:<bucket>:/<path>（冒号）        gcs  gs:<bucket>:/<path>（冒号）   azure azure:<container>:/<path>（冒号）
 *   sftp  sftp:<user>@<host>:/<path>（冒号）  rest rest:<url>（原样）           rclone rclone:<remote>:<path>（冒号）
 */

export type RepoTypeKey = 'local' | 's3' | 'b2' | 'sftp' | 'azure' | 'gcs' | 'rest' | 'rclone'

/** 组译/解析共用的地址字段集（repo/index.vue 的 repoForm 为其响应式实现）。 */
export interface RepoUrlFields {
  type: RepoTypeKey | ''
  path: string
  endpoint: string
  bucket: string
  user: string
  server: string
  url: string
  remote: string
  raw: string
}

/** parseRepoUrl 的回填产物（不含 type/raw——type 由调用方决定、raw 只在失败时落原文）。 */
export type ParsedRepoUrl = Partial<Pick<RepoUrlFields, 'path' | 'endpoint' | 'bucket' | 'user' | 'server' | 'url' | 'remote'>>

export function trimLeadSlash(p: string): string {
  return p.replace(/^\/+/, '')
}

/** s3 endpoint 形态合法性（restic 0.19.1 s3 ParseConfig + minio-go 域名校验的可接受集）：
 *  裸 host / host:port / [IPv6][:port] / http(s)://host[:port]。
 *  拒绝形如 cn-north-1:mybucket: 的旧方言残串（冒号后非数字）——它会被 restic 当成
 *  endpoint，minio-go 域名校验直接报错；宁可解析失败落 raw 回退让用户重填，不静默错填。 */
export function isS3Endpoint(v: string): boolean {
  if (/^https?:\/\/\S+$/i.test(v)) {
    return true
  }
  return /^\[[^\]]+\](:\d+)?$/.test(v) || /^[^:/]+(:\d+)?$/.test(v)
}

/** 解析 restic s3 URL（v0.19.1 ParseConfig 方言，源码核验）：
 *  s3:host[:port]/<bucket>[/<prefix>]（裸 endpoint，https）
 *  s3://host/<bucket>[/<prefix>]（与上一行同代码路径，语义等价；归一成裸 host 回显）
 *  s3:http(s)://host[:port]/<bucket>[/<prefix>]（endpoint 带协议头，UseHTTP 由 scheme 决定）
 *  返回 null = 无法解析（调用方落 raw 原文回退）。 */
export function parseS3Url(url: string): { endpoint: string; bucket: string; path: string } | null {
  let s = url
  if (/^s3:https?:\/\//i.test(s)) {
    const m = /^s3:(https?:\/\/[^/]+)\/(.*)$/i.exec(s)
    if (!m) {
      return null
    }
    const bucket = m[2].includes('/') ? m[2].slice(0, m[2].indexOf('/')) : m[2]
    if (!bucket) {
      return null
    }
    return { endpoint: m[1], bucket, path: m[2].slice(bucket.length + 1) }
  }
  if (s.startsWith('s3://')) {
    s = s.slice(5)
  } else if (s.startsWith('s3:')) {
    s = s.slice(3)
  } else {
    return null
  }
  const slash = s.indexOf('/')
  if (slash < 0) {
    return null
  }
  const endpoint = s.slice(0, slash)
  const rest = s.slice(slash + 1)
  const slash2 = rest.indexOf('/')
  const bucket = slash2 < 0 ? rest : rest.slice(0, slash2)
  const path = slash2 < 0 ? '' : rest.slice(slash2 + 1)
  if (!endpoint || !bucket || !isS3Endpoint(endpoint)) {
    return null
  }
  return { endpoint, bucket, path }
}

/** 字段 → restic URL（与 parseRepoUrl 互逆；type 为空时回落 raw 原文）。 */
export function composeRepoUrl(f: RepoUrlFields): string {
  const p = trimLeadSlash(f.path)
  switch (f.type) {
    case 'local':
      return f.path
    case 's3':
      // restic 方言：s3:<endpoint>/<bucket>[/<prefix>]——endpoint/bucket 以 / 分隔
      // （冒号仅用于协议头或 host:port）。path 为空时不补尾斜杠：restic 以首个 / 切
      // endpoint，s3:host/bucket 即可正确切出 bucket，且与解析严格互逆（无假脏）。
      return p ? `s3:${f.endpoint}/${f.bucket}/${p}` : `s3:${f.endpoint}/${f.bucket}`
    case 'b2':
      return `b2:${f.bucket}:/${p}`
    case 'gcs':
      return `gs:${f.bucket}:/${p}`
    case 'azure':
      return `azure:${f.bucket}:/${p}`
    case 'sftp':
      return `sftp:${f.user}@${f.server}:/${p}`
    case 'rest':
      return f.url
    case 'rclone':
      return `rclone:${f.remote}:${p}`
    default:
      return f.raw
  }
}

/** restic URL → 字段（对齐 Duplicati mapper.from；解析失败返回 null，由 raw 原文回退）。 */
export function parseRepoUrl(type: RepoTypeKey, url: string): ParsedRepoUrl | null {
  switch (type) {
    case 'local':
      // restic location.Parse 对裸路径自动补 local: 前缀；反之存了 local:/path 也按裸路径回显
      return { path: url.startsWith('local:') ? url.slice(6) : url }
    case 's3': {
      const parsed = parseS3Url(url)
      if (!parsed) {
        return null
      }
      return { endpoint: parsed.endpoint, bucket: parsed.bucket, path: parsed.path }
    }
    case 'b2':
    case 'gcs':
    case 'azure': {
      const scheme = type === 'b2' ? 'b2:' : type === 'gcs' ? 'gs:' : 'azure:'
      const rest = url.startsWith(scheme) ? url.slice(scheme.length) : url
      const idx = rest.indexOf(':')
      if (idx <= 0) {
        return null
      }
      return { bucket: rest.slice(0, idx), path: trimLeadSlash(rest.slice(idx + 1)) }
    }
    case 'sftp': {
      const rest = url.startsWith('sftp:') ? url.slice(5) : url
      const at = rest.indexOf('@')
      if (at < 0) {
        return null
      }
      const hostPath = rest.slice(at + 1)
      const colon = hostPath.indexOf(':')
      if (colon < 0) {
        return null
      }
      return { user: rest.slice(0, at), server: hostPath.slice(0, colon), path: trimLeadSlash(hostPath.slice(colon + 1)) }
    }
    case 'rest':
      return { url }
    case 'rclone': {
      const rest = url.startsWith('rclone:') ? url.slice(7) : url
      const idx = rest.indexOf(':')
      if (idx < 0) {
        return null
      }
      return { remote: rest.slice(0, idx), path: trimLeadSlash(rest.slice(idx + 1)) }
    }
    default:
      return null
  }
}

/** rclone 段的「实际落盘路径」推导（新建仓库表单的落点提示）。
 *
 * - **crypt 段**：数据写在它的底层目标（conf 里的 `remote` 参数）之下 → `<底层>/<仓库路径>`；
 *   底层可能是本地绝对路径（`/home/222`），也可能是别的远端（`onedrive:backup`）。
 * - **其它段**（网盘 / WebDAV 等）：段内路径不对外暴露 → 只能按 `<remote>:<path>` 示意。
 * - **未探测到该段**：`kind = unknown`，仅回显用户输入供核对。
 *
 * 返回 `null` = 远端名为空（调用方不渲染提示）。
 */
export function resolveRcloneTarget(
  remote: string,
  path: string,
  section: { type?: string; params?: Record<string, string> } | null,
): { kind: 'crypt' | 'remote' | 'unknown'; target: string } | null {
  const name = (remote || '').trim()
  if (!name) {
    return null
  }
  const rel = (path || '').trim().replace(/^\/+/, '')
  const base = section?.type === 'crypt' ? (section.params?.remote ?? '').trim() : ''
  if (base) {
    return { kind: 'crypt', target: rel ? `${base.replace(/\/+$/, '')}/${rel}` : base }
  }
  return { kind: section ? 'remote' : 'unknown', target: rel ? `${name}:${rel}` : name }
}
