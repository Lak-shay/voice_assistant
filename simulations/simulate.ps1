param(
    [ValidateSet("text", "audio")]
    [string]$Mode = "text",
    [int]$Concurrency = 10,
    [string]$Scenarios = "scenarios.yaml"
)

$rootDir = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$rootDir\agent.py")) {
    $rootDir = Get-Location
}

# Load environment variables from .env.dev
$envDevPath = Join-Path $rootDir ".env.dev"
if (Test-Path $envDevPath) {
    Get-Content $envDevPath | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
        $parts = $_ -split '=', 2
        $key = $parts[0].Trim()
        $val = $parts[1].Trim()
        [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
    }
}

# Resolve scenario file path
$scenarioPath = $Scenarios
if (-not (Test-Path $scenarioPath)) {
    if (Test-Path (Join-Path $PSScriptRoot $Scenarios)) {
        $scenarioPath = Join-Path $PSScriptRoot $Scenarios
    } elseif (Test-Path (Join-Path $rootDir "simulations\$Scenarios")) {
        $scenarioPath = Join-Path $rootDir "simulations\$Scenarios"
    }
}

# Resolve lk CLI path and agent.py
$lkPath = if (Test-Path "$rootDir\.venv\Scripts\lk.exe") {
    "$rootDir\.venv\Scripts\lk.exe"
} elseif (Test-Path ".\.venv\Scripts\lk.exe") {
    ".\.venv\Scripts\lk.exe"
} else {
    "lk"
}

$agentPath = if (Test-Path "agent.py") { "agent.py" } else { Join-Path $rootDir "agent.py" }

Write-Host "Running LiveKit AI Caller Simulation ($Mode mode, Concurrency: $Concurrency, Scenarios: $scenarioPath)..." -ForegroundColor Cyan
& $lkPath agent simulate $Mode --concurrency $Concurrency --scenarios $scenarioPath $agentPath
