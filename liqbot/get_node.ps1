$ProgressPreference = 'SilentlyContinue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip = Join-Path $here 'node.zip'; $tmp = Join-Path $here '.nodejstmp'; $dst = Join-Path $here '.nodejs'
Invoke-WebRequest 'https://nodejs.org/dist/v22.20.0/node-v22.20.0-win-x64.zip' -OutFile $zip
Expand-Archive $zip -DestinationPath $tmp -Force
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Move-Item (Join-Path $tmp 'node-v22.20.0-win-x64') $dst
Remove-Item $zip; Remove-Item $tmp -Recurse
Write-Output "node ok"
