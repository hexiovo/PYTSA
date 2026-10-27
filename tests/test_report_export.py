import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from matplotlib.figure import Figure

import report_export


def _sample_results(row_count=4):
    values = pd.DataFrame({"time_seconds": range(row_count), "signal": range(row_count)})
    return (
        {
            "data": values.copy(),
            "original": values.copy(),
            "fs": 1.0,
            "time_unit": "seconds",
            "warnings": ["<script>prepared warning</script>"],
            "meta": {
                "status": "REAL_OR_USER_PROVIDED",
                "time_col": "time_seconds",
                "value_cols": ["signal"],
                "parameters": {"missing": "reject"},
            },
        },
        {
            "tables": {
                "summary": pd.DataFrame({"variable": ["signal"], "mean": [1.5]}),
                "tests": pd.DataFrame({"variable": ["signal"], "test": ["ADF"]}),
            },
            "matrices": {
                "pearson": pd.DataFrame([[1.0]], index=["signal"], columns=["signal"]),
            },
            "warnings": ["<b>analysis warning</b>"],
            "meta": {"parameters": {"max_lag": 3}},
        },
    )


def _figure():
    figure = Figure(figsize=(2, 2))
    axes = figure.subplots()
    axes.plot([0, 1], [1, 0])
    return figure


class ReportTemplateTests(unittest.TestCase):
    def test_normalizer_returns_canonical_sections_and_rejects_empty_formats(self):
        normalized = report_export.normalize_report_template({
            "name": "  My report  ",
            "sections": {"summary": True, "warnings": False},
            "formats": ["html", "json", "html"],
            "experiment": {"label": "pilot"},
        })
        self.assertEqual(normalized["name"], "My report")
        self.assertEqual(normalized["sections"], ["summary"])
        self.assertEqual(normalized["formats"], ["json", "html"])
        self.assertEqual(normalized["figures"], "all")
        with self.assertRaisesRegex(ValueError, "至少选择一种"):
            report_export.normalize_report_template({"formats": []})
        with self.assertRaisesRegex(ValueError, "未知章节"):
            report_export.normalize_report_template({"sections": ["unknown"]})

    def test_formats_and_sections_control_published_files_and_visible_content(self):
        prepared, analysis = _sample_results()
        with tempfile.TemporaryDirectory() as temp:
            result = report_export.export_report(
                temp,
                prepared,
                analysis,
                research_path=str(Path(temp) / "missing-research.json"),
                template={
                    "name": "selective",
                    "title": "Report",
                    "experiment": {"note": "<script>experiment</script>"},
                    "sections": ["summary", "warnings", "annotations"],
                    "formats": ["json", "html"],
                    "figures": [],
                },
                annotations={"note": "<img src=x>"},
            )
            published = Path(result["folder"])
            self.assertEqual({Path(path).name for path in result["files"]}, {"results.json", "report.html"})
            self.assertEqual({path.name for path in published.iterdir()}, {"results.json", "report.html"})
            report = (published / "report.html").read_text(encoding="utf-8")
            payload = json.loads((published / "results.json").read_text(encoding="utf-8"))

        self.assertIn("&lt;script&gt;experiment&lt;/script&gt;", report)
        self.assertIn("&lt;img src=x&gt;", report)
        self.assertIn("&lt;b&gt;analysis warning&lt;/b&gt;", report)
        self.assertEqual(payload["warnings"], [
            "<script>prepared warning</script>", "<b>analysis warning</b>",
            "Research notes file was not found: " + str(Path(temp) / "missing-research.json"),
        ])
        self.assertEqual(payload["experiment"]["note"], "<script>experiment</script>")
        self.assertEqual(payload["annotations"]["note"], "<img src=x>")

    def test_html_only_embeds_selected_figure_without_leaking_png_file(self):
        prepared, analysis = _sample_results()
        with tempfile.TemporaryDirectory() as temp:
            result = report_export.export_report(
                temp,
                prepared,
                analysis,
                figures={"time_series": _figure(), "welch_psd": _figure()},
                template={
                    "name": "html only",
                    "title": "Chart report",
                    "sections": ["summary"],
                    "formats": ["html"],
                    "figure_titles": {"time_series": "<img src=x>"},
                    "figures": ["time_series"],
                },
            )
            published = Path(result["folder"])
            report = (published / "report.html").read_text(encoding="utf-8")
            names = {path.name for path in published.iterdir()}

        self.assertEqual(names, {"report.html"})
        self.assertIn("data:image/png;base64,", report)
        self.assertIn("&lt;img src=x&gt;", report)
        self.assertNotIn('href="figures/figure_01.png"', report)
        self.assertEqual([item["key"] for item in result["figures"]], ["time_series"])

    def test_cancellation_does_not_publish_a_partial_folder(self):
        prepared, analysis = _sample_results()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(report_export.ExportCancelledError):
                report_export.export_report(
                    temp, prepared, analysis,
                    template={"formats": ["json"]},
                    cancel=lambda: True,
                )
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
