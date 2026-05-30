param(
    [string]$TargetUrl = "https://criavideo.pro/video",
    [ValidateSet("remote", "local-proxy")]
    [string]$RuntimeMode = "remote",
    [string]$ApiTargetUrl = "",
    [switch]$SkipInstall,
    [switch]$SkipCopyToStatic
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktopDir = Resolve-Path (Join-Path $repoRoot "desktop\windows-shell")
$configPath = Join-Path $desktopDir "desktop-config.json"
$downloadsDir = Join-Path $repoRoot "static\downloads"

$originalConfig = Get-Content -Raw -Encoding UTF8 $configPath

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

try {
    $config = $originalConfig | ConvertFrom-Json
    $config.targetUrl = $TargetUrl
    if (-not $config.runtime) {
        $config | Add-Member -MemberType NoteProperty -Name runtime -Value ([pscustomobject]@{})
    }
    $config.runtime.mode = $RuntimeMode
    if ($ApiTargetUrl) {
        $config.runtime.apiTargetUrl = $ApiTargetUrl
    }
    Write-Utf8NoBom -Path $configPath -Content ($config | ConvertTo-Json -Depth 10)

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
    Write-Utf8NoBom -Path $configPath -Content $originalConfig
}
