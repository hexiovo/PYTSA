# PYTSA Workflow Workspace Implementation Plan

## Background and Context

### Why

PYTSA currently provides a local desktop workflow for loading one table, selecting analysis settings, running time-series or EDA analysis, viewing results, and exporting files. Users must recreate settings and cannot reopen a complete prior workspace after closing the application. Repeated analyses also lack durable run history, batch handling, and a persistent record of warnings and errors. This implementation adds the requested workspace capabilities while preserving the existing analysis behavior.

### Context

The current application is centered in `app.py`; `core.py` supplies table loading, preprocessing, analysis, forecasting, and result exports, while `experimental.py` supplies EDA analysis. The project already uses a local virtual environment and a PyInstaller build. The implementation keeps those dependencies and the current Memphis-style desktop interface. It does not change the scientific algorithms in `core.py` or `experimental.py`.

The task scope is nine requested workflow features, with recent-use history retained alongside them: parameter templates; persistent logs; project save and restore; data-quality preflight; batch analysis; run-history comparison; progress and cancellation; report templates; interactive chart interval annotations; and recent projects/data. The scope review also fixed their user-visible behavior: preflight is read-only, annotations do not change analysis unless explicitly applied to the EDA statistical interval, batches require explicit event-file sharing/mapping, and report export stays within existing CSV/XLSX/JSON/PNG/HTML formats.

### History and Related Work

The current request is to implement the complete workbench workflow. No external issue tracker or third-party integration is part of this task. The active implementation plan is based on the scope review and the frozen UI/backend contract in the associated HexiPlan RUN.

## Acceptance Criteria

- Parameter and report templates can be saved, copied, imported, exported, selected, and checked against the current data context; incompatible fields are shown before application.
- Projects persist configuration, input snapshot, available results, mappings, and manual annotations in versioned JSON-based files; they can be restored without the original source, while changed or missing source files are identified without silently replacing data.
- Recent projects/data and append-only run logs remain available after restart. Logs retain warnings and full exceptions within bounded rotation.
- Read-only preflight identifies missing values, duplicate or irregular time points, constant columns, and IQR-based outlier ranges with counts and useful row/time locations. It never changes source data.
- Batch analysis freezes settings at start, makes event-file handling explicit, isolates per-file errors and outputs, supports targeted retries by batch ID, writes a CSV summary, and reports cooperative cancellation accurately.
- Completed runs have immutable parameter/result snapshots. Comparisons show parameter and metric differences and overlay only curves with matching units and time basis; no interpolation is added.
- Progress reports stages, completed/total items, and elapsed time without invented intra-file percentages. Cancellation is cooperative at stage/file boundaries.
- Chart annotations are editable and saved with projects and applicable exports. Only an explicit action applies a selected time-series range to the existing EDA start/end fields; full-segment decomposition remains unchanged.
- Selective report templates honor the requested sections, titles, figures, and supported existing output formats. Word and PDF are not added.
- The application remains launchable and packageable as version 1.1.0 with no new dependencies. The final small-project verification is run once after all work packages are integrated and is performed independently of implementation.

## Implementation Details

### Approach

Add `workspace_service.py` as the persistence and workflow boundary. It owns workspace paths, safe/versioned project snapshots, parameter/report template storage, recent items, immutable run history and comparisons, persistent logs, read-only preflight, and batch manifests/results. JSON is used directly or inside ZIP with tabular JSON data; pickle and arbitrary object deserialization are prohibited. Writes use atomic replacement. `PYTSA_HOME` overrides the default `%LOCALAPPDATA%/PYTSA` location for tests and controlled deployments.

Keep existing analysis calls intact. The UI loads each new source once, performs preflight at that boundary, freezes a copy of the chosen settings for a run or batch, then calls the existing `core` and `experimental` functions. A source change clears stale results before a new analysis. Input checks are not repeated in inner computation stages.

The chart work adds view interactions and saved interval annotations. A selected range is stored in relative seconds. The UI applies it to the EDA statistical start/end values only through an explicit user action; decomposition and other chart types keep their existing behavior.

### Work Packages and Files

- **WP1 — Workflow backend** (`workspace_service.py`, `tests/test_workflow.py`): implement safe workspace storage, parameter/project/report template persistence, recent-item tracking, project snapshots, run history and comparison, logs, preflight, batch execution, and batch retry using the frozen API contract.
- **WP2 — Desktop UI** (`app.py`, `workflow_ui.py`): integrate project save/restore, templates, recent items, quality findings, batch status/retry, log/history browsing, progress/cancellation, and chart annotation/apply actions using the same contract.
- **WP3 — Export and local delivery** (`report_export.py`, `tests/test_report_export.py`, `docs/使用说明.md`, `build.ps1`): implement selective report export from the shared version-1 report-template schema, document user workflows in Chinese, and retain the existing local build behavior while adding the version 1.1.0 delivery path.

All three work packages contribute to one roadmap step, `deliver-workflow`, and can proceed in parallel because their public data contract is frozen. Final integration and verification happen after all are complete.

### Dependencies and Safety

No new package, network access, global environment change, or external tool is needed. Use the project `.venv` for Python commands. The original input data is read-only; annotations/preflight do not modify it. Imported JSON is schema/version checked at the boundary. A malformed project or template must not partially replace the current in-memory workspace. Batch event files are either explicitly shared or explicitly mapped per input; there is no silent pairing.

### Verification Strategy

Because this is a small project with multiple coupled UI/backend capabilities, use one final integration verification after the work packages are complete. Cover persistence round trips and unsafe-format rejection, source change/relink behavior, template compatibility, known preflight findings, batch success/failure/retry/cancellation/summary, history comparison, selective reports, UI startup and key flows, the existing smoke path, and a built executable smoke check. After a failure, rerun only the affected checks. Do not repeat successful, unaffected scans.
