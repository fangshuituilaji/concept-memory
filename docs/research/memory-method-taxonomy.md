# AI Agent 记忆方法分类：它们究竟在坚持什么理念？

> 基于本项目截至 2026-08-22 的 Agent Memory、RAG、论文、开源项目和评测基准调研整理。

## 1. 先说结论：不要按“用了什么数据库”分类

向量数据库、图数据库、文件系统和 KV Cache 都只是实现手段。真正有区分度的问题是：

> **一个系统认为“记忆”究竟是什么？它认为记忆应该如何形成、如何存在、如何被使用？**

因此，下面按照系统的**第一性理念**分类，而不是按照技术组件分类。

一个系统可能同时属于多个类别。例如：

- Mem0 既是“事实画像记忆”，也使用混合 RAG；
- Hindsight 既是“经验记忆”，也使用时间图和反思；
- ReMe 既是“文件型记忆”，也做后台巩固；
- EverMemOS 既是“自组织记忆”，也属于 Memory OS。

为了避免重复，本文为每个系统指定一个**主分类**，再列出它的次要属性。

---

## 2. 八大主分类总览

| 分类 | 它把记忆看成什么 | 主要回答的问题 | 代表系统/研究 | 一句话核心观念 |
|---|---|---|---|---|
| A. 外部知识检索型 | 模型之外的可检索知识 | “当前问题需要哪些资料？” | RAG、LlamaIndex、GraphRAG、HippoRAG、Cognee | **模型不必什么都记住，只要能在需要时找到可靠证据。** |
| B. 上下文管理/虚拟内存型 | 有限上下文的分层扩展 | “什么应该留在眼前，什么应该放到外部？” | MemGPT、Letta、LangGraph Store/LangMem、LlamaIndex Memory | **上下文窗口不是全部记忆，而是 Agent 正在使用的工作内存。** |
| C. 事实与用户画像型 | 关于用户、项目或世界的稳定事实 | “这个人/项目长期是什么样？” | Mem0、Supermemory、Memobase、MemoryOS | **把连续对话提炼成少量可更新的长期事实。** |
| D. 时间世界模型型 | 随时间变化的实体、关系和事件 | “谁在什么时候与谁有什么关系？” | Graphiti/Zep、Cognee、Hindsight、EverMemOS | **记忆不是静态文本，而是带时间和历史的动态世界模型。** |
| E. 经历、技能与策略学习型 | Agent 做过什么以及如何做得更好 | “以前怎样完成过类似任务？” | Hindsight、ReMe、Memori、memU、AgentMem、MemOS | **真正有价值的记忆不是知道发生过什么，而是知道下次怎么做。** |
| F. 认知巩固/自组织型 | 从经历到摘要、反思、概念和关联的过程 | “记忆如何自己变得更有组织？” | Generative Agents、MemoryBank、A-MEM、LightMem、ReMe、EverMemOS | **记忆不是一次写入，而是不断压缩、联结、反思和重构。** |
| G. 记忆操作系统型 | 可调度、可迁移、可组合的系统资源 | “不同类型的记忆如何统一管理？” | MemoryOS、MemOS、EverMemOS、部分 MemGPT | **记忆应像操作系统管理资源一样被分层、调度、版本化和治理。** |
| H. 可验证与治理型 | 带证据、权限、版本和删除能力的状态 | “我凭什么相信这条记忆，如何纠错和撤销？” | AgentMem、Memoria、ReMe、Letta MemFS、Graphiti provenance | **记忆首先必须可追溯、可审计、可修正，而不是只看起来聪明。** |

---

# 3. 八类方法的核心思想

## A. 外部知识检索型：把记忆看成“模型之外的知识库”

### 核心问题

这类方法最关心的不是用户画像或 Agent 经历，而是如何让模型在回答问题时访问它参数中没有的资料。

### 基本流程

```text
文档/知识 → 切分 → 索引
用户问题 → 召回 → 重排序 → 拼接证据 → 生成答案
```

### 典型系统与研究

- **RAG**：把外部非参数记忆接入语言模型。
- **LlamaIndex**：提供文档、Node、向量索引、Property Graph 和 Query Engine 等数据接入与检索抽象。
- **GraphRAG**：用图结构和社区摘要补充单纯 chunk 检索，尤其处理全局性问题。
- **HippoRAG**：借鉴海马体和图扩散机制，用知识图与 Personalized PageRank 做长期检索。
- **Cognee**：把文档、对话和数据转成图+向量形式的可搜索知识。

### 它们相信什么

- 模型参数不是唯一知识来源；
- 知识可以外置、更新和重新索引；
- 回答质量很大程度取决于证据召回，而不只是模型本身；
- 记忆的最小单元通常是文档、chunk、实体、关系或证据。

### 它们没有完全解决什么

- 用户偏好如何长期演化；
- 旧事实如何被新事实替代；
- Agent 过去做过什么；
- 记忆是否应该写入；
- 用户要求删除后如何删除所有派生索引。

### 一句话核心观念

> **模型不必什么都记住，只要能在需要时找到可靠证据。**

---

## B. 上下文管理/虚拟内存型：把记忆看成“上下文窗口的延伸”

### 核心问题

这类方法从一个工程事实出发：LLM 的上下文窗口有限，但 Agent 的任务、工具和历史可能无限增长。

### 基本流程

```text
当前工作记忆 / core memory
          ↕ paging / tool call / checkpoint
外部 archival memory / store / file system
```

### 典型系统与研究

- **MemGPT**：把上下文窗口类比为物理内存，把外部记忆类比为磁盘，让 Agent 主动分页管理内容。
- **Letta**：发展 Memory Block、外部文件、Skill 和 MemFS，使记忆成为 Agent runtime 的一部分。
- **LangGraph Store / LangMem**：把 thread-scoped checkpoint 与跨 thread 的长期 store 分开，并支持热路径工具和后台 memory manager。
- **LlamaIndex Memory**：提供消息、摘要、记忆块和可扩展的 Agent Memory，但将具体长期策略留给应用组合。

### 它们相信什么

- 当前上下文不等于全部记忆；
- 记忆需要分层，只有少量高价值信息应该直接进入 prompt；
- Agent 可以把“读取/写入记忆”当成推理动作；
- checkpoint、store 和 long-term memory 应分开设计。

### 它们没有完全解决什么

- Agent 是否会正确调用记忆工具；
- Agent 自己写入的内容是否可信；
- 记忆之间的冲突和时间有效期；
- 自主记忆操作的权限和安全边界。

### 一句话核心观念

> **上下文窗口不是全部记忆，而是 Agent 正在使用的工作内存。**

---

## C. 事实与用户画像型：把记忆看成“稳定事实的持续更新”

### 核心问题

这类方法认为，跨会话个性化最需要的不是完整聊天记录，而是少量结构化事实，例如：

- 用户偏好中文；
- 用户正在维护某个项目；
- 项目使用某种技术栈；
- 用户习惯先讨论设计再写代码；
- 某项约束长期有效。

### 基本流程

```text
对话 → 事实抽取 → 去重/冲突判断 → profile/fact store
查询 → 相关事实 → 注入 Agent 上下文
```

### 典型系统与研究

- **Mem0**：原子事实、偏好、user/session/agent scope 和事实更新。
- **Supermemory**：Fact、Preference、Episode、Profile，以及 `updates`、`extends`、过期和遗忘。
- **Memobase**：User Profile、Event Timeline、topic/subtopic memo 和 per-user buffer。
- **MemoryOS**：短期、中期和长期个人画像/知识分层。
- **EverMemOS**：从对话抽取事实和偏好，再逐步形成用户画像。

### 它们相信什么

- 长期记忆应尽量原子化、结构化和低噪声；
- “记住用户是谁”比“保存所有聊天”更有价值；
- 事实必须支持更新、过期、覆盖或撤销；
- 不同 scope（用户、项目、Agent、租户）应隔离。

### 它们没有完全解决什么

- 抽取出来的事实可能是错误推断；
- 偏好可能只在当前任务中暂时成立；
- 事实之间的因果和复杂关系表达不足；
- 事实应该何时升级为长期记忆，需要策略判断。

### 一句话核心观念

> **把连续对话提炼成少量可更新的长期事实。**

---

## D. 时间世界模型型：把记忆看成“动态实体关系图”

### 核心问题

这类方法认为，很多 Agent 问题不是“找到相似文本”，而是回答：

- 谁在什么时候做了什么？
- 某个事实何时成立？
- 现在的状态和过去有什么不同？
- A 和 B 通过哪些事件发生了关系？
- 哪条事实被哪条新事实替代？

### 基本流程

```text
episode/event → entity resolution → fact/edge extraction
             → valid time + provenance → temporal graph
query → vector/BM25/entity/time/graph → rerank
```

### 典型系统与研究

- **Graphiti / Zep**：实体、事实边、episode、validity window、增量图更新和时间检索。
- **Cognee**：图、向量、ontology、DataPoint、NodeSet 和时间检索。
- **Hindsight**：事实、实体、观察、因果关系和时间过滤。
- **EverMemOS**：Episode、Fact、Preference、Profile 的渐进式组织。
- **HippoRAG**：以知识图和图扩散补足长程、多跳检索。

### 它们相信什么

- 文本只是事件的表面表达，真正重要的是实体、关系、时间和来源；
- 旧事实不一定要删除，可能需要标记为历史或失效；
- 图结构比单一 embedding 更适合多跳和时间推理；
- 记忆的价值来自关系网络，而不只是单条文本相似度。

### 它们没有完全解决什么

- 实体消歧和实体合并可能出错；
- 错误关系会在图中扩散；
- 图构建需要额外模型调用和基础设施；
- 图膨胀、边失效和删除传播会增加运维复杂度。

### 一句话核心观念

> **记忆不是静态文本，而是带时间和历史的动态世界模型。**

---

## E. 经历、技能与策略学习型：把记忆看成“未来行动的经验”

### 核心问题

这类方法不满足于记住事实，而是关心 Agent 的行动轨迹：

- 做过什么；
- 哪个工具调用成功或失败；
- 哪种解决方案有效；
- 哪些方案已经试过；
- 下一次遇到类似任务应如何行动。

### 基本流程

```text
任务 → 行动 → 工具结果 → 成功/失败
                         ↓
                 经验/策略/技能抽取
                         ↓
                 相似任务时检索复用
```

### 典型系统与研究

- **Hindsight**：World Facts、Experiences、Observations、Mental Models 和 Reflect。
- **ReMe**：daily memory、digest、procedural memory、工具结果和成功经验。
- **Memori**：Entity、Process、Session、Event、Rule、Skill，重点记录 Agent 执行过程。
- **memU**：从不同 Agent 的 session log 沉淀为 Markdown memory 和 skill。
- **AgentMem**：把 Agent 声称做过的事情与真实工具事件、仓库状态和证据关联。
- **MemOS**：把 skill memory、任务经验和其他记忆类型统一调度。

### 它们相信什么

- Agent 的长期能力来自可复用的经验和程序，而不只是知识；
- 失败经验同样重要，尤其是避免重复踩坑；
- 经验必须和任务类型、环境、工具版本和结果证据绑定；
- 记忆应该直接改善下一次决策，而不是只改善回答的知识量。

### 它们没有完全解决什么

- 过去经验是否适用于当前环境；
- 错误经验会不会被固化为错误技能；
- 如何评价一条经验对真实任务的边际收益；
- 技能版本如何升级、回滚和废弃。

### 一句话核心观念

> **真正有价值的记忆不是知道发生过什么，而是知道下次怎么做。**

---

## F. 认知巩固/自组织型：把记忆看成“不断重构的认知过程”

### 核心问题

这类方法认为，原始经历不会自动变成好记忆；系统需要像人类认知一样进行：

- 重要性判断；
- 摘要和压缩；
- 关联和链接；
- 反思和抽象；
- 从短期材料形成长期概念；
- 睡眠式或后台巩固。

### 基本流程

```text
raw episodes → salience/importance
             → summary/compression
             → link/reflection/consolidation
             → semantic memory / profile / skill
```

### 典型系统与研究

- **Generative Agents**：memory stream、recency、importance、relevance、reflection 和 planning。
- **MemoryBank**：借鉴遗忘曲线，通过强化、更新和遗忘管理长期记忆。
- **A-MEM**：以 Zettelkasten note、标签、关键词和动态链接组织记忆。
- **LightMem**：轻量在线处理，加上离线 sleep-time consolidation。
- **ReMe**：session → daily memory → digest，并可保留原始 JSONL。
- **EverMemOS**：从 episodic trace 到 semantic consolidation，再进行 reconstructive recall。
- **Hindsight**：从 Fact 组织为 Observation 和 Mental Model，并通过 Reflect 综合。

### 它们相信什么

- 原始对话不是最终记忆，只是记忆形成的材料；
- 记忆应随着时间被压缩、整理、关联和重新解释；
- 在线路径应保持轻量，复杂巩固可以放到后台；
- 记忆的“可用性”比原始信息量更重要。

### 它们没有完全解决什么

- 摘要和反思可能制造幻觉；
- 过度压缩会丢失关键细节；
- 错误概念一旦被链接和巩固，可能更难清除；
- 重要性和遗忘曲线不一定适合所有 Agent 任务。

### 一句话核心观念

> **记忆不是一次写入，而是不断压缩、联结、反思和重构。**

---

## G. 记忆操作系统型：把记忆看成“需要统一调度的系统资源”

### 核心问题

这类方法不只问“怎么检索”，而是问：

- 文本记忆、图记忆、技能记忆、偏好记忆如何共存？
- 哪类记忆应该被激活？
- 哪类记忆应该合并、冻结、归档或迁移？
- 如何让多个 Agent 共享或隔离记忆？
- 如何管理版本、生命周期和资源预算？

### 基本流程

```text
多类型 memory
      ↓
统一容器/元数据/调度器
      ↓
激活、组合、迁移、合并、冻结、归档
      ↓
按任务构建上下文
```

### 典型系统与研究

- **MemoryOS**：短期、中期、长期记忆的分层管理。
- **MemOS**：MemCube、文本/树/偏好/技能/KV/LoRA 等多形态记忆统一调度。
- **EverMemOS**：自组织长期记忆、episode、profile 和长期推理。
- **MemGPT / Letta**：虽然不完全是 Memory OS，但有明显的分层内存和 paging 理念。

### 它们相信什么

- 记忆不是一个 vector store，而是一组不同性质的系统资源；
- 不同记忆类型需要不同生命周期和检索策略；
- 记忆应该可组合、可迁移、可版本化和可治理；
- Agent 的长期能力可能来自文本、技能、激活和参数记忆的协同。

### 它们没有完全解决什么

- 系统边界很大，容易过度设计；
- 多种记忆形态之间的一致性很难维护；
- 参数/KV/激活记忆的删除、回滚和租户隔离非常复杂；
- 调度器本身可能成为新的复杂性和故障源。

### 一句话核心观念

> **记忆应像操作系统管理资源一样被分层、调度、版本化和治理。**

---

## H. 可验证与治理型：把记忆看成“有证据的可审计状态”

### 核心问题

这类方法把“记住”视为一个可信度问题：

- 这条记忆来自哪里？
- 是观察到的事实，还是模型推断？
- Agent 说自己做过，是否真的做过？
- 记忆被更新后，旧版本能否恢复？
- 用户要求删除时，派生摘要、向量和图边是否一起删除？

### 基本流程

```text
事件/工具结果/仓库状态
          ↓
证据绑定 + 版本化记忆
          ↓
审计、回滚、撤销、删除和权限控制
```

### 典型系统与研究

- **AgentMem**：将 Agent 事件和真实仓库状态、工具结果、checkpoint 绑定。
- **Memoria**：强调 working/persistent memory、实体、Git-like 历史和版本控制。
- **ReMe**：保留原始 JSONL、Markdown 派生记忆、digest 和来源关系。
- **Letta MemFS**：以 Git 管理可读的 Agent 记忆文件。
- **Graphiti / Zep**：保留 episode、provenance 和事实的时间窗口。
- **HaluMem**：不只评估最终答案，而是分别评估 memory extraction、update 和 QA 阶段的幻觉。

### 它们相信什么

- 记忆的正确性不能只靠最终答案判断；
- 原始事件、派生记忆和索引必须分层；
- 记忆必须能解释、修改、撤销和删除；
- 对 Agent 来说，“可证明地知道”比“似乎记得”更重要。

### 它们没有完全解决什么

- 证据本身可能不完整或被污染；
- 审计、版本和删除会增加系统成本；
- 可验证性与低延迟之间存在取舍；
- 个人数据和企业数据还需要更严格的隐私与权限策略。

### 一句话核心观念

> **记忆首先必须可追溯、可审计、可修正，而不是只看起来聪明。**

---

# 4. 全部研究/系统归类表

## 4.1 系统与框架

| 系统 | 主分类 | 次要分类 | 核心记忆单元 | 最核心的理念 |
|---|---|---|---|---|
| RAG | A 外部知识检索 | H 可验证 | 文档、chunk、证据 | 需要时从外部知识中找证据 |
| LlamaIndex | A 外部知识检索 | B 上下文管理 | Document、Node、Index、Chat Store | 把各种外部数据接入 Agent 检索 |
| GraphRAG | A 外部知识检索 | D 时间/关系模型 | 图节点、社区摘要、关系 | 用图结构回答局部和全局问题 |
| HippoRAG | A 外部知识检索 | D、F | 知识图、节点、扩散路径 | 用图扩散模拟长期联想检索 |
| Cognee | A 外部知识检索 | D、F、G | DataPoint、chunk、图节点、NodeSet | 把多源资料逐步转成可检索知识脑 |
| MemGPT | B 上下文管理 | G、F | Core memory、archival memory、recall | 用分页机制突破上下文窗口限制 |
| Letta | B 上下文管理 | E、G、H | Memory Block、文件、Skill、MemFS | Agent 自己管理当前可见的记忆 |
| LangGraph Store / LangMem | B 上下文管理 | C、F | checkpoint、namespace/key、JSON memory | 把线程状态与跨会话长期记忆分开 |
| Mem0 | C 事实/画像 | A、D | 原子事实、偏好、scope | 从对话中提炼少量可更新事实 |
| Supermemory | C 事实/画像 | A、D、F | Fact、Preference、Episode、Profile | 用更新关系维护个性化上下文 |
| Memobase | C 事实/画像 | D | User Profile、Event Timeline | 用画像和事件时间线服务个性化 |
| MemoryOS | C 事实/画像 | G、F | Short/Mid/Long memory、Profile | 让个人记忆按层级逐步沉淀 |
| Graphiti / Zep | D 时间世界模型 | A、H | Entity、Fact Edge、Episode、validity window | 用动态时间图表达变化中的事实 |
| Hindsight | E 经历/技能 | D、F、H | Fact、Experience、Observation、Mental Model | 让 Agent 从经历中形成可复用判断 |
| ReMe | E 经历/技能 | F、H、B | Daily、Digest、Procedure、Markdown | 把历史运行过程沉淀为可读经验 |
| Memori | E 经历/技能 | C、F | Entity、Process、Session、Event、Skill | 记住 Agent 执行过什么以及如何执行 |
| memU | E 经历/技能 | F、B | Session log、Markdown memory、Skill | 让多个 Agent 共享个人经验和技能 |
| AgentMem | H 可验证治理 | E、B | Event、Evidence、Checkpoint、Repo state | 只把有外部证据的行动写入长期记忆 |
| A-MEM | F 认知巩固 | E、A | Memory Note、Tag、Keyword、Link | 用动态链接让记忆网络自行生长 |
| LightMem | F 认知巩固 | G | 压缩记忆、短期/长期、KV | 用轻量在线处理和后台巩固降低成本 |
| EverMemOS | F 认知巩固 | C、D、G | Episode、Fact、Preference、Profile | 从经历逐步巩固出长期画像和场景记忆 |
| MemOS | G 记忆操作系统 | E、F、H | MemCube、Text、Skill、KV、LoRA | 统一调度不同形态的记忆资源 |
| Memoria | H 可验证治理 | B、G | Working/Persistent、Entity、Version | 用版本和历史保证记忆可回滚 |
| OpenMemory | D 时间世界模型 | H、B | SQL memory、Temporal Graph | 用本地 SQL 和时间图管理可控记忆 |
| MineContext | C 事实/画像 | F、B | Context、Memory Bank、User Memory | 通过主动上下文理解用户长期状态 |

## 4.2 论文与评测

| 研究 | 主分类 | 核心贡献 | 一句话核心理念 |
|---|---|---|---|
| Retrieval-Augmented Generation | A 外部知识检索 | 参数记忆+外部非参数记忆 | 让模型在需要时访问外部知识 |
| Generative Agents | F 认知巩固 | memory stream、重要性、新近性、相关性、reflection | 像认知系统一样从经历形成行动计划 |
| MemoryBank | F 认知巩固 | 遗忘曲线、记忆强化、更新与遗忘 | 记忆需要随时间维护，而不是永久堆积 |
| MemGPT | B 上下文管理 | 虚拟上下文、分层记忆、paging | 用操作系统思想管理有限上下文 |
| CoALA | 理论框架 | working、episodic、semantic、procedural memory | Agent 需要不同类型的记忆，而非一个统一缓存 |
| HippoRAG | A 外部知识检索 | 知识图+Personalized PageRank | 长期检索应具有图结构和联想能力 |
| Zep | D 时间世界模型 | Temporal Knowledge Graph | Agent 需要理解事实的时间有效性 |
| A-MEM | F 认知巩固 | Zettelkasten、动态链接、记忆演化 | 新记忆应重新组织已有记忆网络 |
| Mem0 | C 事实/画像 | 生产化长期记忆、事实抽取与整合 | 只保留对个性化有用的结构化记忆 |
| MemOS | G 记忆操作系统 | 统一文本、技能、激活和参数记忆 | 记忆应成为可调度的系统资源 |
| LightMem | F 认知巩固 | Small LM、在线压缩、离线巩固 | 用低成本处理获得长期记忆能力 |
| EverMemOS | G 记忆操作系统 | 自组织长期记忆、场景和画像 | 记忆应从原子经历逐步成长为结构化认知 |
| LoCoMo | 评测基准 | 超长期、多 session、多类型记忆问题 | 长期记忆必须在长时段和多跳场景中测试 |
| LongMemEval | 评测基准 | 信息抽取、跨 session 推理、时间、更新、拒答 | 评估记忆必须覆盖更新和不知道时拒答 |
| HaluMem | 评测基准 | 分解 extraction/update/QA 的记忆幻觉 | 要定位记忆错误发生在生命周期的哪一步 |
| MemoryAgentBench | 评测基准 | 检索、test-time learning、长程理解、选择性遗忘 | Agent Memory 是多能力系统而不是单一 QA |

---

# 5. 从这些分类可以看出四种根本分歧

## 分歧一：记忆是“知识”还是“经历”？

- RAG、GraphRAG、Mem0 更接近知识/事实；
- Hindsight、ReMe、Memori、AgentMem 更接近经历/行动；
- 对真正的 Agent 来说，两者都需要，但数据模型不能完全相同。

## 分歧二：记忆是“存储”还是“过程”？

- 传统 RAG 偏向存储和检索；
- Generative Agents、MemoryBank、A-MEM、LightMem、EverMemOS 偏向形成、巩固和重构；
- 后者提醒我们：写入后的管理可能比第一次写入更重要。

## 分歧三：记忆由“系统”管理还是由“Agent”管理？

- Mem0、Supermemory、MemoryOS 更偏系统自动管理；
- Letta、MemGPT、LangMem 更偏 Agent 主动调用和管理；
- 生产系统通常需要折中：Agent 提议，系统验证和执行。

## 分歧四：记忆首先追求“智能”还是“可信”？

- Memory OS、反思和自组织方法优先追求长期能力；
- AgentMem、Memoria、ReMe、Graphiti provenance 优先追求证据、版本和可审计；
- 真正可用的系统不能只选一边，而应让高价值记忆同时具备能力收益和证据链。

---

# 6. 对本项目的最终建议

本项目不应该复制某一个系统，而应该采用一个**双层、分型、可审计**的设计：

```text
第一层：记忆类型
  ├─ Profile / Fact       用户和项目的稳定事实
  ├─ Episodic             过去发生的任务和行动
  ├─ Procedural           可复用的技能、规则和解决方案
  ├─ Semantic             文档、实体、关系和领域知识
  └─ Working              当前线程和任务状态

第二层：记忆治理
  ├─ Source / Provenance  来源和证据
  ├─ Time                 发生时间和有效时间
  ├─ Confidence           置信度和验证状态
  ├─ Revision             版本、替代和回滚
  ├─ Scope / Permission   用户、项目、Agent、租户隔离
  └─ Lifecycle            激活、更新、归档、遗忘、删除
```

## 建议采用的核心理念

> **记忆不是聊天记录的仓库，而是 Agent 在证据约束下，将事实、经历、技能和知识持续组织成可检索、可更新、可验证行动依据的系统。**

## 最小实现顺序

1. 先实现事件日志和可审计的 Memory Item；
2. 再实现 Profile/Fact、Episodic、Procedural、Semantic 四种长期记忆；
3. 用关键词+向量作为第一版检索，不急于引入完整时间图；
4. 加入 supersede、archive、forget、delete 和 provenance；
5. 最后再加入后台巩固、经验反思、图关系和多 Agent 共享。
