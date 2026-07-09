$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$target = Join-Path $root "local_data\references"
New-Item -ItemType Directory -Force -Path $target | Out-Null

$files = @(
  "$env:USERPROFILE\Downloads\0102.pdf",
  "$env:USERPROFILE\Downloads\0103.pdf",
  "$env:USERPROFILE\Downloads\0215.pdf",
  "$env:USERPROFILE\Downloads\0315.pdf",
  "$env:USERPROFILE\Downloads\0414.pdf",
  "$env:USERPROFILE\Downloads\최종보고서_2022041006_김건우.pdf",
  "$env:USERPROFILE\Downloads\명세서 양식.docx",
  "$env:USERPROFILE\Downloads\명세서_양식_새_출원_준비_건.docx",
  "$env:USERPROFILE\Desktop\계절\2022041006_김건우_작성중_021915.doc"
)

foreach ($file in $files) {
  if (Test-Path -LiteralPath $file) {
    Copy-Item -LiteralPath $file -Destination $target -Force
    Write-Output "copied: $(Split-Path $file -Leaf)"
  } else {
    Write-Output "missing: $(Split-Path $file -Leaf)"
  }
}
