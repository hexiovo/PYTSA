# PYTSA 当前实际契约（读取基线：2026-10-31）

## 复核规则

本契约按 PYTSA 1.3.1 源码核对。后续执行技能时仍须先运行 scripts/inspect_contract.py --project-root <PYTSA目录>，再阅读目标版本的 README、UI 收集器、实际入口与操作指南。哈希用于标识版本，不证明正确性。AST 输出是配置键发现线索，不是完整 schema、运行验证或科学适用性证明。

## 原生数据入口：core.prepare_data(df, config)

- df 是非空 pandas DataFrame；需要一个或多个实际存在、可转数值的信号列。配置从对象读取；代码使用 cfg.get，未知配置键可能被忽略，因此不能将 AST 键列表当成闭合 schema。
- 当前步骤为：验证列/类型，建时间轴并排序，聚合重复时间，可选按秒分箱均值，处理缺失，执行探索预处理、Butterworth、demean/detrend/z-score/difference，再检查长度/缺失。实际 helper 还要以当前源码核对。
- 返回至少含 data、original、fs、time_unit、meta、warnings。data 是探索分支，original 保留物理量，但已去重/可能重采样；缺失箱在 original 保留。
- 若指定时间列，该列决定实际 fs；配置 fs 不一致会产生警告。明显不规则时间需要明确 resample。missing=drop 若有信号缺失会拒绝，因为会破坏等间距。前向填充/线性插值在探索分支，边界缺口不会被线性插值填掉。
- resample 参数按秒分箱，对信号做箱内均值；它不是抗混叠 polyphase 滤波。差分后时间戳代表一对样本中的后一个，首行去除。
- 预测使用 prepared["original"]，EDA 也使用 original；其物理尺度不等于未经过时间整理的原始记录。

## 皮电与事件入口：experimental.analyze_eda(prepared, column, config, event_df=None)

- 读取 prepared["original"]。单位必需为 uS/等价 microSiemens、mS、nS 或 a.u.；不会把电阻/电压直接当电导。
- fs 要求至少 2 Hz；截止频率须小于 Nyquist；clean=biosppy 要求 fs 大于 10 Hz。至少有一段 20 秒连续有效数据。失效样本会切段，不插值、不跨段滤波。
- 事件表需 time 数值列与 label；time 是从首个记录点开始的相对秒。输出区间、响应窗边界和重叠/越界处理按源码记录。
- 有效窗内无峰时 SCR count 为零；无效/不可判定事件不是零响应。事件 AUC、单位、无反应 denominator 和其它规则由返回元数据/源代码解释。
- a.u. 不可用基于 µS 的质量阈值。皮电指标不能直接诊断压力、情绪或疾病。

## 模板、UI 与运行配置

- workflow_ui._workflow_collect_parameters() 将 prepare、analysis、forecast、eda、event_mapping 分开收集。batch_options 仅在真实批处理入口使用。
- WorkspaceStore 包络使用 schema=pyt-workflow-template、schema_version=1、id、name、kind、parameters、创建/更新时间。kind 有 prepare / analysis / forecast / eda / combined。combined 参数包含上述分组。
- 模板包络保存和导出不代表算法参数均有效。操作指南明确：应用模板只更新界面，不自动开始计算；缺少列会有映射提示。操作人员要对照字典，不能盲点列映射/采样率/单位弹框。
- runtime_settings.py 的 workers 和 backend 为本机运行设置，不是实验分析参数。不要混进 study config。
- 项目未提供通用 run-config、validate、plan CLI，也未提供通用组间/重复测量推断入口。参数模板、项目、批处理有各自流程，不要拼造通用命令。
- 没有通用推断支持时，按设计生成单独统计计划和统计脚本。Statsmodels AnovaRM 仅覆盖平衡纯被试内设计，不含 between 效应，也没有球形性修正；不能代替混合设计模型。
- 真实模板通过 WorkspaceStore 外壳检查应在隔离工作区保存/导出；不能将 _runtime 或 workers/backend 写成研究参数。模板应用不自动运行。

## 真实项目的复核顺序

查看 README 版本与散列；确认 UI collector 到核心入口的调用链；从被保护临时 workspace save/export 一个实际 native shell；确认具体 keys/constraints；用合成 fixture 调实际 API；最后才报告原生执行 PASS。AST 仅做目录线索。若界面/数据映射、所需依赖或运行证据缺失，写 BLOCKED/NOT_RUN，不作 ready 声明。
