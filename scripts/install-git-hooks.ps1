# Install .githooks into .git/hooks (run after clone)
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root ".githooks"
$dst = Join-Path $root ".git\hooks"

if (-not (Test-Path $dst)) {
    Write-Error "Not a git repository: $dst"
    exit 1
}

Get-ChildItem $src -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $dst $_.Name) -Force
    Write-Host "Installed hook: $($_.Name)"
}

Write-Host "Git hooks installed. .env will be blocked on commit and push."
