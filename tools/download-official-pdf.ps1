param(
  [Parameter(Mandatory = $true)]
  [string]$LawIdOrUniqueId,

  [ValidateSet("h1", "v1", "v2", "v4")]
  [string]$Layout = "h1",

  [string]$OutFile
)

$ErrorActionPreference = "Stop"

function Invoke-EgovJson {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url,

    [Parameter(Mandatory = $true)]
    [hashtable]$Body
  )

  $json = $Body | ConvertTo-Json -Compress
  Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $json
}

function Resolve-LawTarget {
  param(
    [Parameter(Mandatory = $true)]
    [string]$LawIdOrUniqueId
  )

  if ($LawIdOrUniqueId -match '^(?<law_id>[A-Za-z0-9]+)_(?<date>\d{8})_(?<amendment>[A-Za-z0-9]+)$') {
    return [pscustomobject]@{
      LawId = $Matches["law_id"].ToUpperInvariant()
      LawUniqueId = $LawIdOrUniqueId.ToUpperInvariant()
      EnforcementDate = "{0}-{1}-{2}" -f $Matches["date"].Substring(0, 4), $Matches["date"].Substring(4, 2), $Matches["date"].Substring(6, 2)
      AmendmentId = $Matches["amendment"].ToUpperInvariant()
    }
  }

  [pscustomobject]@{
    LawId = $LawIdOrUniqueId.ToUpperInvariant()
    LawUniqueId = $null
    EnforcementDate = $null
    AmendmentId = $null
  }
}

$target = Resolve-LawTarget -LawIdOrUniqueId $LawIdOrUniqueId
$revisionResponse = Invoke-EgovJson `
  -Url "https://laws.e-gov.go.jp/internal-api/SelectLawRevisionData.json" `
  -Body @{ law_id = $target.LawId }

$history = @($revisionResponse.result.Amendment_History)
if (-not $history.Count) {
  throw "改正履歴が取得できませんでした: $($target.LawId)"
}

if ($target.LawUniqueId) {
  $entry = $history | Where-Object {
    $_.EnforcementDate -eq $target.EnforcementDate -and $_.AmendmentId.ToUpperInvariant() -eq $target.AmendmentId
  } | Select-Object -First 1
  if (-not $entry) {
    throw "指定した law_unique_id に一致する改正履歴が見つかりませんでした: $($target.LawUniqueId)"
  }
  $lawUniqueId = $target.LawUniqueId
} else {
  $entry = $history | Where-Object { $_.IsCurrentEnforcement -eq $true } | Select-Object -First 1
  if (-not $entry) {
    $entry = $history | Select-Object -First 1
  }
  $lawUniqueId = "{0}_{1}_{2}" -f $target.LawId, ($entry.EnforcementDate -replace "-", ""), $entry.AmendmentId
}

$downloadPathResponse = Invoke-EgovJson `
  -Url "https://laws.e-gov.go.jp/internal-api/GetDownloadFilePath.json" `
  -Body @{
    law_unique_id = $lawUniqueId
    law_data_id = [int]$entry.LawDataId
    subRevision = [string]$entry.SubRevision
  }

$layoutKeyMap = @{
  h1 = "PDF_H1"
  v1 = "PDF_V1"
  v2 = "PDF_V2"
  v4 = "PDF_V4"
}

$layoutKey = $layoutKeyMap[$Layout]
$relativePath = $downloadPathResponse.result.Download_Infos.$layoutKey
if (-not $relativePath) {
  throw "公式PDFのパスが取得できませんでした: $layoutKey"
}

$downloadUrl = "https://laws.e-gov.go.jp$relativePath"

if (-not $OutFile) {
  $downloadsDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\\downloads"))
  if (-not (Test-Path -LiteralPath $downloadsDir)) {
    New-Item -ItemType Directory -Path $downloadsDir | Out-Null
  }
  $OutFile = Join-Path $downloadsDir ([System.IO.Path]::GetFileName($relativePath))
}

Invoke-WebRequest -Uri $downloadUrl -OutFile $OutFile

Write-Output ("Saved official PDF: {0}" -f ([System.IO.Path]::GetFullPath($OutFile)))
Write-Output ("Source URL: {0}" -f $downloadUrl)
