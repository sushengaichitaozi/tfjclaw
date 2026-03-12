param(
    [string]$EnvFile = "$PSScriptRoot\.env"
)

$pythonExe = "D:\miniconda3\envs\openai\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$PSCommandPath`"",
        "-EnvFile",
        "`"$EnvFile`""
    )
    exit
}

Set-Location $PSScriptRoot
& $pythonExe "$PSScriptRoot\run_dashboard.py" --env-file $EnvFile
