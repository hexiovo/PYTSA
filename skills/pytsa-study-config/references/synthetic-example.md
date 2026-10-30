# SYNTHETIC_ONLY 最小示例

## 运行

examples/synthetic_only/run_example.py 生成人工皮电信号和五名被试的配对测量。它通过 PYTSA markers_to_events 将已知毫秒事件转成相对秒，调用实际 core.prepare_data 与 experimental.analyze_eda，再执行 SciPy 配对 t 检验。没有真实研究数据。在项目根目录运行：

    .venv\Scripts\python.exe -X utf8 -B .\deliver\skills\pytsa-study-config\examples\synthetic_only\run_example.py --project-root . --out .\deliver\skills\pytsa-study-config\examples\synthetic_only\output

配对表有十行，但五个唯一 subject ID（001 到 005）才是独立配对数。重复的 ID-condition 键会失败。

## 独立手算基准

差值为 [1,2,3,4,5]：
- 平均差为 3
- 样本 SD 为 sqrt(2.5) = 1.5811388300841898
- SE 为 sqrt(2.5/5) = sqrt(0.5) = 0.7071067811865476
- t = 3 / sqrt(0.5) = sqrt(18) = 4.242640687119285
- df = 4
- Cohen dz = 3 / sqrt(2.5) = 1.8973665961010275

双侧 p 用自由度4的 t 分布计算；脚本将 ttest_rel 结果与公式算出的 t、自由度和分布尾概率核对。

## 验证边界

样例断言 20000 ms 转为 20.0 s、PYTSA EDA 产生一条有效事件，配对差值/t/df/dz 与独立公式相符。它不验证完整参数 schema、真实单位/设备校准、其它分析方法、统计假设或真实研究效应。
