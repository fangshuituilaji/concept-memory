# Architecture Notes

## Initial scope

第一阶段只定义领域边界，不预设具体数据库、向量模型或 Agent 框架。

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
