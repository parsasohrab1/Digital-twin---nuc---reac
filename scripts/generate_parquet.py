"""Generate synthetic parquet without Persian console output (Windows-safe)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data import SyntheticReactorDataGenerator  # noqa: E402


def main() -> None:
    out = ROOT / "reactor_synthetic_data_30days.parquet"
    if out.exists():
        print(f"Already exists: {out}")
        return

    gen = SyntheticReactorDataGenerator(duration_days=30, time_step_sec=60)
    df = gen.generate()
    df.to_parquet(out, compression="snappy")
    print(f"Saved {len(df):,} rows -> {out}")


if __name__ == "__main__":
    main()
