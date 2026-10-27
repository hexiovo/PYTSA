# PYTSA

本地运行的 Python 桌面时间序列与实验信号分析工具。当前版本：**1.2.0**。

支持 CSV、TSV、TXT 和 Excel 数据输入，提供数据预检、预处理、描述统计、频谱与变量关系分析、预测、皮电与事件分析，以及项目保存、参数模板、批量处理和离线报告导出。

## 主要功能

- 时间单位与时区、重复时间处理、重采样、缺失填补上限、Butterworth 滤波和探索性变换。
- 描述统计、平稳性检验、ACF/PACF、STL、Welch PSD、命名频带功率和滑动窗口统计。
- 相关、协方差、偏相关、定向滞后相关、相干与标准化距离矩阵。
- Naive、SeasonalNaive、ETS、ARIMA 和 SARIMA，支持留后比较、可调预测区间与滚动验证。
- 皮电清理与分解、SCR 检测、SCL/SCR 窗口汇总、事件基线校正、响应 AUC 和无响应比例。
- 项目恢复、参数与报告模板、运行历史、区间标注、批量分析、CSV/XLSX/JSON/PNG/HTML 导出。

原始输入文件保持只读。预测使用保留的物理值分支；探索性插值、滤波或标准化不会进入预测训练。内置示例为明确标记的合成数据。

## 本地运行

当前 Windows 构建使用 Python 3.12。在 PowerShell 中运行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe app.py
```

## 测试

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe app.py --smoke-test --smoke-output build/smoke.json
```

1.2.0 已通过 54 项自动化测试，并完成源码与 Windows EXE 的界面和冒烟验证。

## Windows 打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -DeliveryDir .\deliver
```

输出为 `deliver/PYTSA.exe`。构建产物、虚拟环境和本地验证输出不纳入 Git。

## 文档与代码

- [使用说明](docs/使用说明.md)：参数含义、指标定义和工作流程。
- `app.py`、`additional_controls.py`、`workflow_ui.py`：桌面界面与工作流。
- `core.py`、`analysis_options.py`：通用时间序列、预处理和预测。
- `experimental.py`：皮电与事件分析。
- `workspace_service.py`、`report_export.py`：本地持久化和报告导出。
- `assets/THIRD_PARTY_LICENSES.txt` 与 `assets/licenses/`：第三方组件许可证资料。
