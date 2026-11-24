# Download dependencies on the build machine; the employee installer is offline.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$vendor = Join-Path (Split-Path $PSScriptRoot -Parent) 'build/vendor'
New-Item -ItemType Directory -Force $vendor | Out-Null
$manifest = @()
function Get-Dependency($Url, $Path, $ExpectedHash = '') {
    Invoke-WebRequest -UseBasicParsing $Url -OutFile $Path
    $hash = (Get-FileHash $Path -Algorithm SHA256).Hash.ToLower()
    if ($ExpectedHash -and $hash -ne $ExpectedHash) { throw "Checksum mismatch: $Path" }
    $script:manifest += @{ url = $Url; sha256 = $hash }
}
$browserVersion = '158.0.8127.36'
$browserZip = Join-Path $vendor 'chrome.zip'
Get-Dependency "https://storage.googleapis.com/chrome-for-testing-public/$browserVersion/win64/chrome-win64.zip" $browserZip
Expand-Archive $browserZip $vendor -Force
Remove-Item $browserZip

$ocrSetup = Join-Path $vendor 'ocr-setup.exe'
Get-Dependency 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe' $ocrSetup 'bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'
$ocr = Join-Path $vendor 'tesseract'
$process = Start-Process $ocrSetup -ArgumentList "/S /D=$ocr" -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'OCR installation failed on build machine' }
Remove-Item $ocrSetup
foreach ($language in @('eng', 'chi_sim')) {
    Get-Dependency "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/$language.traineddata" (Join-Path $ocr "tessdata/$language.traineddata")
}
Get-Dependency 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/LICENSE' (Join-Path $ocr 'tessdata/LICENSE')
Get-Dependency 'https://raw.githubusercontent.com/tesseract-ocr/tesseract/5.5.3/LICENSE' (Join-Path $ocr 'LICENSE')
if (-not (Test-Path "$vendor/chrome-win64/chrome.exe") -or -not (Test-Path "$ocr/tesseract.exe")) {
    throw 'Bundled browser or OCR engine is missing'
}
$manifest | ConvertTo-Json | Set-Content -Encoding UTF8 "$vendor/dependencies.json"
