"""PINN Core Service – trained model inference + hot-reload (PINN-01 to PINN-06, NFR-M-04)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog
import torch

from shared.config import get_settings, load_yaml_config
from shared.messaging import RedisBus, RabbitMQBus
from shared.models import PINNOutput
from shared.pinn_model import PINNReactor, PINNNormalizer, load_checkpoint

log = structlog.get_logger()
settings = get_settings()
config = load_yaml_config()

GRID_R = 20
GRID_Z = 30
HOT_RELOAD_INTERVAL_SEC = 30


class PINNCoreService:
    def __init__(self):
        self.redis = RedisBus(settings.redis_url)
        self.rabbit = RabbitMQBus(settings.rabbitmq_url)
        pinn_cfg = config.get("pinn", {})
        self.max_inference_ms = pinn_cfg.get("max_inference_ms", 50)
        self.model_path = Path(os.getenv("PINN_MODEL_PATH", "/app/models/pinn_reactor_v1.pt"))
        self.device = self._resolve_device()
        self.model: PINNReactor | None = None
        self.normalizer: PINNNormalizer | None = None
        self.metrics: dict = {}
        self._model_mtime: float = 0.0
        self._grid_r = torch.linspace(0, 1, GRID_R)
        self._grid_z = torch.linspace(0, 1, GRID_Z)

    @staticmethod
    def _resolve_device() -> torch.device:
        requested = os.getenv("PINN_INFERENCE_DEVICE", "cpu").lower()
        if requested == "cuda" and torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")

    def _load_model(self, force: bool = False) -> bool:
        if not self.model_path.exists():
            log.warning("pinn_checkpoint_missing", path=str(self.model_path))
            return self._init_fallback_model()

        mtime = self.model_path.stat().st_mtime
        if not force and mtime == self._model_mtime and self.model is not None:
            return True

        try:
            self.model, self.normalizer, self.metrics = load_checkpoint(self.model_path, self.device)
            self._model_mtime = mtime
            err = self.metrics.get("best_val_temp_relative_error_pct", "N/A")
            log.info(
                "pinn_model_loaded",
                path=str(self.model_path),
                device=str(self.device),
                val_temp_error_pct=err,
            )
            return True
        except Exception as e:
            log.error("pinn_load_failed", error=str(e))
            return self._init_fallback_model()

    def _init_fallback_model(self) -> bool:
        """Untrained fallback when checkpoint is absent."""
        pinn_cfg = config.get("pinn", {})
        self.model = PINNReactor(
            hidden=pinn_cfg.get("neurons_per_layer", 128),
            layers=pinn_cfg.get("hidden_layers", 6),
        ).to(self.device)
        self.model.eval()
        self.normalizer = PINNNormalizer(
            input_mean=torch.ones(9, device=self.device),
            input_std=torch.ones(9, device=self.device),
            target_mean=torch.zeros(4, device=self.device),
            target_std=torch.ones(4, device=self.device),
        )
        log.warning("pinn_using_untrained_fallback")
        return False

    async def start(self) -> None:
        await self.redis.connect()
        await self.rabbit.connect()
        self._load_model(force=True)
        asyncio.create_task(self._inference_loop())
        asyncio.create_task(self._hot_reload_loop())
        log.info("pinn_core_started", device=str(self.device))

    async def _hot_reload_loop(self) -> None:
        """NDT-ST-02 / NFR-M-04: reload weights without full stack restart."""
        while True:
            reload_flag = await self.redis.get_state("ndt:pinn:reload_request")
            if reload_flag:
                log.info("pinn_hot_reload_api_trigger")
                self._load_model(force=True)
                await self.redis.set_state("ndt:pinn:reload_request", {}, ttl=1)

            if self.model_path.exists():
                mtime = self.model_path.stat().st_mtime
                if mtime != self._model_mtime:
                    log.info("pinn_hot_reload_detected")
                    self._load_model(force=True)

            await asyncio.sleep(5)

    def _build_grid_inputs(self, reactor: dict) -> torch.Tensor:
        """Batch 20×30 grid with live boundary conditions (PINN-02)."""
        t_in = reactor.get("temp_inlet_c", 290) / 400.0
        p_in = reactor.get("pressure_mpa", 15.5) / 20.0
        m_dot = reactor.get("mass_flow_kg_s", 18500) / 25000.0
        q = reactor.get("power_mwth", 3000) / 3300.0
        theta_phys = 0.05
        t_norm = (time.time() % 86400) / 86400.0

        rows: list[list[float]] = []
        for r in self._grid_r.tolist():
            for z in self._grid_z.tolist():
                rows.append([r, 0.0, z, t_norm, t_in, p_in, m_dot, q, theta_phys])

        return torch.tensor(rows, dtype=torch.float32, device=self.device)

    def _infer(self, reactor_state: dict) -> PINNOutput:
        t0 = time.perf_counter()
        r = reactor_state.get("reactor", reactor_state)

        if self.model is None or self.normalizer is None:
            self._load_model(force=True)

        grid_in = self._build_grid_inputs(r)
        with torch.no_grad():
            pred = self.normalizer.denormalize_output(  # type: ignore[union-attr]
                self.model(self.normalizer.normalize_input(grid_in))  # type: ignore[union-attr]
            )
        pred_np = pred.cpu().numpy()

        # Reshape to 2D fields
        temp_field = pred_np[:, 0].reshape(GRID_R, GRID_Z).tolist()
        pressure_field = pred_np[:, 1].reshape(GRID_R, GRID_Z).tolist()
        velocity = pred_np[:, 2].reshape(GRID_R, GRID_Z).tolist()
        fuel_surface = pred_np[:, 3].reshape(GRID_R, GRID_Z).tolist()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        if elapsed_ms > 200 and self.device.type == "cpu":
            log.warning("pinn_inference_slow", inference_ms=round(elapsed_ms, 2))

        return PINNOutput(
            temp_coolant=temp_field,
            pressure_field=pressure_field,
            velocity_z=velocity,
            temp_fuel_surface=fuel_surface,
            inference_ms=elapsed_ms,
        )

    async def _inference_loop(self) -> None:
        while True:
            try:
                state = await self.redis.get_state("ndt:state:current")
                if state and self.model is not None:
                    pinn_out = self._infer(state)
                    payload = {
                        **pinn_out.model_dump(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "grid": {"r_steps": GRID_R, "z_steps": GRID_Z},
                        "model_loaded": self.model_path.exists(),
                        "val_temp_error_pct": self.metrics.get("best_val_temp_relative_error_pct"),
                    }
                    await self.redis.set_state("ndt:pinn:current", payload, ttl=5)
                    await self.rabbit.publish("pinn.output", payload)
            except Exception as e:
                log.error("pinn_inference_error", error=str(e))
            await asyncio.sleep(0.2)  # ≤ 200 ms dashboard update (UI-RQ-01)


async def main() -> None:
    service = PINNCoreService()
    await service.start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
