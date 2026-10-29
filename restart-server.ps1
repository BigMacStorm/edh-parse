# Kill whatever is listening on the web UI port, then start the edhparse web server.
$port = 8765

Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Set-Location $PSScriptRoot
python -m edhparse web
