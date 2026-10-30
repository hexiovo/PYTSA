# 未来研究配置包和主试操作卡

## 目录契约

每项研究独立放入用户指定位置，原始数据只读。按需创建：
- config/native-template.json：PYTSA 参数模板草案。
- design/design.json 与 design/decision-ledger.csv：设计、假设、已知/建议/缺失与决定历史。
- data/data-dictionary.csv、data/preparation-checklist.md：列/单位/ID/时间说明与检查。
- transformations/transform-plan.json；仅需转换时才有 transformations/prepare_data.py。
- statistics/analysis-plan.json；仅需要推断脚本时才有 statistics/run_statistics.py。
- operator-card.zh.md：主试操作、预期现象、成功/失败标准与输出位置。
- evidence/manifest.json：实际输入、脚本、版本、命令、退出码、输出摘要/哈希和验证状态。
- 只有每个必要阶段能实际运行且验证后，才创建 run-study.ps1/shell 入口；不可包含虚构命令或静默继续错误阶段。

综合模板外壳当前以 schema=pyt-workflow-template、schema_version=1、kind=combined 和 parameters 为核心，参数组为 prepare/analysis/forecast/eda/event_mapping。须随目标版本再次核验。研究设计/转换/统计不要塞进 native parameters。

## 主试逐步操作卡

每一步写预计时间、打开哪个菜单/按钮、要选的文件/sheet/列、要核对的单位/采样率/时间/ID、预期屏幕或文件变化、成功信号、失败信号、日志/导出位置和回退点。

1. 把原件放入只读输入目录，确认文件名、大小、哈希和数据来源；另建工作副本。
2. 按字典选 CSV/TSV/TXT/XLSX、编码、分隔符和 sheet；核对预览首尾行、ID字符串前导零、列类型、时间跨度、缺失标记和键重复。
3. 选择时间/信号列、单位、时区、设备 fs；对照设备说明。由时间戳推算的 fs 与设备不符、时间不规则或发生重启时先暂停。
4. 按数据字典映射事件文件、时间列和标签；确认是起始点或起止区间、哪个时间为零和同步偏移。单位/列提示不清楚时不盲选。
5. 导入模板后逐项核对当前变量、采样率、单位和转换路径。应用模板只更新界面，不自动开始计算。完成映射后才由主试主动点击准备/分析。
6. 检查状态栏、日志、warnings、有效时长、行数、分支、事件数及实际参数回显；失败就停下，不导出给统计。
7. 检查结果数据字典、被试/事件/窗口行数、QC、NA/无响应0和聚合单位；再运行独立统计脚本并核对参数/结果路径。
8. 导出到新的带版本目录，保留 manifest 和日志；只把实际完成的项目标为 PASS。

## 失败恢复

- 停下消费错误产物的下游步骤，保留原文件、当前输出和 traceback。
- 标出首个出错阶段：解析/编码、sheet/列、ID/键、单位/校准、fs/不规则时间、事件同步、缺失/伪迹、参数边界、转换依赖、统计结构或写入权限。
- 记录原始错误、文件/脚本哈希、命令、运行环境、已排除原因和下一步；无证据不猜修复。
- 缺资料时回到 pending 问题；路线/估计量变更需更新设计决策，不局部偷换检验。
- 修复后只从受影响步骤重跑，重新记录来源与派生数据哈希，结果写新版本；不能与旧结果混用。
- 不能运行则 NOT_RUN/BLOCKED，交给主试一条明确下一步，不宣称已执行。

## 状态语义

DRAFT/PENDING 表示仍缺资料或未定；PASS 和 FAIL 只用于实际运行的具体检查；BLOCKED 表示依赖/权限/事实缺失；NOT_RUN 是尚未执行；NOT_APPLICABLE 必须说明无关理由。壳结构通过只证明壳结构。字段检查不证明参数有意义。统计设计审查不证明模型运行成功。单个样例通过不代表所有研究 ready。没有设计/数据时真实研究状态仍是 pending。
