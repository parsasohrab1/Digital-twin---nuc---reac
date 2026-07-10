"""Dataset builder from reactor_synthetic_data_30days.parquet (PINN-02)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

CORE_HEIGHT_M = 3.66
CORE_RADIUS_M = 1.52


def _build_spatial_targets(
    row: pd.Series,
    r: float,
    theta: float,
    z: float,
) -> np.ndarray:
    """Derive spatial field targets from reactor scalars (physics-based labels)."""
    t_in = row["temp_inlet"]
    t_out = row["temp_outlet"]
    t_hot = row.get("temp_hot_channel", t_out + 15)
    t_clad = row["temp_cladding_max"]
    p_in = row["pressure_primary"]
    m_dot = row["mass_flow_rate"]
    power = row["power"]

    # Axial coolant temperature profile with radial peaking factor
    z_norm = z
    radial_factor = 1.0 + 0.3 * r * np.cos(theta)
    t_coolant = t_in + (t_out - t_in) * z_norm * radial_factor

    # Pressure drop along core (~0.15 MPa nominal)
    dp = row.get("pressure_drop_core", 0.15)
    pressure = p_in - dp * z_norm

    # Axial velocity (normalized m/s scale)
    rho = 780.0  # kg/m³ approx
    area = np.pi * CORE_RADIUS_M ** 2
    v_z = m_dot / (rho * area)

    # Fuel surface: hot channel at high r, mid-core z
    radial_peak = 0.5 + 0.5 * r
    axial_peak = np.sin(np.pi * z_norm)
    t_fuel = t_clad * radial_peak * (0.7 + 0.3 * axial_peak)

    return np.array([t_coolant, pressure, v_z, t_fuel], dtype=np.float32)


class ReactorPINNDataset(Dataset):
    """
    Point-wise PINN dataset.
    Each sample: input (r, θ, z, t, T_in, P_in, m_dot, Q, θ_phys) → 4 outputs.
    """

    def __init__(
        self,
        parquet_path: Path,
        sample_stride: int = 3600,
        points_per_row: int = 32,
        seed: int = 42,
    ):
        if not parquet_path.exists():
            raise FileNotFoundError(
                f"Parquet not found: {parquet_path}. Run: python data.py"
            )

        df = pd.read_parquet(parquet_path)
        df = df.iloc[::sample_stride].reset_index(drop=True)

        rng = np.random.default_rng(seed)
        inputs_list: list[np.ndarray] = []
        targets_list: list[np.ndarray] = []

        t0 = pd.Timestamp(df["timestamp"].iloc[0])
        for _, row in df.iterrows():
            t_sec = (pd.Timestamp(row["timestamp"]) - t0).total_seconds()
            t_norm = (t_sec % 86400) / 86400.0

            t_in_n = row["temp_inlet"] / 400.0
            p_in_n = row["pressure_primary"] / 20.0
            m_dot_n = row["mass_flow_rate"] / 25000.0
            q_n = row["power"] / 3300.0
            theta_phys = row.get("roughness_factor", 0.05)

            for _ in range(points_per_row):
                r = float(rng.uniform(0, 1))
                theta = float(rng.uniform(0, 2 * np.pi))
                z = float(rng.uniform(0, 1))

                inp = np.array(
                    [r, theta / (2 * np.pi), z, t_norm, t_in_n, p_in_n, m_dot_n, q_n, theta_phys],
                    dtype=np.float32,
                )
                tgt = _build_spatial_targets(row, r, theta, z)
                inputs_list.append(inp)
                targets_list.append(tgt)

        self.inputs = torch.tensor(np.stack(inputs_list), dtype=torch.float32)
        self.targets = torch.tensor(np.stack(targets_list), dtype=torch.float32)

    def __len__(self) -> int:
        return len(self.inputs)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.inputs[idx], self.targets[idx]

    @staticmethod
    def compute_normalizer(
        inputs: torch.Tensor,
        targets: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        return (
            inputs.mean(dim=0),
            inputs.std(dim=0).clamp(min=1e-6),
            targets.mean(dim=0),
            targets.std(dim=0).clamp(min=1e-6),
        )
