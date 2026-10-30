# 官方来源

访问日期：2026-10-31。外部 API 语义以官方文档为准；PYTSA 契约以当前代码、调用链和实测为准。

- SciPy resample_poly：https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html （有理比例多相滤波重采样）
- SciPy sosfiltfilt：https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html （二阶节双向滤波与边界/pad 规则）
- SciPy ttest_rel：https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_rel.html （配对 t 检验；调用者仍需保证 ID 正确配对）
- Statsmodels AnovaRM：https://www.statsmodels.org/stable/generated/statsmodels.stats.anova.AnovaRM.html （平衡纯被试内 ANOVA 的支持范围）
- Statsmodels MixedLM：https://www.statsmodels.org/stable/generated/statsmodels.regression.mixed_linear_model.MixedLM.html （线性混合效应接口；随机结构仍由设计制定）
- Statsmodels multipletests：https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html （多重校正 API；family 由研究预先定义）

链接不表示目标环境已安装对应库、实验假设成立或结果已运行验证。
