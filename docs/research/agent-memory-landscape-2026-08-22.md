# AI Agent 记忆系统与 RAG 调研报告

> 调研快照：2026-08-22（Asia/Shanghai）  
> 项目：AI Agent Memory System  
> 范围：GitHub 开源项目、官方文档、论文/预印本、评测基准与公开技术资料

## 0. 结论先行

### 一句话结论

现在的 Agent 记忆系统，已经从“把聊天记录放进向量数据库再做 top-k 检索”的简单 RAG，演化成一个包含 **写入、组织、检索、更新、冲突处理、压缩/巩固、遗忘、权限和评估** 的独立系统层。

### 当前主流形态

1. **短期/工作记忆**：当前线程的消息、工具结果、任务状态和 scratchpad，通常由 checkpointer 或上下文管理器保存。
2. **事实/画像记忆**：从对话中抽取用户偏好、身份、约束、稳定事实，通常以结构化 JSON、profile 或原子事实保存。
3. **经历/轨迹记忆**：保存 Agent 做过什么、采取了哪些动作、结果如何、哪些路径失败，常用于相似任务复用。
4. **语义/知识记忆**：文档、实体、关系、规则和领域知识，通常使用向量索引、全文检索、知识图谱或混合索引。
5. **程序/技能记忆**：可复用的工作流、工具使用方法、成功案例、项目规则和技能文件。
6. **时间与版本记忆**：不仅记录“现在是什么”，还记录“什么时候成立、什么时候被替代、历史上曾经是什么”。

### RAG 与 Agent Memory 的边界

- **RAG** 主要解决：给模型补充外部知识，重点是“当前问题需要哪些资料”。
- **Agent Memory** 主要解决：让 Agent 在不同时间、会话、任务和工具之间保持连续性，重点是“什么值得留下、如何演化、何时取回、如何证明”。
- 生产系统通常不是二选一，而是 **Memory + RAG 的统一 Context Layer**：RAG 负责文档/知识库，Memory 负责个体化状态、经历、偏好和任务经验。

### 本项目最值得吸收的设计方向

建议从一个“可审计的混合记忆层”开始，而不是直接复制某一个产品：

```text
原始事件/对话/工具轨迹
        │
        ├─ 原始日志：不可变、可回放、可追溯
        │
        └─ 候选记忆 → 验证/去重/冲突检测 → 结构化记忆
                                      │
                     ┌────────────────┼────────────────┐
                     │                │                │
                 语义索引          关键词索引        实体/时间图
                     └────────────────┼────────────────┘
                                      │
                  查询路由 → 混合召回 → 重排序 → 最小充分上下文
                                      │
                                Agent 使用与反馈
                                      │
                         更新 / 巩固 / 归档 / 遗忘 / 删除
```

---

## 1. 调研范围、纳入标准与停止标准

### 1.1 纳入标准

本报告优先纳入满足以下任一条件的系统或方法：

- GitHub 上具有明显社区信号：较高 Star/Fork、持续提交、近期 release、较多集成或活跃文档。
- 被 2024—2026 年的 Agent Memory survey、技术地图或论文反复引用。
- 论文提出了可复用的记忆机制、生命周期、存储表示或评估基准。
- 能处理跨会话、长时段、更新、时间推理、任务经验、遗忘或 Agent 轨迹，而不只是普通对话缓存。

### 1.2 不纳入或只作为背景

- 纯向量数据库、全文搜索引擎或图数据库本身：它们是存储/检索基础设施，不自动等于 Agent Memory。
- 只有聊天历史缓存、没有跨会话或长期管理能力的简单组件。
- 仅有营销页面、缺少可验证文档/代码/论文的项目。
- 只在单个个人博客或社交平台出现、且没有足够代码或论文证据的小项目。

### 1.3 停止标准

本轮调研按以下标准停止，而不是声称穷尽互联网全部项目：

- 已交叉检查多个当前的 Agent Memory 项目地图/awesome list、GitHub 官方仓库和论文综述。
- 已覆盖主要架构家族：朴素/混合 RAG、摘要/压缩、分层虚拟上下文、画像/事实记忆、知识图谱/时间图、Agent 自管理记忆、经验/反思记忆、文件系统记忆、Memory OS、离线巩固与轻量模型记忆。
- 已整理 15 个具有代表性的系统/框架，以及 15 个基础论文、方法论文或评测基准。
- 连续两轮检索没有再发现“同时满足热门/活跃且代表新架构家族”的高信号项目；新增项目主要是已有方法的包装、适配器或相似实现。

因此，本报告是一个**面向架构选型和项目设计的高信号地图**，不是 PRISMA 意义上的穷尽式系统综述。

---

## 2. 先建立共同语言：Agent 的记忆到底是什么

### 2.1 从 CoALA 看记忆类型

CoALA（Cognitive Architectures for Language Agents）把语言 Agent 描述为：模块化记忆、结构化动作空间和决策过程。它给出了一个很适合工程设计的四分法：

| 类型 | 记住什么 | 工程形态 |
|---|---|---|
| Working memory | 当前目标、观测、工具结果、临时推理状态 | 上下文窗口、线程 state、scratchpad |
| Episodic memory | 过去发生的事件、任务轨迹、行动结果 | 会话、运行日志、案例、trajectory |
| Semantic memory | 世界事实、用户事实、实体与关系 | JSON/profile、向量库、知识图谱 |
| Procedural memory | 如何完成任务、规则、技能、工具策略 | prompt、skill、workflow、代码、策略文档 |

这个分类的价值不在于必须使用四个独立数据库，而在于提醒我们：**不同类型记忆的写入时机、更新方式、检索问题和可信度不同**。把它们全部混成一张向量表，往往会导致检索噪声和更新困难。

### 2.2 用三个维度描述一个记忆系统

建议在本项目中用以下三个正交维度描述系统，而不要只说“短期/长期”：

1. **时间范围**：当前步骤、当前会话、跨会话、长期历史。
2. **表示载体**：原始文本、摘要、原子事实、JSON、向量、实体图、时间图、KV/激活/参数。
3. **控制策略**：开发者写入、模型热路径写入、后台抽取、Agent 自主管理、定时巩固、策略/规则控制。

### 2.3 记忆的最小生命周期

一个可运营的记忆系统至少要回答：

```text
observe → extract → validate → store → index → retrieve
        → use → reinforce/update → supersede/archive/forget/delete
```

其中最容易被忽略的是 `validate`、`update`、`supersede`、`forget` 和 `delete`。很多 demo 只实现了 `store + vector search`，这还不能算完整的长期记忆系统。

---

## 3. RAG 与 Agent Memory 的区别

### 3.1 经典 RAG

经典 RAG 的基本流程是：

```text
文档 → 切分 → embedding → 向量索引
查询 → embedding → top-k 召回 → 拼接上下文 → LLM 生成
```

它的强项是：

- 能把模型参数之外的知识接入推理。
- 知识库可更新，不必重新训练模型。
- 来源可保留，适合企业文档、FAQ、规范和研究资料。
- 存储和检索边界相对清晰。

它的典型不足是：

- 默认把知识当成静态文档片段，而不是持续变化的状态。
- 对“旧事实被新事实替代”通常没有明确语义。
- 对跨会话身份、用户偏好、任务经验和工具结果不够自然。
- top-k 相似度不等价于“对当前 Agent 决策最有用”。
- 只做检索，不自动解决什么应该写入、何时压缩、如何遗忘和如何审计。

### 3.2 Agent Memory

Agent Memory 多了一个“经历和状态的演化环”：

```text
交互/行动 → 记忆候选 → 选择性写入 → 组织/巩固 → 检索/使用
                          ↑                      │
                          └── 反馈、修正、冲突、遗忘 ──┘
```

它需要处理更多问题：

- 这是用户的长期偏好，还是只在这一次对话里成立？
- 这是 Agent 的观察，还是 Agent 自己推断的结论？
- 新事实是否 supersede 旧事实？旧事实是否要保留为历史？
- 这个工具调用成功了吗？这条经验能否迁移到相似任务？
- 记忆来源是否可信？是否包含 prompt injection 或恶意指令？
- 用户要求删除后，原文、摘要、embedding、图关系和缓存是否都删除？

### 3.3 最实用的判断

- **只读知识库**：优先按 RAG 设计。
- **跨会话用户个性化**：需要 profile/fact memory。
- **长任务恢复和工具经验复用**：需要 episodic/procedural memory。
- **强时间关系、实体关系和频繁更新**：需要 temporal/knowledge graph。
- **长运行 Agent**：需要 working memory、checkpoint、压缩和后台 consolidation。
- **研究型或持续学习系统**：可以进一步考虑 Memory OS、参数/激活记忆和可学习的 memory policy。

---

## 4. 代表性系统与框架

> “流行/活跃”是相对判断。GitHub Star、Fork、提交数量、release、文档更新和生态集成都会变化；表中只记录本次快照能验证的信号，不把厂商自报 benchmark 当作独立事实。

### 4.1 工程系统总览

| 系统 | 核心定位 | 主要表示 | 写入/管理 | 检索 | 适合场景 | 主要注意点 |
|---|---|---|---|---|---|---|
| **Mem0** | 通用 Agent 长期记忆层 | 事实、profile、向量、图、KV | 从对话抽取 salient facts；支持更新/删除/多层 scope | 语义 + 关键词 + 实体/图等混合方向 | 快速给现有 Agent 加跨会话记忆 | LLM 抽取成本、冲突策略和厂商 benchmark 需独立复核 |
| **Letta / MemGPT** | 有状态 Agent runtime | core memory blocks、archival/recall memory、线程状态 | Agent 通过工具自主管理上下文和记忆；支持 block attach/detach | Agent 主动 paging、工具查询 | 长运行 Agent、个性/工作状态、可控上下文 | 记忆能力与 Agent runtime 绑定较深；工具循环会增加延迟 |
| **Graphiti / Zep** | 动态时间上下文图 | entity、edge/fact、episode、validity window | 增量构图；旧事实失效但历史保留；episode 可追溯 | 语义 + BM25 + 图遍历 + 时间查询 | 动态企业知识、实体关系、历史状态 | 图抽取和实体解析复杂；自托管需要图数据库等基础设施 |
| **Cognee** | 开源 AI memory platform | 向量 + 知识图谱 + ontology + session cache | `remember / recall / forget / improve`；session memory 后台同步图 | 自动路由、图/向量搜索 | 文档、组织知识、跨 Agent 共享、知识图谱 | 系统较宽，部署依赖和云/本地能力需按版本核验 |
| **Hindsight** | 学习型 Agent Memory | temporal、semantic、entity memory；facts/observations/experience | retain 时结构化事实、实体解析；支持 reflect；后台/服务化 | semantic + BM25 + entity graph + temporal，再 rerank | 需要时间推理、实体关系、经验和 MCP 的 Agent | 复杂管线增加写入成本；性能与效果依赖内部模型/配置 |
| **Supermemory** | Memory + RAG/context cloud | facts、profiles、documents、connectors、ontology | 自动提取、冲突更新、过期遗忘、连接器同步 | 混合 RAG + personalization | 个人助手、跨产品连接器、托管服务 | 很多能力偏平台化；SaaS 依赖、数据治理和可迁移性需评估 |
| **LangGraph Store / LangMem** | Agent framework 原生记忆 | namespace/key 下的 JSON document | 工具热路径管理，或后台 memory manager 抽取/整合 | store semantic search + 自定义过滤 | LangGraph/LangChain 生态，需自己组合策略 | 基础原语强，但最终记忆 schema、冲突和治理仍由应用负责 |
| **LlamaIndex Memory** | Agent 工作流记忆 | FIFO 消息、MemoryBlock、可扩展长期记忆 | `put/get`；token 限制后归档/压缩；可自定义 | 最近消息、summary、vector/自定义 memory | LlamaIndex Agent/Workflow、RAG 应用 | `ChatMemoryBuffer` 已 deprecated，需跟随新版 Memory API |
| **MemoryOS** | OS-inspired 个性化记忆 | 短期/中期/长期 memory | Storage、Updating、Retrieval、Generation 四模块 | 分层召回 | 个性化聊天、MCP 接入、研究复现 | 论文/仓库主张的 benchmark 需关注数据和配置可比性 |
| **MemOS** | Memory Operating System | text/tree/preference/skill/KV/LoRA 等 MemCube | scheduler、lifecycle、version、compose/migrate/fuse | 多类型记忆调度和检索 | 研究型 Memory OS、长期知识/参数/激活统一治理 | 范围很大，落地复杂度和实际收益需要分模块验证 |
| **ReMe** | 文件+向量记忆管理工具 | daily cards、digest、原始 JSONL、vector memory | session → daily note；dream → 长期 digest；保留原始来源 | 文件搜索 + 向量搜索 | Coding agent、个人本地记忆、可读审计 | 文件语义与索引一致性、并发和多租户需自行设计 |
| **LightMem** | 轻量、低成本记忆增强 | sensory/STM/MTM/LTM、主题组 | 轻量在线压缩 + 主题短期记忆 + sleep-time offline consolidation | 粗召回 + 一致性 rerank | 追求低延迟、低 token 和本地/小模型 | 质量依赖小模型和巩固策略；不是“零模型成本” |
| **A-MEM** | Zettelkasten 式 Agentic Memory | 结构化 note、tag、keyword、link | 新 note 生成；历史 note 动态链接和演化 | 语义/关键词 + linked notes | 需要关联发现、知识网络和动态组织 | LLM 驱动的链接/演化可能引入错误或漂移 |
| **EverMemOS** | 自组织长期记忆 OS | MemCell、MemScene、profile、foresight | episodic trace → semantic consolidation → reconstructive recall | scene 引导的 Agentic retrieval | 长期对话、画像、前瞻性提示 | 较新，需区分论文结果、开源实现和产品成熟度 |
| **Memori** | Agent trace 的结构化记忆服务 | facts、decisions、constraints、actions、outcomes + rolling summaries | 从消息和执行轨迹抽取；后台同步；显式 recall | 精确结构化 recall + summary | 需要记住“Agent 做了什么”，而非只记住对话 | 服务依赖、scope 与 trace 脱敏需重点审查 |
| **memU** | 文件系统/主动式 Agent 记忆 | 层级文件、结构化 memory item、wiki | 持续捕获用户意图并压缩为可复用记忆 | 层级访问和检索 | 24/7 proactive agent、跨设备个人记忆 | 需要严格控制自动捕获、隐私和噪声累积 |

### 4.2 重点系统解读

#### Mem0：最接近“插入现有 Agent”的通用内存层

Mem0 的典型产品化路径是：

```text
messages → memory.add()
query → memory.search()
results → 拼入 Agent context
```

它强调 user/session/agent 多级 scope，并逐步从纯事实抽取扩展到图和混合检索。适合把“跨会话用户偏好”和“历史事实”快速接入现有 Agent。其论文把动态抽取、整合、检索作为主线，并报告了相对 full-context 的成本/延迟收益；这些数字应理解为论文/厂商配置下的结果，不应直接当作所有部署的保证。

**值得借鉴**：统一 API、scope、事实抽取、混合检索、与大量 Agent 框架集成。  
**需要补强**：来源证据、冲突可解释性、写入前验证、删除传播和跨租户隔离。

#### Letta / MemGPT：把上下文当作“虚拟内存”

MemGPT 的核心想法是：有限 context window 类似物理内存，外部 archival memory 类似磁盘；Agent 通过函数调用将内容分页进出，并维护一部分始终可见的 core memory。Letta 延续了这一方向，memory blocks 可以独立创建、附加到多个 Agent、动态 detach，从而把“哪些信息对当前 Agent 可见”变成运行时控制面。

**值得借鉴**：working/core/archival 分层、Agent 自主管理、显式上下文压力和可共享 memory block。  
**需要补强**：不要只依赖模型自觉调用 memory tool；生产系统需要规则、预算、写入验证和可观测性。

#### Graphiti / Zep：把“变化中的事实”建模成时间图

Graphiti 的关键不是“使用图数据库”本身，而是把事实边建模为带有效期的关系，并保留产生事实的 episode：

```text
Episode → Entity → Fact/Relationship
                       ├─ valid_from
                       ├─ valid_to
                       └─ provenance
```

这样可以表达“现在住在上海”“之前住在北京”“某政策在某日期前有效”等问题。Graphiti README 还明确强调增量更新、混合检索、时间查询和 provenance，这使它比静态 GraphRAG 更适合动态 Agent memory。

**值得借鉴**：实体解析、时间有效窗口、旧事实失效而非粗暴删除、来源回溯。  
**需要补强**：实体合并错误、图膨胀、构图 LLM 成本、复杂查询的延迟和一致性。

#### Cognee：从数据管道到“知识脑”

Cognee 将 ingestion、ontology、graph、vector 和 session memory 组合在一起，并提供 `remember / recall / forget / improve` 这样比较完整的生命周期接口。它特别适合“多来源资料 + 组织知识 + Agent 共享”的场景，而不只是个人偏好记忆。

**值得借鉴**：数据导入、图/向量统一、session 到长期图的后台同步、forget/improve 生命周期。  
**需要补强**：系统边界大，应该按模块拆分验证，不要一开始把全套基础设施引入项目。

#### Hindsight：多策略召回与时间/实体/经验记忆

Hindsight 的公开文档把记忆分成世界事实、Agent 自身经验和观察，并将 semantic、keyword、graph、temporal 召回融合后再 rerank。它的设计代表了一个明显趋势：**记忆检索不再只靠一个 embedding，相似度只是多个信号之一**。

**值得借鉴**：多路召回、cross-encoder rerank、时间过滤、实体图、MCP memory server。  
**需要补强**：多路管线本身的成本、可解释性和故障降级要显式设计。

#### LangGraph Store / LangMem：将短期状态和长期记忆分开

LangGraph 将 thread-scoped checkpoint 与跨 thread 的 long-term Store 分开：

- checkpointer：保存当前线程的状态，适合恢复执行、human-in-the-loop 和短期上下文。
- store：以 namespace/key 组织跨会话 JSON memory，并可启用 semantic search。
- LangMem：提供 hot-path memory tools 和 background memory manager。

这是一种很实用的应用框架原语：**先把持久化状态和长期记忆的边界划清，再选择具体记忆策略**。

#### ReMe：可读文件是记忆，索引只是加速层

ReMe 的 Auto Memory 将每个 session 整理成 daily memory card，同时保留原始 JSONL 对话；之后由 dream 流程把 daily 材料沉淀为 digest，搜索同时覆盖 daily 和 digest。这类设计对 Coding Agent 很有吸引力：记忆不是不可读的向量，而是可以审阅、版本化、备份和人工修改的 Markdown 文件。

**值得借鉴**：原始事件与派生记忆分离、文件可读、来源链接、daily → digest 的分层巩固。  
**需要补强**：文件系统并发、索引失效、权限、规模增长和敏感信息处理。

#### MemOS / MemoryOS：把记忆提升为系统资源

MemoryOS 侧重分层存储、更新、检索和生成；MemOS 更进一步把文本、树、偏好、技能、KV/激活和参数级记忆纳入统一的 Memory OS，并用 MemCube 携带内容、来源、版本等元数据。

这一方向的意义在于：它尝试把“记忆”从一个向量库适配器升级为**可调度、可迁移、可版本化和可治理的系统资源**。但对于本项目的第一阶段来说，更适合吸收其数据模型和生命周期思想，而不是立即实现参数/激活记忆。

---

## 5. 主要记忆方法家族

### 5.1 原始消息/事件日志

**做法**：完整保留用户消息、Agent 回复、工具调用、工具结果和时间戳，必要时按 session/thread 分区。

**优点**：事实最完整、可回放、可审计、可以重新抽取。  
**缺点**：检索噪声大、上下文成本高、隐私风险大。

**适用**：作为不可变 source of truth，不建议直接作为唯一的长期记忆。

### 5.2 滑动窗口、裁剪和摘要

**做法**：只保留最近 N 条消息，或在接近 token 上限时压缩旧消息。

**优点**：实现简单、延迟低。  
**缺点**：容易丢失细节、摘要可能产生事实漂移、缺乏结构化更新。

**适用**：短期工作记忆的基础策略；需要与原始日志和长期抽取配合。

### 5.3 原子事实/Profile Memory

**做法**：把对话转换为“用户喜欢深色模式”“项目使用 PostgreSQL”这样的事实，并按 user/project/agent scope 保存。

**优点**：召回上下文小、可解释、适合个性化。  
**缺点**：抽取错误会污染长期记忆；事实粒度和冲突更新很难。

**关键设计**：每条事实必须有 source、event_time、confidence、scope、status 和 supersedes。

### 5.4 向量 RAG

**做法**：对原文、摘要、事实或案例 embedding，按 query 召回相似项。

**优点**：通用、生态成熟、适合语义相似。  
**缺点**：无法自然表达否定、时间、有效期、实体一致性和复杂关系。

**建议**：不要把所有记忆都塞成同一种 chunk；至少保留 lexical、metadata filter 和时间过滤。

### 5.5 混合检索与重排序

**做法**：并行使用向量、BM25/关键词、实体匹配、时间过滤、图邻居和结构化过滤，再用 RRF、cross-encoder 或 LLM rerank 融合。

**趋势**：这是当前产品系统最普遍、最实用的增强方向之一。它承认不同查询需要不同信号：

- 人名、代码符号、ID：关键词更强。
- 同义表达：向量更强。
- “之前/现在/截止某日”：时间过滤更强。
- “A 与 B 的关系”：图遍历更强。
- “这条记忆是否真的回答问题”：reranker 更强。

### 5.6 知识图谱/时间图

**做法**：将实体、关系、事实和来源显式建模；时间图额外记录 valid time 或 transaction time。

**优点**：多跳、关系、历史状态、冲突处理和来源追踪更好。  
**缺点**：抽取、实体消歧、图更新、图膨胀和运维成本高。

**适用**：组织知识、人物/项目/产品关系、时间敏感业务和跨文档推理。

### 5.7 Agent 自主管理

**做法**：把 `remember/search/update/delete` 作为工具，让 Agent 自己决定何时读写。

**优点**：灵活，能把记忆作为推理动作。  
**缺点**：Agent 可能忘记调用、滥写、误删或被上下文中的恶意指令影响。

**建议**：采用“Agent 提议 + 系统策略确认”的双层控制，而不是完全放权。

### 5.8 反思与经验记忆

**做法**：从多次行动轨迹抽象出更高层的经验、策略、失败模式或可复用步骤。

**优点**：不止记住事实，还能改进任务执行。  
**缺点**：反思可能把错误推理固化成错误规则；经验迁移存在分布偏移。

**建议**：经验记忆必须带任务类型、环境、工具版本、结果证据和适用边界。

### 5.9 分层与离线巩固

**做法**：在线只做低成本记录和粗筛，后台再做压缩、聚类、事实合并、图构建和 profile 更新。

**优点**：降低在线延迟和 token 成本，适合长时间运行。  
**缺点**：记忆存在最终一致性；后台失败可能导致读不到最新知识。

**代表方向**：LightMem、ReMe daily/digest、LangMem background manager、Cognee session→graph。

### 5.10 文件系统/可读记忆

**做法**：把记忆存为 Markdown/JSON/SQLite，向量或全文索引只作为派生加速层。

**优点**：可读、可改、可备份、可进入 Git、便于审计。  
**缺点**：规模、并发、权限和索引一致性要自行处理。

**适用**：Coding Agent、个人 Agent、项目知识、人工参与的记忆治理。

### 5.11 参数/激活级记忆

**做法**：将知识写入模型参数、LoRA、KV cache 或其他激活/适配器层。

**优点**：推理时不必反复拼接文本，可能获得更强的行为适应。  
**缺点**：更新、回滚、解释、隔离、删除和多租户极其复杂。

**判断**：这是 Memory OS 的研究前沿，不是本项目 MVP 的优先级。

---

## 6. 论文与评测基准路线

### 6.1 基础论文

| 论文/基准 | 年份 | 核心贡献 | 对本项目的启发 |
|---|---:|---|---|
| Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks | 2020 | 参数记忆 + 外部非参数记忆的 RAG 范式 | RAG 是外部知识接入基础，但不等于 Agent Memory |
| Generative Agents | 2023 | memory stream、重要性/新近性/相关性检索、reflection、planning | 记忆不只存储，还要抽象和反思 |
| MemoryBank | 2023 | 基于遗忘曲线的更新、强化与遗忘 | 记忆需要生命周期和衰减机制 |
| MemGPT | 2023 | OS-inspired virtual context、分层记忆、paging | working/core/archival 分层和上下文压力 |
| CoALA | 2023 | working/episodic/semantic/procedural 记忆分类 | 为工程设计提供共同语言 |
| A Survey on the Memory Mechanism of LLM-based Agents | 2024 | 系统总结 Agent Memory 机制和评估 | 说明领域已从零散技巧走向独立研究方向 |
| HippoRAG | 2024 | LLM + knowledge graph + Personalized PageRank | 图结构可以补足多跳长期记忆 |
| LoCoMo | 2024 | 超长期、多 session、时间/因果/多模态对话评测 | 不能只用单轮 QA 评估记忆 |
| LongMemEval | 2024/2025 | 信息抽取、多 session 推理、时间推理、知识更新、abstention | 记忆系统必须测更新和拒答 |
| Zep | 2025 | temporal knowledge graph architecture | 动态事实需要有效期和历史关系 |
| Mem0 | 2025 | 可扩展长期记忆，事实/图记忆和生产评估 | 抽取、整合、检索、成本一起评估 |
| A-MEM | 2025 | Zettelkasten 式动态链接和 memory evolution | 记忆可形成互联网络，不必固定 schema |
| MemOS | 2025 | 将记忆视为系统资源，统一文本/激活/参数级 memory | 记忆需要版本、调度、迁移和治理 |
| LightMem | 2025/2026 | 轻量在线处理 + offline sleep-time consolidation | 将质量和在线成本解耦 |
| HaluMem | 2025 | 分别评估 extraction/update/QA 阶段的 memory hallucination | 要定位错误发生在哪个操作，而非只看最终答案 |
| MemoryAgentBench | 2025/2026 | accurate retrieval、test-time learning、long-range understanding、selective forgetting | 记忆评测需要多能力拆分 |
| EverMemOS | 2026 | MemCell→MemScene→reconstructive recall 的自组织记忆 OS | 由原子经历逐步巩固成语义场景 |

### 6.2 评测维度

不要只报告“最终回答对不对”。建议至少拆成：

1. **写入保真度**：事实是否忠实于原始交互？是否凭空添加信息？
2. **更新正确性**：新信息能否替代旧信息？历史是否保留？
3. **检索召回**：正确记忆是否在候选集合中？
4. **时间推理**：能否回答过去、当前、未来和时间范围问题？
5. **多跳关联**：能否通过实体/事件/关系拼出答案？
6. **选择性遗忘**：过期或要求删除的内容是否不再影响回答？
7. **拒答/不确定性**：没有证据时是否拒绝编造？
8. **下游任务收益**：记忆是否真的提升任务成功率，而不是只提高记忆 QA 分数？
9. **系统代价**：写入 LLM 次数、读取延迟、token、索引增长和后台成本。
10. **治理正确性**：来源、权限、租户隔离、删除和审计是否可靠。

### 6.3 对 benchmark 数字的使用原则

各项目的 LoCoMo、LongMemEval、BEAM 等数字通常来自不同的：

- 模型和 embedding 模型
- 记忆写入 prompt
- chunk/session 切分
- top-k 和 rerank 预算
- 答案生成模型
- judge 模型和 judge prompt
- 是否使用厂商托管优化

因此本项目不应简单按“最高分排名”选型。正确做法是固定配置，公开完整 pipeline，并同时报告均值、方差、延迟、token、写入成本和失败案例。

---

## 7. 当前领域的共同趋势

### 7.1 从“记忆库”转向“记忆管理器”

越来越多系统把重点放在 `write/manage/read` 闭环，而不是只提供 `add/search` 两个接口。管理器通常包含：

- 重要性筛选
- 去重和实体合并
- 冲突识别与 supersede
- 时间有效期
- 归档和遗忘
- 后台巩固
- provenance 和版本
- 权限、审计和删除

### 7.2 从单一向量检索转向混合检索

向量、BM25、实体、图、时间和 metadata filter 被组合使用。原因是不同类型的记忆问题需要不同的检索信号，单一 cosine similarity 很难同时解决名字、时间、关系和同义表达。

### 7.3 从“事实”转向“经历、策略和结果”

Coding Agent 和自动化 Agent 需要的不仅是用户偏好，还包括：

- 做过哪些修改
- 哪些命令成功/失败
- 某个项目的约束
- 某类错误如何排查
- 哪个工具参数在当前环境有效
- 哪些方案已经被否决以及原因

这推动 episodic/procedural memory 成为新的重点。

### 7.4 从在线昂贵调用转向在线/后台分离

在线路径追求低延迟，后台路径负责高质量抽取、聚类、反思、图更新和巩固。LightMem、ReMe、LangMem background manager 等都体现了这一趋势。

### 7.5 从“全自动”转向“可审计、可编辑、可删除”

文件式记忆、结构化事实、provenance、版本链和 Memory OS 的共同方向是：记忆必须能够被人和系统检查。对于企业和个人数据，删除、权限和错误修正比“看起来像人一样记住”更重要。

### 7.6 MCP 成为记忆能力的分发接口

多个系统通过 MCP 将 `recall / retain / reflect / forget` 暴露给 Claude Code、Codex、Cursor、OpenHands 等不同 Agent 客户端。MCP 解决的是连接和工具暴露，不会自动解决记忆 schema、可信度、冲突和治理。

---

## 8. 对本项目的建议架构

### 8.1 第一阶段：先做“可验证的最小闭环”

不要一开始实现完整 Memory OS。建议先完成：

```text
1. Event Log       原始交互和工具轨迹
2. Memory Item     结构化记忆模型
3. Write Policy    写入候选、验证、去重、冲突
4. Hybrid Recall   关键词 + 向量 + metadata filter
5. Context Builder 生成最小充分上下文
6. Lifecycle       supersede / archive / forget / delete
7. Evaluation      固定数据集和失败案例回放
```

### 8.2 建议的 Memory Item 数据模型

```json
{
  "id": "mem_01J...",
  "scope": {
    "tenant_id": "tenant_a",
    "user_id": "user_123",
    "project_id": "project_memory_system",
    "agent_id": "agent_default"
  },
  "type": "semantic|episodic|procedural|working|profile",
  "content": "用户偏好使用中文并希望先理解设计再编码。",
  "summary": "中文协作；先分析设计后开发",
  "entities": ["user_123"],
  "source_refs": ["event_2026-08-22_001"],
  "event_time": "2026-08-22T08:30:00+08:00",
  "created_at": "2026-08-22T08:31:00+08:00",
  "valid_from": "2026-08-22T08:30:00+08:00",
  "valid_to": null,
  "confidence": 0.96,
  "importance": 0.82,
  "status": "active|superseded|archived|quarantined|deleted",
  "supersedes": [],
  "access_policy": {
    "visibility": "user|project|agent|tenant",
    "sensitivity": "normal|sensitive|restricted"
  },
  "embedding_ref": "vec_...",
  "revision": 1
}
```

### 8.3 原始数据与派生记忆必须分离

建议至少保留三层：

- `events/`：原始消息、工具调用和结果，不可变，带时间和来源。
- `memories/`：验证后的结构化事实、经历、策略和 profile。
- `indexes/`：向量、全文、实体图、时间图等可重建索引。

索引丢失可以重建；派生记忆错误可以重新抽取；原始事件保留了事实依据。

### 8.4 写入策略：系统建议 + Agent 提议

推荐两条路径：

- **Hot path**：当前任务中明确有价值的记忆，由 Agent 或规则提出，系统快速验证后写入。
- **Background path**：异步从 session/event 中抽取、合并、更新 profile、建立关系和总结经验。

不要让 Agent 直接自由修改数据库。更安全的流程是：

```text
Agent proposes memory operation
        ↓
Schema / policy / privacy validation
        ↓
Duplicate + conflict check
        ↓
Persist immutable event + versioned memory
        ↓
Build or refresh indexes
```

### 8.5 检索策略：先路由，再召回，再压缩

```text
query classification
  ├─ user preference/profile → profile store
  ├─ recent task state → thread/checkpoint
  ├─ past successful workflow → episodic/procedural store
  ├─ time/entity relation → temporal graph
  └─ domain documents → RAG index

candidate retrieval
  → metadata filter
  → lexical + vector + graph + time signals
  → rerank
  → deduplicate
  → provenance-aware context compression
```

返回的不是“top-k 原始 chunk”，而是带来源、时间、状态和置信度的最小充分证据。

### 8.6 必须从第一天设计的治理能力

- 记忆写入白名单和敏感信息检测。
- Prompt injection / memory poisoning 防护。
- 用户、项目、Agent、租户 scope 隔离。
- 记忆查看、修改、撤销和删除 API。
- 删除传播到原文、派生摘要、embedding、图边和缓存。
- 记忆读取日志：谁在什么时候读取了什么。
- 低置信度记忆 quarantine，而不是直接进入高优先级上下文。
- 记忆来源和模型版本可追踪。

---

## 9. 推荐实施路线

### Milestone 1：文件优先的可审计 MVP

- JSONL event log + Markdown/JSON memory items。
- SQLite FTS5 或 BM25 做关键词检索。
- 可选本地 embedding 做语义检索。
- 手动/显式 `remember`、`search`、`forget`、`inspect`。
- 先支持 user/project/agent 三种 scope。

### Milestone 2：自动抽取与生命周期

- 对话结束后台抽取候选事实和经历。
- schema validation、去重、冲突检测、supersede。
- 记忆版本和 source refs。
- 时间字段和过期策略。

### Milestone 3：混合检索与任务经验

- 关键词 + 向量 + metadata filter。
- 任务/工具/结果/失败原因的 episodic/procedural memory。
- rerank、上下文预算和 provenance-aware compression。

### Milestone 4：图与评估

- 实体解析和关系图。
- temporal validity window。
- LoCoMo、LongMemEval、MemoryAgentBench 子集和自有 coding-agent 数据。
- 线上 recall hit rate、memory write quality、latency/cost dashboard。

### Milestone 5：后台巩固与多 Agent 共享

- daily/session → digest/profile/skill。
- 经验合并和定时 reflection。
- 多 Agent 共享记忆、访问控制和冲突协商。
- 视实际收益再考虑 Memory OS、激活或参数级记忆。

---

## 10. 主要风险与反模式

1. **把所有聊天都写入长期记忆**：噪声、成本和错误会累积。
2. **只有向量没有结构**：时间、身份、关系和冲突无法可靠处理。
3. **只有摘要没有原文**：无法审计，也无法纠正摘要幻觉。
4. **只看最终 QA 分数**：看不到错误来自抽取、更新、检索还是生成。
5. **过度依赖 Agent 自己调用记忆工具**：忘记调用、滥写、误删都很常见。
6. **用厂商自报 benchmark 直接排名**：配置、模型和 judge 不同，数字不具备直接可比性。
7. **没有删除和权限设计**：一旦上线个人或企业数据，返工成本极高。
8. **没有记忆版本与来源**：错误一旦进入长期记忆，就很难定位和撤销。
9. **在线路径过重**：每轮都进行多次大模型抽取和图更新，最终延迟不可接受。
10. **把记忆和 Agent runtime 强绑定**：未来更换模型、框架或客户端时迁移困难。

---

## 11. 本轮调研覆盖结果

### 已覆盖的系统家族

- 经典/混合 RAG：RAG、GraphRAG、LangGraph Store、LlamaIndex Memory
- 生产化事实记忆：Mem0、Supermemory、Memori
- Agent runtime / 虚拟上下文：Letta / MemGPT
- 时间知识图：Graphiti / Zep
- 图+向量知识脑：Cognee、HippoRAG 相关路线
- 记忆 OS：MemoryOS、MemOS、EverMemOS
- 文件/个人/Coding Agent：ReMe、memU、MemoryOS local-first 类路线
- Agentic organization：A-MEM
- 高效在线/后台巩固：LightMem
- 评测与可靠性：LoCoMo、LongMemEval、HaluMem、MemoryAgentBench

### 结论

本轮已经满足预先设定的停止标准：主要架构家族均已覆盖，热门/活跃的代表性系统和论文已建立对照矩阵，且新增候选大多落在已有家族或缺少足够公开证据。后续不应继续无边界堆积项目清单，而应进入：

1. 选定本项目的目标场景；
2. 固定 Memory Item schema；
3. 设计最小可评估 MVP；
4. 用统一 benchmark 和真实任务回放做对照实验。

---

## 12. 参考资料

### 综述与概念框架

- [CoALA: Cognitive Architectures for Language Agents](https://arxiv.org/abs/2309.02427)
- [A Survey on the Memory Mechanism of Large Language Model based Agents](https://arxiv.org/abs/2404.13501)
- [Memory in the Age of AI Agents](https://arxiv.org/abs/2512.13564)
- [From Storage to Experience: A Survey on the Evolution of LLM Agent Memory Mechanisms](https://arxiv.org/abs/2605.06716)
- [Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers](https://arxiv.org/abs/2603.07670)

### 基础与方法论文

- [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- [MemoryBank: Enhancing Large Language Models with Long-Term Memory](https://arxiv.org/abs/2305.10250)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models](https://arxiv.org/abs/2405.14831)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956)
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413)
- [MemOS: A Memory OS for AI System](https://arxiv.org/abs/2507.03724)
- [LightMem: Lightweight and Efficient Memory-Augmented Generation](https://arxiv.org/abs/2510.18866)
- [EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning](https://arxiv.org/abs/2601.02163)
- [Lightweight LLM Agent Memory with Small Language Models](https://arxiv.org/abs/2604.07798)

### 评测基准

- [LoCoMo](https://arxiv.org/abs/2402.17753) / [GitHub](https://github.com/snap-research/locomo)
- [LongMemEval](https://arxiv.org/abs/2410.10813) / [GitHub](https://github.com/xiaowu0162/LongMemEval)
- [HaluMem](https://arxiv.org/abs/2511.03506)
- [MemoryAgentBench](https://arxiv.org/abs/2507.05257) / [GitHub](https://github.com/HUST-AI-HYZ/MemoryAgentBench)

### 代表性开源项目与官方资料

- [Mem0](https://github.com/mem0ai/mem0)
- [Letta](https://github.com/letta-ai/letta) / [Letta Docs](https://docs.letta.com/)
- [Graphiti](https://github.com/getzep/graphiti)
- [Cognee](https://github.com/topoteretes/cognee)
- [Hindsight](https://github.com/vectorize-io/hindsight)
- [Supermemory](https://github.com/supermemoryai/supermemory)
- [LangMem](https://github.com/langchain-ai/langmem) / [LangGraph Memory Docs](https://docs.langchain.com/oss/python/langchain/long-term-memory)
- [LlamaIndex Memory](https://github.com/run-llama/llama_index/blob/main/docs/src/content/docs/framework/module_guides/deploying/agents/memory.mdx)
- [MemoryOS](https://github.com/BAI-LAB/MemoryOS)
- [MemOS](https://github.com/MemTensor/MemOS)
- [ReMe](https://github.com/agentscope-ai/ReMe)
- [LightMem](https://github.com/zjunlp/LightMem)
- [A-MEM](https://github.com/agiresearch/A-mem)
- [EverMemOS](https://github.com/NetMindAI-Open/EverMemOS)
- [memU](https://github.com/NevaMind-AI/memU)

### 领域地图（用于候选发现，不作为单一事实来源）

- [Awesome Agent Memory — mnemoverse](https://github.com/mnemoverse/awesome-agent-memory)
- [Awesome Agent Memory — Snseam](https://github.com/Snseam/awesome-agent-memory)
- [Awesome Agentic Memory](https://github.com/Anandesh-Sharma/awesome-agentic-memory)
