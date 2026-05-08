param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$CompareProduction,
    [switch]$CompareStaging,
    [string]$SshTarget = "root@criavideo.pro",
    [string]$RemoteRoot = "/opt/levita-video",
    [string]$ContainerName = "levita-video",
    [string]$PublicUrl = "https://criavideo.pro/video",
    [string]$StagingRemoteRoot = "/opt/levita-video-staging",
    [string]$StagingContainerName = "levita-video-staging",
    [string]$StagingPublicUrl = "https://staging.criavideo.pro/video"
)

$ErrorActionPreference = "Stop"

$bundleFiles = @(
    "static/app.js",
    "static/index.html",
    "static/style.css",
    "static/pwa.js",
    "static/sw.js"
)

function Fail([string]$Message) {
    throw $Message
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        Fail $Message
    }
}

function Read-RawFile([string]$Path) {
    return [System.IO.File]::ReadAllText($Path)
}

function Get-SingleMatchValue([string]$Text, [string]$Pattern, [string]$Label) {
    $match = [regex]::Match($Text, $Pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) {
        Fail "Nao foi possivel localizar $Label."
    }
    return $match.Groups["value"].Value
}

function Get-LocalBundleMetadata([string]$RootPath) {
    $appJs = Read-RawFile (Join-Path $RootPath "static/app.js")
    $indexHtml = Read-RawFile (Join-Path $RootPath "static/index.html")
    $pwaJs = Read-RawFile (Join-Path $RootPath "static/pwa.js")
    $swJs = Read-RawFile (Join-Path $RootPath "static/sw.js")

    $appVersion = Get-SingleMatchValue $appJs 'app\.js v(?<value>\d+) loaded' 'o marcador de versao do app.js'
    $requiredVersion = Get-SingleMatchValue $indexHtml 'REQUIRED_VER\s*=\s*(?<value>\d+)' 'o REQUIRED_VER do index.html'
    $styleQuery = Get-SingleMatchValue $indexHtml '/video/static/style\.css\?v=(?<value>[0-9\-]+)' 'a query string do style.css no index.html'
    $appQuery = Get-SingleMatchValue $indexHtml '/video/static/app\.js\?v=(?<value>[0-9\-]+)' 'a query string do app.js no index.html'
    $pwaQuery = Get-SingleMatchValue $indexHtml '/video/static/pwa\.js\?v=(?<value>[0-9\-]+)' 'a query string do pwa.js no index.html'
    $swQuery = Get-SingleMatchValue $pwaJs '/video/static/sw\.js\?v=(?<value>[0-9\-]+)' 'a query string do sw.js no pwa.js'
    $cacheName = Get-SingleMatchValue $swJs 'CACHE_NAME\s*=\s*"(?<value>[^"]+)"' 'o CACHE_NAME do service worker'
    $swStyleQuery = Get-SingleMatchValue $swJs '/video/static/style\.css\?v=(?<value>[0-9\-]+)' 'a query string do style.css no sw.js'
    $swAppQuery = Get-SingleMatchValue $swJs '/video/static/app\.js\?v=(?<value>[0-9\-]+)' 'a query string do app.js no sw.js'
    $swPwaQuery = Get-SingleMatchValue $swJs '/video/static/pwa\.js\?v=(?<value>[0-9\-]+)' 'a query string do pwa.js no sw.js'

    Assert-True ($appVersion -eq $requiredVersion) "app.js e index.html estao com versoes divergentes ($appVersion vs $requiredVersion)."
    Assert-True ($styleQuery -eq $appQuery -and $styleQuery -eq $pwaQuery) "index.html nao esta com as query strings do bundle sincronizadas."
    Assert-True ($styleQuery -eq $swQuery) "pwa.js aponta para uma versao de sw.js diferente da query do bundle atual."
    Assert-True ($styleQuery -eq $swStyleQuery -and $styleQuery -eq $swAppQuery -and $styleQuery -eq $swPwaQuery) "sw.js nao esta com os assets cacheados sincronizados com o index.html."

    return [pscustomobject]@{
        AppVersion = $appVersion
        RequiredVersion = $requiredVersion
        AssetQuery = $styleQuery
        SwQuery = $swQuery
        CacheName = $cacheName
    }
}

function Get-LocalHashes([string]$RootPath) {
    $hashes = @{}
    foreach ($file in $bundleFiles) {
        $hashes[$file] = (Get-FileHash (Join-Path $RootPath $file) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $hashes
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-LocalNormalizedTextHashes([string]$RootPath) {
    $hashes = @{}
    foreach ($file in $bundleFiles) {
        $text = Read-RawFile (Join-Path $RootPath $file)
        $normalized = $text -replace "`r`n", "`n" -replace "`r", "`n"
        $hashes[$file] = Get-Sha256Hex ([System.Text.Encoding]::UTF8.GetBytes($normalized))
    }
    return $hashes
}

function Get-RemoteHashes([string]$Target, [string]$Command, [string]$Label) {
    $output = & ssh $Target $Command
    if ($LASTEXITCODE -ne 0) {
        Fail "Falha ao obter hashes remotos de $Label."
    }

    $hashes = @{}
    foreach ($line in $output) {
        $trimmed = [string]$line
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }

        $match = [regex]::Match($trimmed, '^(?<hash>[a-f0-9]{64})\s*(?<path>.+)$')
        if (-not $match.Success) {
            Fail "Saida inesperada ao ler hashes de ${Label}: $trimmed"
        }

        $leaf = [System.IO.Path]::GetFileName($match.Groups["path"].Value)
        switch ($leaf) {
            "app.js" { $hashes["static/app.js"] = $match.Groups["hash"].Value.ToLowerInvariant() }
            "index.html" { $hashes["static/index.html"] = $match.Groups["hash"].Value.ToLowerInvariant() }
            "style.css" { $hashes["static/style.css"] = $match.Groups["hash"].Value.ToLowerInvariant() }
            "pwa.js" { $hashes["static/pwa.js"] = $match.Groups["hash"].Value.ToLowerInvariant() }
            "sw.js" { $hashes["static/sw.js"] = $match.Groups["hash"].Value.ToLowerInvariant() }
            default { Fail "Arquivo inesperado no hash remoto de ${Label}: $leaf" }
        }
    }

    foreach ($file in $bundleFiles) {
        if (-not $hashes.ContainsKey($file)) {
            Fail "Hash ausente para $file em $Label."
        }
    }

    return $hashes
}

function Assert-HashSetsEqual([hashtable]$Expected, [hashtable]$Actual, [string]$Label) {
    foreach ($file in $bundleFiles) {
        if ($Expected[$file] -ne $Actual[$file]) {
            Fail "${Label} divergiu em $file.`nLocal: $($Expected[$file])`n${Label}: $($Actual[$file])"
        }
    }
}

function Get-PublicBundleMetadata([string]$Url) {
    $response = Invoke-WebRequest -UseBasicParsing $Url
    $html = [string]$response.Content

    return [pscustomobject]@{
        RequiredVersion = Get-SingleMatchValue $html 'REQUIRED_VER\s*=\s*(?<value>\d+)' 'o REQUIRED_VER da pagina publica'
        StyleQuery = Get-SingleMatchValue $html 'style\.css\?v=(?<value>[0-9\-]+)' 'a query string publica do style.css'
        AppQuery = Get-SingleMatchValue $html 'app\.js\?v=(?<value>[0-9\-]+)' 'a query string publica do app.js'
        PwaQuery = Get-SingleMatchValue $html 'pwa\.js\?v=(?<value>[0-9\-]+)' 'a query string publica do pwa.js'
    }
}

$localMetadata = Get-LocalBundleMetadata $RepoRoot
$localHashes = Get-LocalHashes $RepoRoot

Write-Output "Frontend local sincronizado:"
Write-Output ("- app.js v{0}" -f $localMetadata.AppVersion)
Write-Output ("- REQUIRED_VER {0}" -f $localMetadata.RequiredVersion)
Write-Output ("- Query do bundle {0}" -f $localMetadata.AssetQuery)
Write-Output ("- Service worker {0} ({1})" -f $localMetadata.SwQuery, $localMetadata.CacheName)

if (-not $CompareProduction) {
    if (-not $CompareStaging) {
        exit 0
    }
}

if ($CompareProduction -and $CompareStaging) {
    Fail "Use apenas um modo por vez: -CompareProduction ou -CompareStaging."
}

if ($CompareStaging) {
    $RemoteRoot = $StagingRemoteRoot
    $ContainerName = $StagingContainerName
    $PublicUrl = $StagingPublicUrl
}

$hostCommand = "cd $RemoteRoot && sha256sum static/app.js static/index.html static/style.css static/pwa.js static/sw.js"
$containerCommand = "docker exec $ContainerName sha256sum /app/static/app.js /app/static/index.html /app/static/style.css /app/static/pwa.js /app/static/sw.js"

if ($CompareStaging) {
    $localHashes = Get-LocalNormalizedTextHashes $RepoRoot
    $hostCommand = 'cd {0} && for f in static/app.js static/index.html static/style.css static/pwa.js static/sw.js; do h=$(tr -d ''\r'' < "$f" | sha256sum | cut -d'' '' -f1); echo "$h  $f"; done' -f $RemoteRoot
    $containerCommand = 'for f in /app/static/app.js /app/static/index.html /app/static/style.css /app/static/pwa.js /app/static/sw.js; do h=$(docker exec {0} cat "$f" | tr -d ''\r'' | sha256sum | cut -d'' '' -f1); echo "$h  $f"; done' -f $ContainerName
}

$hostHashes = Get-RemoteHashes $SshTarget $hostCommand "o host do VPS"
$containerHashes = Get-RemoteHashes $SshTarget $containerCommand "o container em execucao"

Assert-HashSetsEqual $localHashes $hostHashes "O host do VPS"
Assert-HashSetsEqual $localHashes $containerHashes "O container em execucao"

$publicMetadata = Get-PublicBundleMetadata $PublicUrl
Assert-True ($publicMetadata.RequiredVersion -eq $localMetadata.RequiredVersion) "A pagina publica nao esta servindo o mesmo REQUIRED_VER do workspace local."
Assert-True ($publicMetadata.StyleQuery -eq $localMetadata.AssetQuery) "A pagina publica nao esta servindo a mesma query do style.css do workspace local."
Assert-True ($publicMetadata.AppQuery -eq $localMetadata.AssetQuery) "A pagina publica nao esta servindo a mesma query do app.js do workspace local."
Assert-True ($publicMetadata.PwaQuery -eq $localMetadata.AssetQuery) "A pagina publica nao esta servindo a mesma query do pwa.js do workspace local."

$environmentLabel = if ($CompareStaging) { "Staging" } else { "Producao" }
Write-Output ("{0} alinhado com o workspace local:" -f $environmentLabel)
Write-Output "- Host do VPS com hashes identicos"
Write-Output "- Container ativo com hashes identicos"
Write-Output ("- HTML publico servindo REQUIRED_VER {0} e query {1}" -f $publicMetadata.RequiredVersion, $publicMetadata.StyleQuery)