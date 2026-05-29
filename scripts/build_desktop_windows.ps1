param(
    [string]$TargetUrl = "https://criavideo.pro/video",
    [switch]$SkipInstall,
    [switch]$SkipCopyToStatic
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktopDir = Resolve-Path (Join-Path $repoRoot "desktop\windows-shell")
$configPath = Join-Path $desktopDir "desktop-config.json"
$downloadsDir = Join-Path $repoRoot "static\downloads"

$originalConfig = Get-Content -Raw -Encoding UTF8 $configPath

try {
    $config = $originalConfig | ConvertFrom-Json
    $config.targetUrl = $TargetUrl
    $config | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $configPath

    Push-Location $desktopDir
    try {
        if (-not $SkipInstall -or -not (Test-Path (Join-Path $desktopDir "node_modules"))) {
            npm install
        }

        $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
        npm run build:win:dir
    }
    finally {
        Pop-Location
        Remove-Item Env:CSC_IDENTITY_AUTO_DISCOVERY -ErrorAction SilentlyContinue
    }

    $unpackedDir = Join-Path $desktopDir "dist\win-unpacked"
    if (-not (Test-Path $unpackedDir)) {
        throw "A pasta win-unpacked nao foi gerada em desktop/windows-shell/dist."
    }

    $artifactPath = Join-Path $desktopDir "dist\CriaVideo-Desktop-Windows-0.1.0.zip"
    if (Test-Path $artifactPath) {
        Remove-Item -Path $artifactPath -Force
    }

    Compress-Archive -Path (Join-Path $unpackedDir "*") -DestinationPath $artifactPath -CompressionLevel Optimal
    $artifact = Get-Item $artifactPath

    if (-not $SkipCopyToStatic) {
        New-Item -ItemType Directory -Force -Path $downloadsDir | Out-Null
        Copy-Item -Path $artifact.FullName -Destination (Join-Path $downloadsDir "criavideo-desktop-windows-latest.zip") -Force
    }

    Write-Output ("Desktop Windows gerado: " + $artifact.FullName)
    if (-not $SkipCopyToStatic) {
        Write-Output ("Copia local atualizada: " + (Join-Path $downloadsDir "criavideo-desktop-windows-latest.zip"))
    }
}
finally {
    Set-Content -Encoding UTF8 $configPath $originalConfig
}
