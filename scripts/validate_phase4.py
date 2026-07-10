"""Phase 4 acceptance – dashboard UI files and API routes."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _extract_api_routes(main_py: str) -> set[str]:
    return set(re.findall(r'@app\.(?:get|post|put|delete|websocket)\("([^"]+)"', main_py))


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    ui_files = [
        "frontend/src/pages/Dashboard.tsx",
        "frontend/src/pages/Settings.tsx",
        "frontend/src/components/Heatmap.tsx",
        "frontend/src/components/Layout.tsx",
        "frontend/src/hooks/useTheme.ts",
        "frontend/src/hooks/useReactorDashboard.ts",
        "frontend/src/lib/api.ts",
    ]
    for f in ui_files:
        p = ROOT / f
        checks.append((f"file:{f}", p.exists(), "OK" if p.exists() else "missing"))

    pkg = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    checks.append(("react-router-dom", "react-router-dom" in pkg, "dependency"))

    main_py = (ROOT / "backend/api_gateway/main.py").read_text(encoding="utf-8")
    paths = _extract_api_routes(main_py)
    required = {
        "/api/v1/reactor/heatmap",
        "/api/v1/prediction",
        "/api/v1/config/ui",
        "/api/v1/config/thresholds",
        "/api/v1/export/download",
        "/api/v1/events/log",
        "/api/v1/pinn/reload",
    }
    for ep in required:
        checks.append((f"api:{ep}", ep in paths, "registered"))
    checks.append(("openapi_tags", "openapi_tags" in main_py, "NFR-M-02"))

    theme_hook = (ROOT / "frontend/src/hooks/useTheme.ts").read_text(encoding="utf-8")
    checks.append(("dark_mode_persist", "localStorage" in theme_hook, "UI-RQ-07"))

    dashboard = (ROOT / "frontend/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
    checks.append(("two_step_dss", "step === 1" in dashboard, "UI-RQ-06"))
    checks.append(("heatmap_component", "Heatmap" in dashboard, "UI-RQ-01"))
    checks.append(("tte_display", "TTE" in dashboard or "tte" in dashboard.lower(), "UI-RQ-04"))

    settings = (ROOT / "frontend/src/pages/Settings.tsx").read_text(encoding="utf-8")
    checks.append(("settings_export", "export/download" in settings, "API-RQ-03"))
    checks.append(("settings_pinn_reload", "pinn/reload" in settings, "UI-RQ-10"))
    checks.append(("event_log_30d", "days=30" in settings, "UI-RQ-11"))

    app_tsx = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    checks.append(("settings_route", "/settings" in app_tsx, "UI-RQ-08"))

    _report(checks)
    return 0 if all(c[1] for c in checks) else 1


def _report(checks: list[tuple[str, bool, str]]) -> None:
    print("Phase 4 validation")
    print("-" * 50)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print("-" * 50)
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"Result: {passed}/{len(checks)} checks passed")


if __name__ == "__main__":
    sys.exit(main())
