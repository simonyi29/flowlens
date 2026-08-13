param(
    [string]$Python = "python",
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $projectRoot

while ($true) {
    $arguments = @("-m", "api.worker", "run")
    if ($Config) {
        $arguments += @("--config", $Config)
    }
    & $Python @arguments
    if ($LASTEXITCODE -eq 0) {
        break
    }
    Write-Warning "FlowLens Worker exited with code $LASTEXITCODE; restarting in 10 seconds."
    Start-Sleep -Seconds 10
}
