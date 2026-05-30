param(
    [string]$TargetUrl = "https://staging.criavideo.pro/video",
    [ValidateSet("remote", "local-proxy")]
    [string]$RuntimeMode = "local-proxy",
    [string]$ApiTargetUrl = "https://staging.criavideo.pro",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktopDir = Resolve-Path (Join-Path $repoRoot "desktop\windows-shell")

try {
    $env:CRIAVIDEO_DESKTOP_TARGET_URL = $TargetUrl
    $env:CRIAVIDEO_DESKTOP_RUNTIME_MODE = $RuntimeMode
    if ($ApiTargetUrl) {
        $env:CRIAVIDEO_DESKTOP_API_TARGET_URL = $ApiTargetUrl
    }
    else {
        Remove-Item Env:CRIAVIDEO_DESKTOP_API_TARGET_URL -ErrorAction SilentlyContinue
    }

    Push-Location $desktopDir
    try {
        if (-not $SkipInstall -or -not (Test-Path (Join-Path $desktopDir "node_modules"))) {
            npm install
        }

        npm run start
    }
    finally {
        Pop-Location
    }
}
finally {
    Remove-Item Env:CRIAVIDEO_DESKTOP_TARGET_URL -ErrorAction SilentlyContinue
    Remove-Item Env:CRIAVIDEO_DESKTOP_RUNTIME_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:CRIAVIDEO_DESKTOP_API_TARGET_URL -ErrorAction SilentlyContinue
}