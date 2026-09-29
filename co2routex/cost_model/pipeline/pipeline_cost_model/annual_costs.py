"""Shared engineering evaluator and route-specific affine annual cost model."""
from __future__ import annotations
import contextlib
import copy
import io
import math
import numpy as np
from .config import PipelineCostConfig, positive
from .models import AffineCoefficient, Approximation, FixedDesignApproximation, DirectCost, RouteCostResult, RouteInput
from .oeuvray import CO2Chain_Oeuvray


COMPONENT_FIELDS = {
    "annualized_capex": "annualized_capex_total_eur_per_year",
    "fixed_opex": "fixed_opex_total_eur_per_year",
    "electricity_opex": "electricity_opex_total_eur_per_year",
    "opex": "opex_total_eur_per_year",
    "total": "total_annual_cost_eur_per_year",
}
FITTED_COMPONENTS = ("annualized_capex", "fixed_opex", "electricity_opex")


def capital_recovery_factor(rate: float, years: float) -> float:
    return 1 / years if rate == 0 else rate / -math.expm1(-years * math.log1p(rate))


def _reconcile(label: str, actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6):
        raise RuntimeError(f"{label} does not reconcile: {actual} versus {expected}")


def _ledger(design, data, config, annual_flow):
    costs = {}
    for output, source in {
        "material": "material", "labour": "labor", "row": "row", "miscellaneous": "misc",
        "pipeline": "pipe", "initial_compression": "initial_compression", "boosters": "recompression",
    }.items():
        costs[f"capex_{output}_eur"] = float(design[f"capex_{source}"])
    costs["capex_compression_eur"] = costs["capex_initial_compression_eur"] + costs["capex_boosters_eur"]
    costs["capex_total_eur"] = costs["capex_pipeline_eur"] + costs["capex_compression_eur"]
    for component in ("pipeline", "initial_compression", "boosters"):
        years = data["z_pipe"] if component == "pipeline" else data["z_pumpcomp"]
        costs[f"annualized_capex_{component}_eur_per_year"] = (
            capital_recovery_factor(config.discount_rate, years) * costs[f"capex_{component}_eur"]
        )
    costs["annualized_capex_total_eur_per_year"] = sum(
        costs[f"annualized_capex_{c}_eur_per_year"] for c in ("pipeline", "initial_compression", "boosters")
    )
    costs["fixed_opex_pipeline_eur_per_year"] = float(design["opex_pipe"])
    for component, source in (("initial_compression", "initial_compression"), ("boosters", "recompression")):
        costs[f"fixed_opex_{component}_eur_per_year"] = (
            float(design[f"capex_{source}"]) * data["muOMpumpcomp"]
        )
        costs[f"electricity_opex_{component}_eur_per_year"] = float(design[f"opex_energy_{source}"])
    costs["fixed_opex_total_eur_per_year"] = sum(
        costs[f"fixed_opex_{c}_eur_per_year"] for c in ("pipeline", "initial_compression", "boosters")
    )
    costs["electricity_opex_total_eur_per_year"] = sum(
        costs[f"electricity_opex_{c}_eur_per_year"] for c in ("initial_compression", "boosters")
    )
    costs["opex_total_eur_per_year"] = costs["fixed_opex_total_eur_per_year"] + costs["electricity_opex_total_eur_per_year"]
    costs["total_annual_cost_eur_per_year"] = costs["annualized_capex_total_eur_per_year"] + costs["opex_total_eur_per_year"]
    costs["levelized_cost_eur_per_t"] = costs["total_annual_cost_eur_per_year"] / annual_flow
    if any(not math.isfinite(value) or value < 0 for value in costs.values()):
        raise RuntimeError("Engineering calculation produced a negative or nonfinite cost")
    _reconcile("Pipeline CAPEX components", sum(costs[f"capex_{c}_eur"] for c in ("material", "labour", "row", "miscellaneous")), costs["capex_pipeline_eur"])
    return costs


class PipelineCostModel:
    """Evaluate a reference design or fit a curve through re-sized designs.

    Both coefficient methods use transported tonnes/year and retain direct costs.
    Baseline costs use physical routed distance. Resistance is routing metadata.
    """

    def __init__(self, config: PipelineCostConfig):
        config.validate(require_input=False)
        self.config = config
        self._engine = CO2Chain_Oeuvray()
        self._source_cache = {}

    def calculate_route(self, route: RouteInput) -> RouteCostResult:
        benchmark = None
        approximation = None
        if self.config.mode != "approximation":
            annual = route.annual_flow_t_per_year
            if annual is None:
                annual = self.config.benchmark_annual_flow_t_per_year
            if annual is None:
                raise ValueError(f"Scenario {route.scenario_id!r}: benchmark annual flow is required")
            benchmark = self.calculate_fixed_flow(route, annual)
        if self.config.mode != "benchmark":
            approximation = self.approximate_route(route, benchmark=benchmark)
        return RouteCostResult(route, approximation, benchmark)

    def calculate_fixed_flow(self, route: RouteInput, annual_flow_t_per_year: float) -> DirectCost:
        for value, label in ((route.distance_km, "distance_km"), (annual_flow_t_per_year, "annual_flow_t_per_year")):
            positive(value, label)
        terrain = route.terrain or self.config.terrain
        if terrain not in {"Onshore", "Offshore"}:
            raise ValueError("terrain must be Onshore or Offshore")
        annual = float(annual_flow_t_per_year)
        hourly = annual / self.config.operating_hours_per_year
        key = (float(route.distance_km), annual, terrain)
        if key not in self._source_cache:
            options = {
                "length_km": route.distance_km, "massflow_kg_per_s": hourly / 3.6,
                "operating_hours_per_a": self.config.operating_hours_per_year,
                "terrain": terrain, "timeframe": self.config.timeframe,
                "electricity_price_eur_per_mw": self.config.electricity_price_eur_per_mwh,
                "discount_rate": self.config.discount_rate,
                "p_inlet_bar": self.config.p_inlet_bar, "p_outlet_bar": self.config.p_outlet_bar,
            }
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = self._engine.calculate_cost(options)
            except (ValueError, KeyError, ZeroDivisionError, OverflowError) as exc:
                raise ValueError(
                    f"Scenario {route.scenario_id or route.from_id + ' -> ' + route.to_id}: "
                    f"engineering calculation failed at {annual:g} t/year ({hourly:g} t/h), "
                    f"{route.distance_km:g} km, {terrain}: {exc}"
                ) from exc
            self._source_cache[key] = copy.deepcopy(result)
        result = self._source_cache[key]
        d = result["configuration"]
        data = self._engine.universal_data
        baseline = _ledger(d, data, self.config, annual)
        _reconcile("Baseline levelized cost", baseline["levelized_cost_eur_per_t"], float(result["levelized_cost"]))
        _reconcile("Pipeline fixed OPEX", baseline["fixed_opex_pipeline_eur_per_year"], baseline["capex_pipeline_eur"] * data["muOMpipe"])
        _reconcile("Compression fixed OPEX", baseline["fixed_opex_initial_compression_eur_per_year"] + baseline["fixed_opex_boosters_eur_per_year"], float(d["opex_fix_compression"]))
        energy = {}
        for name, source in (("initial_compression", "initial_compression"), ("boosters", "booster")):
            specific = float(d[f"{source}_specific_mwh_per_t"])
            power = float(d[f"{source}_power_mw"])
            energy[f"specific_{name}_mwh_per_t"] = specific
            energy[f"{name}_mwh_per_year"] = specific * annual
            energy[f"{name}_power_mw"] = power
            _reconcile(f"{name} annual electricity", specific * annual, power * self.config.operating_hours_per_year)
            _reconcile(f"{name} electricity cost", specific * annual * self.config.electricity_price_eur_per_mwh, baseline[f"electricity_opex_{name}_eur_per_year"])
        energy["specific_total_mwh_per_t"] = energy["specific_initial_compression_mwh_per_t"] + energy["specific_boosters_mwh_per_t"]
        energy["total_mwh_per_year"] = energy["initial_compression_mwh_per_year"] + energy["boosters_mwh_per_year"]
        energy["total_power_mw"] = energy["initial_compression_power_mw"] + energy["boosters_power_mw"]
        _reconcile("Specific energy", energy["specific_total_mwh_per_t"], float(result["energy_requirements"]["specific_compression_energy"]))
        if any(not math.isfinite(value) or value < 0 for value in energy.values()):
            raise RuntimeError("Engineering calculation produced invalid energy")
        design = {
            "inner_diameter_m": float(d["id_nps_m"]), "outer_diameter_m": float(d["od_nps_m"]),
            "wall_thickness_m": float(d["wall_thickness_m"]), "steel_grade": str(d["steel_grade"]),
            "number_of_booster_stations": int(d["n_pumps"]),
            "nominal_pipeline_inlet_pressure_bar": float(d["pinlet_mpa"] * 10),
            "initial_compressor_discharge_pressure_bar": float(d["initial_discharge_mpa"] * 10),
            "delivery_pressure_bar": float(d["poutlet_mpa"] * 10),
            "booster_spacing_km": float(d["l_pump_km"]),
            "phase_label_in_source": "liquid", "selection_basis": "baseline_levelized_cost",
        }
        assumptions = {
            "operating_hours_per_year": self.config.operating_hours_per_year,
            "discount_rate": self.config.discount_rate,
            "pipeline_lifetime_years": float(data["z_pipe"]), "compression_lifetime_years": float(data["z_pumpcomp"]),
            "pipeline_fixed_opex_fraction": float(data["muOMpipe"]), "compression_fixed_opex_fraction": float(data["muOMpumpcomp"]),
            "electricity_price_eur_per_mwh": self.config.electricity_price_eur_per_mwh,
            "feed_pressure_bar": self.config.p_inlet_bar, "timeframe": self.config.timeframe,
            "cost_basis": "baseline", "resistance_use": "routing_only_no_financial_multiplier",
            "currency": "EUR", "cost_reference_year": 2021,
        }
        return DirectCost(annual, hourly, hourly / 3.6, terrain, design, assumptions, energy,
                          {"baseline": baseline})

    def calculate_fixed_design(self, route: RouteInput, annual_flow_t_per_year: float | None = None) -> FixedDesignApproximation:
        annual = annual_flow_t_per_year
        if annual is None:
            annual = route.annual_flow_t_per_year
        if annual is None:
            annual = self.config.benchmark_annual_flow_t_per_year
        if annual is None:
            raise ValueError(f"Scenario {route.scenario_id!r}: fixed_design coefficients require annual transported flow")
        return coefficients_from_fixed_design(self.calculate_fixed_flow(route, annual))

    def approximate_route(self, route: RouteInput, *, benchmark: DirectCost | None = None) -> Approximation | FixedDesignApproximation:
        if self.config.coefficient_method == "fixed_design":
            return coefficients_from_fixed_design(benchmark) if benchmark is not None else self.calculate_fixed_design(route)
        lo, hi, range_basis = self.config.flow_bounds(route)
        n = 2 if self.config.fit_method == "endpoints" else self.config.fit_samples
        fit_flows = np.linspace(lo, hi, n)
        # Interior validation points are evaluated independently of the fit.
        candidates = lo + (np.arange(self.config.validation_samples) + 0.5) / self.config.validation_samples * (hi - lo)
        validation_flows = [float(q) for q in candidates if not np.any(np.isclose(q, fit_flows, rtol=1e-12, atol=1e-8))]
        if not validation_flows:
            validation_flows = [float((fit_flows[0] + fit_flows[1]) / 2)]
        fitted = [self.calculate_fixed_flow(route, float(q)) for q in fit_flows]
        validated = [self.calculate_fixed_flow(route, q) for q in validation_flows]
        samples = [("fit", point) for point in fitted] + [("validation", point) for point in validated]
        if benchmark is not None and lo <= benchmark.annual_flow_t_per_year <= hi:
            q = benchmark.annual_flow_t_per_year
            if not any(math.isclose(q, p.annual_flow_t_per_year, rel_tol=1e-12) for _, p in samples):
                samples.append(("benchmark_check", benchmark))
                validated.append(benchmark)
        samples.sort(key=lambda item: item[1].annual_flow_t_per_year)
        coefficients, diagnostics, warnings = {}, {}, []
        for basis in fitted[0].costs:
            coefficients[basis] = {
                component: fit_affine(fit_flows, [p.costs[basis][COMPONENT_FIELDS[component]] for p in fitted])
                for component in FITTED_COMPONENTS
            }
            coefficients[basis]["opex"] = sum_coefficients(coefficients[basis]["fixed_opex"], coefficients[basis]["electricity_opex"])
            coefficients[basis]["total"] = sum_coefficients(*(coefficients[basis][k] for k in FITTED_COMPONENTS))
            diagnostics[basis] = {}
            for component, coefficient in coefficients[basis].items():
                errors = []
                validation_errors = []
                for role, point in samples:
                    actual = point.costs[basis][COMPONENT_FIELDS[component]]
                    error = coefficient.value(point.annual_flow_t_per_year) - actual
                    relative = 0.0 if actual == 0 and abs(error) < 1e-6 else (None if actual == 0 else 100 * error / actual)
                    errors.append((error, relative))
                    if role != "fit":
                        validation_errors.append((error, relative))
                rel = [e[1] for e in errors if e[1] is not None]
                validation_rel = [e[1] for e in validation_errors if e[1] is not None]
                diagnostics[basis][component] = {
                    "fit_sample_count": len(fitted), "validation_sample_count": len(validated),
                    "rmse_eur_per_year": math.sqrt(sum(e[0] ** 2 for e in errors) / len(errors)),
                    "max_abs_relative_error_percent": max(map(abs, rel), default=None),
                    "validation_max_abs_relative_error_percent": max(map(abs, validation_rel), default=None),
                    "max_underprediction_percent": max([0.0] + [-v for v in rel]),
                    "max_overprediction_percent": max([0.0] + rel),
                }
                if coefficient.intercept_eur_per_year < -1e-6:
                    warnings.append(f"{basis}/{component}: negative fitted intercept; do not interpret as physical fixed OPEX or extrapolate")
                if coefficient.slope_eur_per_t < -1e-12:
                    warnings.append(f"{basis}/{component}: negative fitted slope")
            total_error = diagnostics[basis]["total"]["max_abs_relative_error_percent"]
            if total_error is not None and total_error > self.config.fit_error_warning_percent:
                warnings.append(f"{basis}: maximum sampled total-cost error {total_error:.2f}% exceeds {self.config.fit_error_warning_percent:g}%")
            if min(coefficients[basis]["total"].value(q) for q in (lo, hi)) < 0:
                warnings.append(f"{basis}: fitted total cost becomes negative inside the valid range; do not use this fit")
        return Approximation(lo, hi, range_basis, self.config.fit_method, coefficients, diagnostics, samples, warnings)


def coefficients_from_fixed_design(point: DirectCost) -> FixedDesignApproximation:
    """Allocate annualized capital and maintenance to F, electricity to v."""
    coefficients = {}
    annual = point.annual_flow_t_per_year
    for basis, costs in point.costs.items():
        components = {
            "annualized_capex": AffineCoefficient(costs[COMPONENT_FIELDS["annualized_capex"]], 0.0),
            "fixed_opex": AffineCoefficient(costs[COMPONENT_FIELDS["fixed_opex"]], 0.0),
            "electricity_opex": AffineCoefficient(0.0, costs[COMPONENT_FIELDS["electricity_opex"]] / annual),
        }
        components["opex"] = sum_coefficients(components["fixed_opex"], components["electricity_opex"])
        components["total"] = sum_coefficients(components["annualized_capex"], components["opex"])
        for component, coefficient in components.items():
            _reconcile(f"Fixed design {basis}/{component}", coefficient.value(annual), costs[COMPONENT_FIELDS[component]])
        coefficients[basis] = components
    return FixedDesignApproximation(annual, coefficients, point)


def fit_affine(flows, costs) -> AffineCoefficient:
    """Unweighted least squares; with two points this is the endpoint secant."""
    x, y = np.asarray(flows, dtype=float), np.asarray(costs, dtype=float)
    if len(x) < 2 or len(x) != len(y) or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("At least two finite flow/cost pairs are required")
    dx = x - x.mean()
    denominator = float(dx @ dx)
    if denominator <= 0:
        raise ValueError("Distinct annual flows are required")
    slope = float(dx @ (y - y.mean()) / denominator)
    return AffineCoefficient(float(y.mean() - slope * x.mean()), slope)


def sum_coefficients(*coefficients) -> AffineCoefficient:
    return AffineCoefficient(sum(c.intercept_eur_per_year for c in coefficients), sum(c.slope_eur_per_t for c in coefficients))
