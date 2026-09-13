"""One strict YAML loader shared by normal and benchmark entry points."""
import argparse
from dataclasses import fields
from pathlib import Path
import yaml
from .models import Settings


def load_config(path):
    path = Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as handle: raw = yaml.safe_load(handle)
    if not isinstance(raw,dict): raise ValueError("Configuration must be a YAML mapping")
    benchmark = raw.pop("benchmark",{})
    if not isinstance(benchmark,dict): raise ValueError("benchmark must be a mapping")
    unknown = set(raw)-{field.name for field in fields(Settings)}
    if unknown: raise ValueError(f"Unknown settings: {sorted(unknown)}")
    if set(benchmark)-{"repetitions","warmup_runs"}:
        raise ValueError("benchmark accepts only repetitions and warmup_runs; distances are calculated from nodes")
    for key in ("raster_path","workbook_path","output_dir"):
        if key not in raw: raise ValueError(f"Missing setting: {key}")
        value = Path(str(raw[key])).expanduser()
        raw[key] = value.resolve() if value.is_absolute() else (path.parent/value).resolve()
    settings = Settings(**raw)
    for key,default,minimum in (("repetitions",1,1),("warmup_runs",0,0)):
        value = benchmark.get(key,default)
        if isinstance(value,bool) or not isinstance(value,int) or value<minimum:
            raise ValueError(f"benchmark.{key} must be an integer >= {minimum}")
        benchmark[key] = value
    return settings,benchmark


def load_settings(path):
    return load_config(path)[0]


def config_argument(default):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=default)
    return parser.parse_args().config
