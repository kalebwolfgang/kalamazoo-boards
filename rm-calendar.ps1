$dir = 'C:\Users\kaleb\OneDrive\Desktop\CoK Boards and Commissions\Code'
$enc = New-Object System.Text.UTF8Encoding $False
$files = Get-ChildItem "$dir\*.html" | Where-Object { $_.Name -ne 'boards.html' }
foreach ($f in $files) {
    $c = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
    $c = $c -replace '\r?\n\s+<a class="nav-link" href="#">Calendar</a>', ''
    [System.IO.File]::WriteAllText($f.FullName, $c, $enc)
    Write-Output "Updated: $($f.Name)"
}
Write-Output 'Done.'
