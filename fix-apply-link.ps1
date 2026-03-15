$dir = 'C:\Users\kaleb\OneDrive\Desktop\CoK Boards and Commissions\Code'
$enc = New-Object System.Text.UTF8Encoding $False
$files = Get-ChildItem "$dir\*.html" | Where-Object { $_.Name -ne 'boards.html' }
$oldAttr = 'class="nav-apply" href="#"'
$newAttr = 'class="nav-apply" href="https://www.kalamazoocity.org/Government/Boards-Commissions/Apply-to-Join-a-Board-or-Commission" target="_blank"'
foreach ($f in $files) {
    $c = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
    if ($c.Contains($oldAttr)) {
        $c = $c.Replace($oldAttr, $newAttr)
        [System.IO.File]::WriteAllText($f.FullName, $c, $enc)
        Write-Output "Fixed: $($f.Name)"
    }
}
Write-Output 'Done.'
