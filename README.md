# PYTSA

PYTSA 是本地运行的 Python 桌面时间序列与实验信号分析工具。当前版本：**1.3.1**。

**Windows 便携版：**[查看 v1.3.1 发布页](https://github.com/hexiovo/PYTSA/releases/tag/v1.3.1) · [直接下载 PYTSA-1.3.1-Windows-x64.zip](https://github.com/hexiovo/PYTSA/releases/download/v1.3.1/PYTSA-1.3.1-Windows-x64.zip)

下载并解压到有写入权限的目录，再双击 `RunWithLocalData.cmd` 启动。首次启动会在程序目录创建 `Data`，用于保存项目、模板、运行历史、日志和批次结果；升级时请保留已有的 `Data` 文件夹。运行资源设置和可选依赖环境保存在 Windows 用户配置目录。便携版不需要预装 Python，CPU 模式可直接使用。

## 功能

- 导入 CSV、TSV、TXT 和 Excel；检查时间、时区、缺失值、重复时间和采样信息。
- 设置重采样、缺失处理、滤波和探索性变换；提供描述统计、平稳性检验、ACF/PACF、STL、Welch PSD、频段功率和滑动统计。
- 提供 Pearson、Spearman、Kendall、协方差、偏相关、滞后相关、相干和标准化距离矩阵。
- 支持 Naive、SeasonalNaive、ETS、ARIMA 和 SARIMA 预测，以及留后比较和滚动验证。
- 提供皮电信号清理、分解、SCR 检测、窗口汇总和事件分析。
- 保存项目、参数模板、报告模板和运行历史；支持批次处理及 CSV/XLSX/JSON/PNG/HTML 导出。
- 可选去趋势、Hampel 去尖峰、中值或 Savitzky–Golay 平滑、陷波和小波软阈值处理，以及 Hjorth、排列熵、线长和过零率等描述指标。

原始输入文件保持只读。探索性处理不会进入预测使用的原始尺度分支。内置示例为程序生成的合成数据，带有 `SYNTHETIC_ONLY` 标记，不可用于实证结论。相关系数、皮电指标和预测结果本身不证明因果、心理状态或临床诊断。

## 计算环境

CPU 是默认可用路径。GPU 为可选功能，需兼容的 NVIDIA GPU、驱动和 CuPy 组件；当前只加速 Pearson、协方差和标准化距离矩阵。环境检测会实际执行 GPU 运算，安装 Python 包不会安装显卡或驱动。cvxEDA 分解需要可选依赖 `cvxopt`；BioSPPy 清理要求采样率高于 10 Hz。SparseEDA 属实验性方法，可能失败。详见[操作指南](docs/操作指南.md)和[发布说明](docs/发布说明-1.3.1.md)。

## 快速开始

见[快速开始](docs/快速开始.md)。详细页面、数据和参数说明见[操作指南](docs/操作指南.md)与[使用说明](docs/使用说明.md)。

## 实验设计 Skill

- [下载 Skill 安装包](https://github.com/hexiovo/PYTSA/releases/download/v1.3.1/PYTSA-study-skill-1.3.1.zip)：解压后双击 `InstallSkill.cmd`，将 `pytsa-study-config` 安装到 Codex Skill 目录；若已有同名 Skill，安装器会先备份旧目录。
- [下载单文件版](https://github.com/hexiovo/PYTSA/releases/download/v1.3.1/PYTSA-study-skill-1.3.1.md)：把该文件和实验设计说明一起提供给其他 AI 使用。单文件版包含 Skill 指令和文字参考；真正运行 PYTSA 或统计脚本仍需相应程序和运行环境。

Skill 帮助把实验设计整理为 PYTSA 配置草案、数据准备方案和主试操作包。主试可以用日常语言描述研究，不必先选统计检验名称；设计中缺少的信息会保留为待确认项。详细说明见 [Skill 使用说明](docs/Skill使用说明.md)。

## 在源码环境运行

源码环境使用 Python 3.12。Windows PowerShell 命令：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe app.py
```

需要 cvxEDA 时，再安装 `requirements-optional.txt` 中列出的可选求解器。CuPy 应在“配置与环境”页按实际驱动能力选择对应版本；不要同时安装多个 CUDA wheel。

## 维护者命令

在已安装开发依赖的源码环境中运行测试：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe app.py --smoke-test --smoke-output build/smoke.json
```

先在项目虚拟环境中构建 Windows EXE，再组装便携版和 Skill 发布包：

```powershell
.\build.ps1
.\packaging\Make-Release.ps1
```

构建默认将 `PYTSA.exe` 写入 `deliver\PYTSA-1.3.1`；`Make-Release.ps1` 从该位置组装 Windows 便携 ZIP、Skill ZIP 和单文件 Skill，输出到 `deliver\releases\v1.3.1`。便携版包含 `RunWithLocalData.cmd`；首次启动时会创建 `Data` 工作区。

## 文档与源码

- [操作指南（HTML，含界面截图）](docs/操作指南.html)
- [使用说明](docs/使用说明.md)：项目、模板、批次、历史和报告导出。
- [PYTSA 实验设计配置 Skill 使用说明](docs/Skill使用说明.md)及[Skill 源码](skills/pytsa-study-config/SKILL.md)
- [研究方法与实现边界](docs/research-update-20261030.md)：参考资料范围和当前未实现的功能。
- `app.py`、`settings_ui.py`、`workflow_ui.py`：桌面界面与工作流。
- `core.py`、`analysis_options.py`：通用时间序列、预处理和预测。
- `experimental.py`：皮电与事件分析。
- `workspace_service.py`、`report_export.py`：本地项目持久化和报告导出。
- `assets/THIRD_PARTY_LICENSES.txt` 与 `assets/licenses/`：第三方组件许可证资料。
