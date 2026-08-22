# AI Agent Memory System

一个专门为 AI Agent 设计的记忆系统项目。

本项目的目标是让 Agent 能够以**可检索、可更新、可解释、可控**的方式管理长期记忆，并在多轮任务、多个会话和不同工具之间保持连续性。

## 项目目标

- 统一管理短期记忆、工作记忆和长期记忆
- 支持记忆的写入、检索、更新、合并、遗忘与归档
- 区分事实、经验、偏好、任务状态和原始对话记录
- 提供记忆来源、时间、置信度和版本等元数据
- 控制记忆污染、重复记忆、过期信息和隐私风险
- 保持存储层、检索层、推理层之间的低耦合

## 核心设计原则

1. **先验证再记忆**：不是所有上下文都应该被持久化。
2. **来源可追溯**：每条记忆都应尽量保留来源和形成时间。
3. **记忆可修正**：新证据可以更新、覆盖或撤销旧记忆。
4. **检索服务于任务**：优先返回与当前任务相关且可信的记忆。
5. **默认最小化存储**：避免不必要地保存敏感信息。
6. **策略与实现分离**：记忆策略不应被某个具体数据库或模型锁定。

## 预期模块

```text
src/
├── ingestion/      # 从对话、工具调用和任务结果中提取候选记忆
├── memory/         # 记忆模型、生命周期和管理策略
├── retrieval/      # 关键词、向量、混合和重排序检索
├── storage/        # 文件、关系型数据库、向量数据库等存储适配
├── policies/       # 写入、更新、遗忘、隐私和权限策略
└── evaluation/     # 记忆质量、检索效果和任务收益评估

tests/              # 单元测试、集成测试和评估测试
docs/               # 架构、设计决策和实验记录
```

当前已进入第一阶段实现；后续记忆关系、组合和评估能力仍按路线逐步增加。

## 开发方式

- 先明确记忆模型和生命周期，再实现存储适配
- 每次只引入一个可验证的能力
- 优先编写可重复的测试和评估样例
- 对关键设计记录决策背景、替代方案和已知限制

## 路线图

- [ ] 定义统一的记忆数据模型
- [ ] 实现最小的记忆写入与读取接口
- [ ] 增加记忆生命周期管理
- [ ] 增加混合检索与重排序
- [ ] 建立记忆质量评估集
- [ ] 增加隐私、权限和审计能力

## 调研资料

- [AI Agent 记忆系统与 RAG 调研](docs/research/README.md)

## 状态

项目已完成初始化，第一阶段“代码理解与概念编码”已实现，下一步可由用户进行功能与语义验收。

## 第一阶段：代码理解与概念编码

当前第一阶段采用“文件级语义概念”闭环：输入一个 Python 文件，默认目标生成约 3 个概念，硬上限为 9 个。概念名由 Qwen-Flash 生成，背景由模型阅读源码后提炼；AST 只用于提供可验证的符号和行号证据。

```bash
pip install -e '.[dashscope]'
export DASHSCOPE_API_KEY='在 shell 中设置，不要写入代码或仓库'
PYTHONPATH=src python3 -m memory_system.cli path/to/project \
  --model qwen-flash \
  --target-concepts 3 \
  --max-concepts 9 \
  --output concepts.json \
  --database concepts.sqlite
```

Python API 示例：

```python
from memory_system import ConceptStore, analyze_path

cards = analyze_path("path/to/project")
with ConceptStore("concepts.sqlite") as store:
    store.upsert_cards(cards)
    results = store.search("用户")
```

概念卡片至少包含概念名、概念定义、源码背景、背景概念、源码位置、源码摘要和来源摘要。没有 `DASHSCOPE_API_KEY` 时会使用离线安全回退，方便语法和数据流测试；正式概念归纳应设置 Qwen-Flash 的环境变量。
