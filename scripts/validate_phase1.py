"""Phase 1 acceptance checks: checkpoint, accuracy, inference latency."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import torch  # noqa: E402

from shared.pinn_model import load_checkpoint  # noqa: E402


def main() -> int:
    ckpt_path = ROOT / "models" / "pinn_reactor_v1.pt"
    metrics_path = ROOT / "models" / "pinn_reactor_v1_metrics.json"

    checks: list[tuple[str, bool, str]] = []

    if not ckpt_path.exists():
        checks.append(("checkpoint_exists", False, str(ckpt_path)))
        _report(checks)
        return 1

    model, normalizer, metrics = load_checkpoint(ckpt_path, torch.device("cpu"))
    val_err = metrics.get("best_val_temp_relative_error_pct", 999.0)
    checks.append(("checkpoint_loads", True, "OK"))
    checks.append(("val_temp_error_le_2pct", val_err <= 2.0, f"{val_err:.2f}%"))

    # Grid inference benchmark (20x30 = 600 points, PINN-06)
    grid = []
    for i in range(20):
        for j in range(30):
            r, z = i / 19, j / 29
            grid.append([r, 0.0, z, 0.5, 290 / 400, 15.5 / 20, 18500 / 25000, 3000 / 3300, 0.05])
    x = torch.tensor(grid, dtype=torch.float32)
    t0 = time.perf_counter()
    with torch.no_grad():
        _ = normalizer.denormalize_output(model(normalizer.normalize_input(x)))
    ms = (time.perf_counter() - t0) * 1000
    checks.append(("grid_inference_le_200ms_cpu", ms <= 200, f"{ms:.1f} ms"))

    if metrics_path.exists():
        with open(metrics_path, encoding="utf-8") as f:
            file_metrics = json.load(f)
        checks.append(("metrics_file", True, metrics_path.name))
        checks.append(
            ("passed_temp_accuracy_flag", file_metrics.get("passed_temp_accuracy", False), "metrics json")
        )

    _report(checks)
    return 0 if all(c[1] for c in checks) else 1


def _report(checks: list[tuple[str, bool, str]]) -> None:
    print("Phase 1 validation")
    print("-" * 50)
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
    print("-" * 50)
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"Result: {passed}/{len(checks)} checks passed")


if __name__ == "__main__":
    sys.exit(main())
