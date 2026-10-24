$ErrorActionPreference = 'Stop'
try {
    & (Join-Path $PSScriptRoot 'Start-Ayven-Milo.ps1')
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Ayven could not start') | Out-Null
    exit 1
}
