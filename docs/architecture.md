# 架构说明

## 1. 项目边界

本项目是供 Coding Agent 使用的本地外部记忆基础设施。当前 Phase 1 的对象是**代码库语义索引**，不是通用用户、会话或任务记忆系统。核心能力以独立 Python API 提供，不绑定具体 Coding Agent runtime；MCP 服务（`memory-mcp`）和概念网络页面（`memory-web`）是围绕该 API 的产品化适配层。

第一版验收以 Python 代码为主，使用 SQLite、FTS5 和 JSON 作为基础存储与检索能力。TypeScript/JavaScript Tree-sitter 支持属于实验性扩展，尚未纳入第一版验收的语言范围。

## 2. Phase 1 数据流

```text
source file
  -> source facts (AST symbols, signatures, locations, digest)
  -> model-generated concept drafts
  -> local evidence anchoring and validation
  -> ConceptCard JSON
  -> SQLite FTS5 derived index
  -> keyword query over cards
  -> matched cards + explanations + short source evidence
```

当前系统已经实现源码读取、源码事实提取、文件级概念生成、证据绑定、JSON 序列化、SQLite FTS 检索、缓存增量更新、文件级增量重扫（`file_state` 表按 mtime/size/摘要跳过未变文件；改动文件的新卡片经同文件匹配沿用旧 card_id，真实使用边不被清零）和敏感文件审计，并通过 MCP 工具（`scan_codebase` / `search_concepts`，后者 query 检索 / card_ids 取卡双模式）向 Coding Agent 暴露。

## 3. 概念卡片和证据契约

概念卡片是源码之上的语义索引、压缩表示、导航和解释层，不替代源码，也不是最终答案。模型负责概念名称、定义、背景和候选证据摘要；本地解析器负责验证文件路径、符号名称、行号范围和源码摘要。

卡片至少应保留：

- `name`、`definition`、`background`、`background_concepts`；
- 文件、模块和证据覆盖范围；
- `evidence_symbols`、`evidence_locations`；
- `source_excerpt`、`source_digest`；
- 生成模型/版本、提示词版本、生成配置和验证状态。

没有可定位源码证据的语义内容不能伪装成源码事实。默认上下文包返回概念卡片、匹配解释和短源码片段；完整源码通过按需读取接口加载。

## 4. 索引与检索

第一阶段使用 SQLite FTS5 作为本地、可复现、可解释的 lexical 检索层。检索结果按概念卡片评分，同时保留触发召回的概念和源码证据。

向量检索、复杂重排序、概念关系和 spreading activation 属于后续研究方向，不在当前默认路径中。

## 5. 在线模型和复现

系统本身在本地运行。普通源码默认允许发送给在线模型；敏感文件必须显式加入白名单。运行应记录发送文件清单、模型、时间和配置，并保留离线 fallback。API key 只能从环境变量读取。

卡片缓存键至少包含：

```text
source_digest
model_name / model_version
prompt_version
generation_config
```

源码或生成条件变化时重新生成；未变化时复用已有结果并保留版本信息。

## 6. 明确非目标

当前 Phase 1 不实现：

- 通用用户/会话/跨项目记忆；
- 自动生成最终代码答案；
- 未经验证模型关系参与正式检索；
- embedding/向量数据库作为首要方案；
- hook 式拦截注入等侵入性集成（MCP 工具调用跑通后再考虑）；
- 通用记忆的写入、冲突、归档、遗忘和权限生命周期。

## 7. 未来架构方向

基础概念检索在真实 coding agent 会话中验证后，再考虑：

```text
候选概念关系
  -> 源码事实/规则验证
  -> 关系质量评估
  -> 图检索和激活传播
  -> 项目架构决策与问题历史
  -> 当前任务状态记忆
```

通用 Agent Memory 的生命周期和多类型记忆仍是长期研究方向，不是当前架构的已实现部分。
