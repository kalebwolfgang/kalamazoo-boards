$dir = 'C:\Users\kaleb\OneDrive\Desktop\CoK Boards and Commissions\Code'
$enc = New-Object System.Text.UTF8Encoding $False
$files = Get-ChildItem "$dir\*.html" | Where-Object { $_.Name -ne 'boards.html' }
foreach ($f in $files) {
    $c = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
    $c = $c -replace '\r?\n\s+<div class="topbar-links">[\s\S]*?</div>', ''
    $c = $c -replace '\r?\n\s+<a class="nav-link" href="#">Minutes</a>', ''
    $c = $c.Replace('Meeting Calendar', 'Calendar')
    if ($c -notmatch 'href="calendar\.html"') {
        $c = $c.Replace(
            '<a class="nav-link" href="index.html">All Boards</a>',
            '<a class="nav-link" href="calendar.html">Calendar</a>' + "`r`n            " + '<a class="nav-link" href="index.html">All Boards</a>'
        )
        $c = $c.Replace(
            '<a class="nav-secondary-link" href="index.html">All Boards</a>',
            '<a class="nav-secondary-link" href="calendar.html">Calendar</a>' + "`r`n        " + '<a class="nav-secondary-link" href="index.html">All Boards</a>'
        )
    }
    [System.IO.File]::WriteAllText($f.FullName, $c, $enc)
    Write-Output "Updated: $($f.Name)"
}
Write-Output 'Nav fix complete.'
