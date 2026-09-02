from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
import traceback
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run top-level test_* functions without pytest fixtures.")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    passed = 0
    failed = 0
    for index, path in enumerate(args.paths):
        resolved = path.resolve()
        spec = importlib.util.spec_from_file_location(f"plain_test_{index}_{resolved.stem}", resolved)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load test module: {resolved}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_") or inspect.signature(function).parameters:
                continue
            try:
                function()
                passed += 1
                print(f"PASS {resolved.name}::{name}", flush=True)
            except Exception:
                failed += 1
                print(f"FAIL {resolved.name}::{name}", flush=True)
                traceback.print_exc()
    print(f"plain tests: passed={passed} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
