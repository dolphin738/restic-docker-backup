# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

**single-context** — 一个仓库共用一套用语：根目录一份 `GLOSSARY.md`，架构决定放 `docs/adr/`。不建 `GLOSSARY-MAP.md`，也不给子项目各建一份。当前这两个位置都还没有文件，等真正写下第一个词条 / 第一条决策时再建（见下文「惰性创建」）。

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.
- If a `GLOSSARY-MAP.md` ever appears at the repo root, the layout has switched to multi-context: read it and then each per-context `GLOSSARY.md` relevant to the topic. Until then, ignore it.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (this repo):

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── backend/ · frontend/ · docs/
```

Multi-context repo (presence of `GLOSSARY-MAP.md` at the root) — not this repo:

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
