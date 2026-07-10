"""Offline PINN training (Phase 1 – PINN-01 to PINN-04)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch
import yaml
from torch.utils.data import DataLoader, random_split

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from shared.pinn_model import PINNReactor, PINNNormalizer, save_checkpoint  # noqa: E402
from training.dataset import ReactorPINNDataset  # noqa: E402
from training.pinn_loss import physics_informed_loss  # noqa: E402


def load_config() -> dict:
    config_path = ROOT / "config" / "ndt.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def evaluate(
    model: PINNReactor,
    normalizer: PINNNormalizer,
    loader: DataLoader,
    device: torch.device,
    lambdas: dict[str, float],
) -> dict[str, float]:
    model.eval()
    total_mse = 0.0
    total_temp_err = 0.0
    n = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            x_n = normalizer.normalize_input(x)
            pred_n = model(x_n)
            pred = normalizer.denormalize_output(pred_n)
            total_mse += torch.mean((pred - y) ** 2).item() * len(x)
            # Relative error on coolant temperature (primary metric)
            rel = torch.abs(pred[:, 0] - y[:, 0]) / y[:, 0].clamp(min=1.0)
            total_temp_err += rel.sum().item()
            n += len(x)
    return {
        "val_mse": total_mse / max(n, 1),
        "val_temp_relative_error_pct": 100.0 * total_temp_err / max(n, 1),
    }


def train(args: argparse.Namespace) -> dict:
    config = load_config()
    pinn_cfg = config.get("pinn", {})
    train_cfg = config.get("training", {})

    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    print(f"Device: {device}")

    stride = args.stride if args.stride is not None else train_cfg.get("sample_row_stride", 60)
    points_per_row = args.points_per_row if args.points_per_row is not None else train_cfg.get("points_per_row", 32)
    epochs = args.epochs if args.epochs is not None else train_cfg.get("epochs", 40)
    batch_size = args.batch_size if args.batch_size is not None else train_cfg.get("batch_size", 512)
    lr = args.lr if args.lr is not None else train_cfg.get("learning_rate", 1e-3)
    val_ratio = args.val_ratio if args.val_ratio is not None else train_cfg.get("val_ratio", 0.2)

    parquet = ROOT / args.data
    dataset = ReactorPINNDataset(
        parquet_path=parquet,
        sample_stride=stride,
        points_per_row=points_per_row,
    )
    print(f"Dataset size: {len(dataset):,} samples")

    val_size = int(len(dataset) * val_ratio)
    train_size = len(dataset) - val_size
    train_ds, val_ds = random_split(
        dataset,
        [train_size, val_size],
        generator=torch.Generator().manual_seed(42),
    )

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size)

    # Normalizer from full dataset statistics
    inp_mean, inp_std, tgt_mean, tgt_std = ReactorPINNDataset.compute_normalizer(
        dataset.inputs, dataset.targets
    )
    normalizer = PINNNormalizer(
        input_mean=inp_mean.to(device),
        input_std=inp_std.to(device),
        target_mean=tgt_mean.to(device),
        target_std=tgt_std.to(device),
    )

    model = PINNReactor(
        hidden=pinn_cfg.get("neurons_per_layer", 128),
        layers=pinn_cfg.get("hidden_layers", 6),
    ).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

    lambdas = {
        "lambda_ns": pinn_cfg.get("lambda_ns", 0.1),
        "lambda_energy": pinn_cfg.get("lambda_energy", 0.1),
        "lambda_neutronics": pinn_cfg.get("lambda_neutronics", 0.01),
    }

    best_val_err = float("inf")
    history: list[dict] = []

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss = 0.0
        n_batches = 0
        t0 = time.perf_counter()

        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            x_n = normalizer.normalize_input(x)
            pred_n = model(x_n)
            pred = normalizer.denormalize_output(pred_n)

            loss, components = physics_informed_loss(
                pred, y, x,
                lambda_ns=lambdas["lambda_ns"],
                lambda_energy=lambdas["lambda_energy"],
                lambda_neutronics=lambdas["lambda_neutronics"],
            )

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            epoch_loss += components["loss_total"]
            n_batches += 1

        val_metrics = evaluate(model, normalizer, val_loader, device, lambdas)
        scheduler.step(val_metrics["val_mse"])

        elapsed = time.perf_counter() - t0
        record = {
            "epoch": epoch,
            "train_loss": epoch_loss / max(n_batches, 1),
            **val_metrics,
            "elapsed_sec": round(elapsed, 2),
        }
        history.append(record)

        print(
            f"Epoch {epoch:3d}/{epochs} | "
            f"loss={record['train_loss']:.6f} | "
            f"val_temp_err={val_metrics['val_temp_relative_error_pct']:.2f}% | "
            f"{elapsed:.1f}s"
        )

        if val_metrics["val_temp_relative_error_pct"] < best_val_err:
            best_val_err = val_metrics["val_temp_relative_error_pct"]
            out_path = ROOT / args.output
            metrics = {
                "best_val_temp_relative_error_pct": best_val_err,
                "epochs_trained": epoch,
                "train_samples": train_size,
                "val_samples": val_size,
                "lambdas": lambdas,
                "history": history,
            }
            save_checkpoint(out_path, model, normalizer, metrics, pinn_cfg)

    # Benchmark inference on grid (20×30 = 600 points)
    model.eval()
    nr, nz = 20, 30
    grid_inputs = []
    for i in range(nr):
        for j in range(nz):
            r = i / max(nr - 1, 1)
            z = j / max(nz - 1, 1)
            grid_inputs.append([r, 0.0, z, 0.5, 290 / 400, 15.5 / 20, 18500 / 25000, 3000 / 3300, 0.05])
    grid = torch.tensor(grid_inputs, dtype=torch.float32, device=device)
    t_inf = time.perf_counter()
    with torch.no_grad():
        _ = normalizer.denormalize_output(model(normalizer.normalize_input(grid)))
    inference_ms = (time.perf_counter() - t_inf) * 1000

    final_metrics_path = ROOT / "models" / "pinn_reactor_v1_metrics.json"
    final_metrics = {
        "best_val_temp_relative_error_pct": best_val_err,
        "target_temp_error_pct": 2.0,
        "passed_temp_accuracy": best_val_err <= 2.0,
        "grid_inference_ms": round(inference_ms, 2),
        "inference_target_ms_cpu": 200,
        "inference_target_ms_gpu": 50,
        "device": str(device),
        "checkpoint": str(args.output),
    }
    final_metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with open(final_metrics_path, "w", encoding="utf-8") as f:
        json.dump(final_metrics, f, indent=2)

    print("\n" + "=" * 60)
    print(f"Best validation temp error: {best_val_err:.2f}% (target <= 2%)")
    print(f"Grid inference: {inference_ms:.1f} ms")
    print(f"Checkpoint: {ROOT / args.output}")
    print(f"Metrics: {final_metrics_path}")
    print("=" * 60)

    return final_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train PINN reactor model")
    parser.add_argument("--data", default="reactor_synthetic_data_30days.parquet")
    parser.add_argument("--output", default="models/pinn_reactor_v1.pt")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--stride", type=int, default=None, help="Row stride when sampling parquet")
    parser.add_argument("--points-per-row", type=int, default=None)
    parser.add_argument("--val-ratio", type=float, default=None)
    parser.add_argument("--cpu", action="store_true", help="Force CPU even if CUDA available")
    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
