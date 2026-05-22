# start_qdrant.ps1
# Starts the local Qdrant server using the bundled qdrant.exe binary.
# Data is persisted in .\qdrant_storage\
#
# Usage:
#   .\start_qdrant.ps1
#
# Stop with Ctrl+C

$StorageDir = Join-Path $PSScriptRoot "qdrant_storage"
New-Item -ItemType Directory -Path $StorageDir -Force | Out-Null

Write-Host "Starting Qdrant server at http://localhost:6333"
Write-Host "Storage: $StorageDir"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

$env:QDRANT__STORAGE__STORAGE_PATH = $StorageDir
& "$PSScriptRoot\qdrant.exe"
