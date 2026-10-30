---
name: pytsa-study-config
description: Generate traceable PYTSA study configurations from experimental designs, including data preparation, conversions, native analysis, statistical plans, and operator instructions. Use when a study must be mapped to PYTSA; keep missing inputs pending and separate native support from external scripts.
---

# PYTSA 实验设计配置 Skill

当用户要把实验设计转成 PYTSA 可执行配置时使用。本技能服务于未来具体研究；没有设计或数据时，只整理缺口，不捏造真实配置。

## 工作方式

1. 读取 [输入采集与决策台账](references/input-design.md)，一次用普通话问完会影响可执行性的问题；逐项标记“已知 / 建议 / 缺失”，把无法推断的字段保留为 pending。
2. 读取 [预处理与转换路线](references/preprocessing-and-conversions.md) 与 [PYTSA 实际契约](references/pytsa-contract.md)，把原始、物理、探索和建模分支分开；只填写经实际入口确认存在的原生参数。
3. 读取 [统计方案](references/statistics.md)，从实验单位和重复结构选统计模型；原生能力不足时生成独立统计脚本及明确输入/输出契约。
4. 按 [配置包与主试操作](references/operator-guide.md) 生成目录、参数/决策台账、数据字典、操作卡、证据清单以及必要的转换/统计脚本。
5. 阅读 [合成实例和验证边界](references/synthetic-example.md) 以了解有限示例的运行方式；使用 [官方来源](references/sources.md) 核对重采样、滤波和统计 API。运行范围适用的 scripts/inspect_contract.py 和 scripts/check_bundle.py，只报告它们确实检查的内容。

## 必须遵守

- 每个参数写清：路径、值、单位、来源、依据、适用条件/分支、软件版本和验证状态。建议值不能冒充用户事实。
- 源文件/被试 ID 按字符串读取，保留前导零。核对长/宽表、事件语义、时间列和时钟后再做合并或转换。
- 保留原始文件只读；转换脚本另存派生文件并记录输入/输出哈希、软件版本、命令和失败信息。
- PYTSA 参数模板只是界面设置载体；应用模板不会自动开始计算。模板壳有效、字段存在、统计计划合理和实际运行成功是不同状态。
- 若转换、参数或统计脚本无法在当前环境实际运行，标 NOT_RUN/BLOCKED，提供明确待办；不写“可运行”或“ready”。
- 窗口、时间点、特征行、事件、条件记录、交叉验证折都不能替代独立被试数。重复/配对结构必须保留。
- 不在同一分析家族里试到显著为止；不使用“正态性 p 值开关”选择模型或挑显著。提前定义估计量、对比、置信区间、效应量、缺失规则和多重比较家族。
- 只在适用阶段记录理由明确的关闭状态；不适用不是未验证或已通过。
- 任何 AI 若没有代码执行或 PYTSA 工具，只能给出配置文本与 NOT_RUN/pending 标签，不能宣称已在 PYTSA 跑过。

## 产物路由

生成真实研究包前，先判断研究输入是否足够。若设计、文件结构、单位、采样/事件时间或独立单位缺少关键项，输出草案并一次集中提问。按需读取上述 references；不要把所有统计方法机械套入每个研究。
