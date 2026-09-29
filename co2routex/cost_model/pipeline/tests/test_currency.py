"""Price-year conversion is consistent across all outputs without re-sizing."""
from copy import deepcopy
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from statistics import fmean
import tempfile
import unittest

from openpyxl import load_workbook
import pandas as pd

from pipeline_cost_model import PipelineCostConfig, PipelineCostModel, RouteInput
from pipeline_cost_model.costed_input import COSTED_INPUT_COLUMNS, result_columns
from pipeline_cost_model.currency import DEFAULT_FACTOR, PPI_2021, PPI_2024, conversion_metadata, to_eur2024
from pipeline_cost_model.exports import build_tables, write_results


class CurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = PipelineCostConfig(mode="both", coefficient_method="fixed_design")
        cls.route = RouteInput("A", "B", 200, 1, "reference", 1_000_000, source_row=2)
        cls.result = PipelineCostModel(cls.config).calculate_route(cls.route)

    def test_bundled_factor_matches_adopt_and_discloses_partial_year(self):
        self.assertEqual((len(PPI_2021), len(PPI_2024)), (12, 11))
        self.assertAlmostEqual(fmean(PPI_2021), 99.99166666666667, places=12)
        self.assertAlmostEqual(fmean(PPI_2024), 120.88181818181819, places=12)
        self.assertAlmostEqual(DEFAULT_FACTOR, 1.208918925061937, places=14)
        metadata = conversion_metadata(self.config)
        self.assertEqual(metadata["source_price_year"], 2021)
        self.assertEqual(metadata["target_price_year"], 2024)
        self.assertEqual(metadata["ppi_2024_months"], 11)
        self.assertIn("December is absent", metadata["ppi_2024_coverage"])
        self.assertIsNone(to_eur2024(None))
        self.assertEqual(to_eur2024(0.0), 0.0)
        self.assertEqual(to_eur2024(-2.0, 1.25), -2.5)  # Negative fitted coefficients are not clamped.
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            to_eur2024(1e308, 2)

    def test_old_yaml_gets_conversion_without_new_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.yaml"
            path.write_text("mode: approximation\ncoefficient_method: fixed_design\n")
            config = PipelineCostConfig.from_yaml(path)
            config.validate(require_input=False)
            self.assertEqual(config.conversion_factor, DEFAULT_FACTOR)
            self.assertEqual(config.electricity_price_reference_year, 2021)

    def test_custom_factor_requires_valid_value_and_documented_source(self):
        custom = replace(self.config, eur2021_to_eur2024_factor=1.25, eur2024_factor_source="Test index ratio")
        custom.validate(require_input=False)
        metadata = conversion_metadata(custom)
        self.assertEqual(metadata["eur2021_to_eur2024_factor"], 1.25)
        self.assertEqual(metadata["conversion_method"], "user_factor")
        self.assertEqual(metadata["conversion_basis"], "Test index ratio")
        self.assertNotIn("ppi_2024_months", metadata)
        self.assertNotIn("conversion_data_source", metadata)
        for invalid in (0, -1, True, "1.25", float("nan"), float("inf")):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "eur2021_to_eur2024_factor"):
                replace(custom, eur2021_to_eur2024_factor=invalid).validate(require_input=False)
        for source in (None, "", " ", 2024):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "eur2024_factor_source"):
                replace(custom, eur2024_factor_source=source).validate(require_input=False)
        with self.assertRaisesRegex(ValueError, "requires"):
            replace(self.config, eur2024_factor_source="Unused source").validate(require_input=False)
        for year in (2024, 2021.0, True):
            with self.subTest(year=year), self.assertRaisesRegex(ValueError, "electricity_price_reference_year"):
                replace(self.config, electricity_price_reference_year=year).validate(require_input=False)

    def test_converted_totals_components_and_coefficients_reconcile(self):
        original = deepcopy(self.result)
        tables = build_tables(self.config, [self.result])
        summary = tables["summary"][0]
        coefficients = tables["coefficients"]
        total = next(row for row in coefficients if row["component"] == "total")
        parts = [row for row in coefficients if row["component"] != "total"]
        f = total["fixed_coefficient_eur2024_per_year"]
        v = total["flow_coefficient_eur2024_per_t"]
        annual = summary["total_annual_cost_eur2024_per_year"]
        self.assertAlmostEqual(f + v * 1_000_000, annual, places=6)
        self.assertAlmostEqual(annual / 1_000_000, summary["levelized_cost_eur2024_per_t"])
        for field in ("fixed_coefficient_eur2024_per_year", "flow_coefficient_eur2024_per_t"):
            self.assertAlmostEqual(sum(row[field] for row in parts), total[field], places=6)
        groups = {"upfront_capex": "capex_total_eur2024",
                  "annualized_capex": "annualized_capex_total_eur2024_per_year",
                  "fixed_opex": "fixed_opex_total_eur2024_per_year",
                  "electricity_opex": "electricity_opex_total_eur2024_per_year"}
        for group, field in groups.items():
            rows = [row for row in tables["cost_components"] if row["cost_group"] == group]
            self.assertAlmostEqual(sum(row["value_eur2024"] for row in rows), summary[field], places=6)
            self.assertEqual({row["unit_eur2024"] for row in rows},
                             {"EUR_2024" if group == "upfront_capex" else "EUR_2024/year"})
        self.assertAlmostEqual(sum(summary[field] for group, field in groups.items() if group != "upfront_capex"), annual, places=6)
        for rows in tables.values():
            for row in rows:
                for field, value in row.items():
                    if "_eur2024" in field and field not in {"value_eur2024", "unit_eur2024"}:
                        self.assertEqual(value, row[field.replace("_eur2024", "_eur")] * DEFAULT_FACTOR)
        self.assertAlmostEqual(summary["total_annual_cost_eur_per_year"], 15463744.110506363, places=6)
        self.assertAlmostEqual(tables["design"][0]["electricity_price_eur2024_per_mwh"], 60 * DEFAULT_FACTOR)
        units = {row["field"]: row["unit"] for row in tables["units"]}
        self.assertEqual(units["fixed_coefficient_eur2024_per_year"], "EUR_2024/year")
        self.assertEqual(units["flow_coefficient_eur2024_per_t"], "EUR_2024/tCO2")
        self.assertEqual(self.result, original)  # Export conversion must not mutate internal results.

    def test_flow_range_predictions_errors_and_missing_reference(self):
        config = replace(self.config, coefficient_method="flow_range", annual_flow_min_t_per_year=500_000,
                         annual_flow_max_t_per_year=2_000_000, validation_samples=2)
        result = PipelineCostModel(config).calculate_route(self.route)
        for mode in ("both", "approximation"):
            settings = replace(config, mode=mode)
            exported = result if mode == "both" else replace(result, benchmark=None)
            tables = build_tables(settings, [exported])
            for row in tables["fit_samples"]:
                self.assertEqual(row["actual_total_annual_cost_eur2024_per_year"],
                                 row["actual_total_annual_cost_eur_per_year"] * DEFAULT_FACTOR)
                self.assertEqual(row["predicted_total_annual_cost_eur2024_per_year"],
                                 row["predicted_total_annual_cost_eur_per_year"] * DEFAULT_FACTOR)
                converted_error = 100 * (row["predicted_total_annual_cost_eur2024_per_year"] /
                                         row["actual_total_annual_cost_eur2024_per_year"] - 1)
                self.assertAlmostEqual(row["error_percent"], converted_error, places=10)
            for row in tables["fit_diagnostics"]:
                self.assertEqual(row["rmse_eur2024_per_year"], row["rmse_eur_per_year"] * DEFAULT_FACTOR)
            columns = result_columns(exported)
            self.assertEqual(set(columns), set(COSTED_INPUT_COLUMNS))
            if mode == "approximation":
                self.assertIsNone(columns["pipeline_annual_cost_eur2024_per_year"])
                self.assertIsNone(columns["pipeline_average_cost_eur2024_per_t"])
                self.assertIsNotNone(columns["pipeline_fixed_cost_eur2024_per_year"])

    def test_custom_factor_matches_report_and_all_costed_formats(self):
        mappings = {
            "pipeline_fixed_cost_eur2024_per_year": ("coefficients", "fixed_coefficient_eur2024_per_year"),
            "pipeline_flow_cost_eur2024_per_t": ("coefficients", "flow_coefficient_eur2024_per_t"),
            "pipeline_annual_cost_eur2024_per_year": ("summary", "total_annual_cost_eur2024_per_year"),
            "pipeline_average_cost_eur2024_per_t": ("summary", "levelized_cost_eur2024_per_t"),
        }
        for extension in ("parquet", "xlsx", "csv"):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / f"routes.{extension}"
                frame = pd.DataFrame({"from_id": ["A", "skip"], "to_id": ["B", "B"],
                                      "distance_km": [200, 200], "annual_flow_t_per_year": [1_000_000] * 2})
                # Existing 2024 values must be replaced, not converted again or retained.
                for column in mappings:
                    frame[column] = [123.0, 456.0]
                if extension == "parquet":
                    frame.to_parquet(source)
                elif extension == "xlsx":
                    frame.to_excel(source, sheet_name="pipeline_route_metrics", index=False)
                else:
                    frame.to_csv(source, index=False)
                before = hashlib.sha256(source.read_bytes()).hexdigest()
                config = replace(self.config, input_path=source, output_dir=Path(directory)/"out",
                                 eur2021_to_eur2024_factor=1.25, eur2024_factor_source="Test index ratio",
                                 write_csv=True, write_json=True)
                outputs = write_results(config, [self.result])
                costed = outputs[f"routes_costed.{extension}"]
                if extension == "parquet":
                    copied = pd.read_parquet(costed)
                elif extension == "xlsx":
                    copied = pd.read_excel(costed, sheet_name="pipeline_route_metrics")
                else:
                    copied = pd.read_csv(costed)
                book = load_workbook(outputs[config.output_workbook_name], data_only=True)
                try:
                    sheets = {}
                    for name in ("summary", "coefficients"):
                        rows = list(book[name].values)
                        sheets[name] = [dict(zip(rows[0], row)) for row in rows[1:]]
                    settings = dict(list(book["settings"].values)[1:])
                    self.assertEqual(settings["eur2021_to_eur2024_factor"], "1.25")
                    self.assertEqual(settings["conversion_basis"], "Test index ratio")
                    payload = json.loads(outputs["pipeline_results.json"].read_text())
                    for column, (sheet, report_column) in mappings.items():
                        self.assertTrue(pd.isna(copied.iloc[1][column]))
                        expected = copied.iloc[0][column.replace("_eur2024", "_eur")] * 1.25
                        self.assertAlmostEqual(copied.iloc[0][column], expected, places=6)
                        for table in (sheets[sheet], payload["tables"][sheet]):
                            row = table[-1] if sheet == "coefficients" else table[0]
                            self.assertAlmostEqual(row[report_column], expected, places=6)
                        with outputs[f"{sheet}.csv"].open(encoding="utf-8-sig") as stream:
                            rows = list(csv.DictReader(stream))
                        row = rows[-1] if sheet == "coefficients" else rows[0]
                        self.assertAlmostEqual(float(row[report_column]), expected, places=6)
                finally:
                    book.close()
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
