# 研究方法与实现边界（参考资料截至 2026-10-30）

本文列出 PYTSA 1.3.1 当前实现所参考的资料和方法边界，资料检索截止北京时间 2026-10-30。它不是系统综述，也不覆盖所有预处理、指标或预测模型。PYTSA 还提供 Welch、谱熵、STL 强度、ADF/KPSS、MASE 和滚动验证等方法。

| 来源与阅读范围 | 对本软件的影响 |
| --- | --- |
| [Liu 与 Ning，2026-09，EDA 综述和跨数据集基准](https://arxiv.org/html/2609.22622v1)，预印本，摘要、预处理/分解与实验比较部分 | 增加分解选择、质量标记和特征；支持比较原始、清理、tonic 和 phasic，不把某一种分解当作真值。 |
| [Chai 等，2026-10，伪迹校正与下游分类研究](https://arxiv.org/html/2610.07438v1)，预印本，摘要、方法和讨论 | 清理默认可关闭，保留原始分支。该研究单项任务的发现不能推断为所有清理都无效。 |
| [NeuroKit 官方 EDA 文档](https://neuropsychology.github.io/NeuroKit/functions/eda.html)及本机 0.2.13 源码 | 复用 biosppy 清理、cvxEDA 和 sparsEDA；后者在上游标注存在不稳定情况，求解错误需直接呈现。 |
| [SciPy signal 官方文档](https://docs.scipy.org/doc/scipy/reference/signal.html) | 补入可选去趋势、陷波、中值与 Savitzky–Golay 平滑；截止频率必须满足采样率约束。 |
| [PyWavelets 阈值文档](https://pywavelets.readthedocs.io/en/latest/ref/thresholding-functions.html) | 提供可选小波软阈值处理，不能解释为自动恢复真实生理信号。 |
| [CuPy 安装文档](https://docs.cupy.dev/en/stable/install.html) | CUDA 包须匹配驱动能力；以实际矩阵运算探测可用性。安装 Python 包不能替代显卡或驱动。 |

复杂度统计使用固定、可复现的定义：排列熵采用三点、延迟一的序模式；Hjorth 指标反映方差与相邻差分的变化；线长和过零率为描述特征。这些不是压力或情绪判定器。具体公式、边界处理及实际参数以导出元数据为准。

通用探索性预处理与 EDA 物理值分支分开。可选滤波默认关闭；开启后会改变曲线、幅度和统计量。EDA 伪迹阈值需要按设备和实验设定，不能套用为所有记录的通用质量标准。无效样本分段处理，不能跨越伪迹或缺失段进行滤波。

## 已发现而尚未实现的覆盖缺口

以下条目是明确的未完成范围，不能把文献条目显示在软件里当作方法已实现：

- [ospEDA（2026-06）](https://pubmed.ncbi.nlm.nih.gov/42295969/)：现有查核仅覆盖书目和摘要，尚未完成原始实现与数值基准验证。
- [gmsEDA（2026-08）](https://arxiv.org/abs/2608.14732)：已读摘要，它联合处理多条记录，与当前单条连续记录的数据契约不同；未实现联合记录求解器。
- [Transformer EDA 分解研究（2025）](https://pmc.ncbi.nlm.nih.gov/articles/PMC12298303/)：全文访问受限，未取得并验证对应模型权重、许可证和推理流程。
- 通用预测的基础模型、深度神经网络、ECG/EEG/EMG 专用流程、因果推断与被试级监督分类：当前工具并未实现，不能由通用时序指标替代。

这份清单说明当前软件的实现范围和未完成项目。没有用户真实数据，不能据此声称某种方法改善了研究结果。
