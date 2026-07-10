"""Physics-informed loss terms (PINN-04, PINN-05)."""

from __future__ import annotations

import torch


def mse_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    return torch.mean((pred - target) ** 2)


def navier_stokes_residual(pred: torch.Tensor, inputs: torch.Tensor) -> torch.Tensor:
    """
    Simplified NS residual: mass-flow consistency with axial velocity v_z.
    inputs columns: r, theta, z, t, T_in, P_in, m_dot, Q, theta_phys
    pred columns: T_coolant, P, v_z, T_fuel_surface
    """
    m_dot_norm = inputs[:, 6]
    v_z = pred[:, 2]
    rho = 780.0
    area = 3.14159 * 1.52 ** 2
    expected_v = (m_dot_norm * 25000.0) / (rho * area)
    return torch.mean((v_z - expected_v) ** 2) / (10.0 ** 2)


def energy_residual(pred: torch.Tensor, inputs: torch.Tensor) -> torch.Tensor:
    """
    Energy balance: T_out - T_in ~ Q / (m_dot * cp) along axial direction.
    """
    t_in = inputs[:, 4] * 400.0
    t_coolant = pred[:, 0]
    q = inputs[:, 7] * 3300.0
    m_dot = inputs[:, 6] * 25000.0
    cp = 5.5
    delta_t_expected = q / (m_dot.clamp(min=100.0) * cp) * 1000.0
    delta_t_pred = t_coolant - t_in
    return torch.mean((delta_t_pred - delta_t_expected) ** 2) / (400.0 ** 2)


def neutronics_coupling_residual(pred: torch.Tensor, inputs: torch.Tensor) -> torch.Tensor:
    """
    Fuel surface temperature should correlate with thermal power Q.
    """
    q = inputs[:, 7]
    t_fuel = pred[:, 3]
    # Normalized: higher Q → higher T_fuel
    expected = 300.0 + 80.0 * q
    return torch.mean((t_fuel - expected) ** 2) / (500.0 ** 2)


def physics_informed_loss(
    pred: torch.Tensor,
    target: torch.Tensor,
    inputs: torch.Tensor,
    lambda_ns: float = 0.1,
    lambda_energy: float = 0.1,
    lambda_neutronics: float = 0.01,
) -> tuple[torch.Tensor, dict[str, float]]:
    loss_mse = mse_loss(pred, target)
    loss_ns = navier_stokes_residual(pred, inputs)
    loss_energy = energy_residual(pred, inputs)
    loss_neut = neutronics_coupling_residual(pred, inputs)

    total = (
        loss_mse
        + lambda_ns * loss_ns
        + lambda_energy * loss_energy
        + lambda_neutronics * loss_neut
    )
    components = {
        "loss_mse": float(loss_mse.detach()),
        "loss_ns": float(loss_ns.detach()),
        "loss_energy": float(loss_energy.detach()),
        "loss_neutronics": float(loss_neut.detach()),
        "loss_total": float(total.detach()),
    }
    return total, components
