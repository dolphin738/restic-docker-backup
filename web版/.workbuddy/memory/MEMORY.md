

## 本项目启动方式（2026-10-11 纠错）
- ⚠️ 更新（2026-10-11 下午）：应用户要求已创建 **`dev-scripts/dev.ps1`**（菜单式：启动/测试/lint/红线校验/漏洞扫描，支持 `-Check` 自检与 `-Run <动作>` 非交互模式）。此条此前记录的「仓库没有 dev-scripts」已不再成立；仓库仍无 README。
- 本项目真实启动：后端 `cd web版/backend && uv run uvicorn app.main:app --reload`（或 `.venv/Scripts/python.exe -m app.main`，main.py 内置 main() 入口含 TLS 判断）；前端 `cd web版/frontend && pnpm dev`（vite :5173，/api 代理到 127.0.0.1:8000）。
- **教训**：给项目建议前，项目本地事实（路径/脚本/命令）必须先 ls/grep 核实，不得从跨项目记忆直接引用。
- dev.ps1 两个实测抓出的坑（已修，他人复用必读）：①便携 Git 的 bash 对 Windows PATH 的 MSYS 转换是坏的（/c 挂载缺失），往 $env:PATH 注入 usr\bin 无效——解法是 bash 内部 `export PATH="/usr/bin:/bin:$PATH"` 且脚本路径转 POSIX（/d/...）传入；②PATH 里的 `C:\Windows\system32\bash.exe` 是 WSL 存根，探测时要排除。运行时为 pwsh 7.6.5，无需 5.1 兼容（用户明确不需要，UTF-8 无 BOM 即可）。