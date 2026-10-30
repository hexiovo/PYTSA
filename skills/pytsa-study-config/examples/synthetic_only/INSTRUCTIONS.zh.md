# 合成示例说明

此目录只包含 SYNTHETIC_ONLY 工程演示，不是真实实验数据或分析结果。运行 run_example.py 需指定本地 PYTSA 源码根目录；脚本直接调用当前源码的 core.prepare_data 和 experimental 入口，另外使用 SciPy 配对 t 检验。

对照 expected-manual.json 检查 ms 到 s 的事件转换和配对统计公式。输出目录由命令参数指定，默认是相对路径 synthetic-output。运行失败时保留 traceback；不要把依赖、阈值或单位静默改成“通过”。
