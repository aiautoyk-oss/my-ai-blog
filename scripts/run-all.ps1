# ファイル: scripts/run-all.ps1

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "=== Step 1: 記事を生成中 ===" -ForegroundColor Cyan
python .\scripts\generate_article.py

Write-Host ""
Write-Host "=== Step 2: アイキャッチ画像を生成中 ===" -ForegroundColor Cyan
python .\scripts\generate_image.py

Write-Host ""
Write-Host "=== Step 3: アフィリエイトリンクを挿入中 ===" -ForegroundColor Cyan
python .\scripts\insert_affiliate_links.py

Write-Host ""
Write-Host "=== 完了！必要なら npm run dev で確認してください ===" -ForegroundColor Green
