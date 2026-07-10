# Nuclear Digital Twin – setup & run (Windows PowerShell)

param(
    [ValidateSet("setup", "up", "down", "logs", "status", "generate-data", "train-pinn", "validate-phase1", "validate-phase3", "validate-phase4")]
    [string]$Action = "setup"
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Root
Set-Location $Root

switch ($Action) {
    "setup" {
        if (-not (Test-Path ".env")) {
            Copy-Item ".env.example" ".env"
            Write-Host "Created .env from .env.example"
        }
        Write-Host "Setup complete. Run: .\scripts\ndt.ps1 up"
    }
    "up" {
        docker compose up -d --build
        Write-Host ""
        Write-Host "NDT is starting..."
        Write-Host "  Dashboard:  http://localhost:5173"
        Write-Host "  API Docs:   http://localhost:8000/docs"
        Write-Host "  Nginx:      http://localhost"
    }
    "down" {
        docker compose down
    }
    "logs" {
        docker compose logs -f
    }
    "status" {
        docker compose ps
    }
    "generate-data" {
        python scripts/generate_parquet.py
    }
    "train-pinn" {
        if (-not (Test-Path "reactor_synthetic_data_30days.parquet")) {
            python scripts/generate_parquet.py
        }
        pip install -r training/requirements.txt -q
        python training/train_pinn.py --cpu
        python scripts/validate_phase1.py
    }
    "validate-phase1" {
        python scripts/validate_phase1.py
    }
    "validate-phase3" {
        python scripts/validate_phase3.py
    }
    "validate-phase4" {
        python scripts/validate_phase4.py
    }
}
