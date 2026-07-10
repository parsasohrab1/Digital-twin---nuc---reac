"""Shared PINN architecture for training and inference (PINN-01, PINN-02)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

INPUT_DIM = 9
OUTPUT_DIM = 4
INPUT_NAMES = ["r", "theta", "z", "t", "T_in", "P_in", "m_dot", "Q", "theta_phys"]
OUTPUT_NAMES = ["T_coolant", "P", "v_z", "T_fuel_surface"]


@dataclass
class PINNNormalizer:
    """Feature/target scaling stored in checkpoint."""

    input_mean: torch.Tensor
    input_std: torch.Tensor
    target_mean: torch.Tensor
    target_std: torch.Tensor

    def normalize_input(self, x: torch.Tensor) -> torch.Tensor:
        return (x - self.input_mean) / self.input_std.clamp(min=1e-6)

    def denormalize_output(self, y: torch.Tensor) -> torch.Tensor:
        return y * self.target_std + self.target_mean

    def to_dict(self) -> dict[str, list[float]]:
        return {
            "input_mean": self.input_mean.tolist(),
            "input_std": self.input_std.tolist(),
            "target_mean": self.target_mean.tolist(),
            "target_std": self.target_std.tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, list[float]]) -> PINNNormalizer:
        return cls(
            input_mean=torch.tensor(data["input_mean"], dtype=torch.float32),
            input_std=torch.tensor(data["input_std"], dtype=torch.float32),
            target_mean=torch.tensor(data["target_mean"], dtype=torch.float32),
            target_std=torch.tensor(data["target_std"], dtype=torch.float32),
        )


class PINNReactor(nn.Module):
    """6 hidden layers × 128 neurons, Swish (SiLU) activation."""

    def __init__(
        self,
        input_dim: int = INPUT_DIM,
        output_dim: int = OUTPUT_DIM,
        hidden: int = 128,
        layers: int = 6,
    ):
        super().__init__()
        dims = [input_dim] + [hidden] * layers + [output_dim]
        modules: list[nn.Module] = []
        for i in range(len(dims) - 2):
            modules.append(nn.Linear(dims[i], dims[i + 1]))
            modules.append(nn.SiLU())
        modules.append(nn.Linear(dims[-2], dims[-1]))
        self.net = nn.Sequential(*modules)
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.hidden = hidden
        self.layers = layers

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def save_checkpoint(
    path: Path,
    model: PINNReactor,
    normalizer: PINNNormalizer,
    metrics: dict[str, Any],
    config: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "normalizer": normalizer.to_dict(),
            "metrics": metrics,
            "config": config,
            "architecture": {
                "input_dim": model.input_dim,
                "output_dim": model.output_dim,
                "hidden": model.hidden,
                "layers": model.layers,
            },
        },
        path,
    )


def load_checkpoint(
    path: Path,
    device: torch.device | None = None,
) -> tuple[PINNReactor, PINNNormalizer, dict[str, Any]]:
    dev = device or torch.device("cpu")
    ckpt = torch.load(path, map_location=dev, weights_only=False)
    arch = ckpt["architecture"]
    model = PINNReactor(
        input_dim=arch["input_dim"],
        output_dim=arch["output_dim"],
        hidden=arch["hidden"],
        layers=arch["layers"],
    )
    model.load_state_dict(ckpt["model_state_dict"])
    model.to(dev)
    model.eval()
    normalizer = PINNNormalizer.from_dict(ckpt["normalizer"])
    normalizer.input_mean = normalizer.input_mean.to(dev)
    normalizer.input_std = normalizer.input_std.to(dev)
    normalizer.target_mean = normalizer.target_mean.to(dev)
    normalizer.target_std = normalizer.target_std.to(dev)
    return model, normalizer, ckpt.get("metrics", {})
