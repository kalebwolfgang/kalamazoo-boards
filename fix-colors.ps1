$dir = 'C:\Users\kaleb\OneDrive\Desktop\CoK Boards and Commissions\Code'
$enc = New-Object System.Text.UTF8Encoding $False
$files = Get-ChildItem "$dir\*.html"

foreach ($f in $files) {
    $c = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
    $c = $c.Replace('--navy:       #0f2744', '--navy:       #0d4f63')
    $c = $c.Replace('--navy-mid:   #1a3a5c', '--navy-mid:   #1a7a97')
    $c = $c.Replace('--navy-light: #2c527e', '--navy-light: #2596be')
    [System.IO.File]::WriteAllText($f.FullName, $c, $enc)
    Write-Output "Updated: $($f.Name)"
}
Write-Output 'Color update complete.'
