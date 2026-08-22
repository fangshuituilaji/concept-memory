# Architecture Notes

## Initial scope

第一阶段实现本地代码理解与概念编码，不直接接入 MCP 或具体 Coding Agent。输入 Python 文件或目录，输出每个文件约 2~3 个、最多 9 个由 Qwen-Flash 归纳的高层概念卡片。

当前实现采用 Python AST 提取可验证的源码事实，使用 DashScope Qwen-Flash 负责整文件语义概括，使用 JSON 作为交换格式，使用 SQLite FTS5 作为本地全文检索派生层。

## Memory lifecycle

```text
candidate -> validated -> stored -> retrieved -> reinforced/updated -> archived/forgotten
```

每个生命周期状态都应有明确的进入条件、退出条件和可观测记录。

## Open decisions

- 记忆的最小粒度是什么
- 如何区分事实、偏好、经验和任务状态
- 何时写入记忆，何时只保留在会话上下文
- 如何处理冲突记忆和过期记忆
- 结构化检索、语义检索和混合检索如何组合
- 哪些信息必须脱敏、加密或禁止持久化
