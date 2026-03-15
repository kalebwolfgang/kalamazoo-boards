$dir = 'C:\Users\kaleb\OneDrive\Desktop\CoK Boards and Commissions\Code'
$enc = New-Object System.Text.UTF8Encoding $False

# All HTML files in the project
$files = Get-ChildItem "$dir\*.html" | Where-Object { $_.Name -ne 'boards.html' }

foreach ($f in $files) {
    $c = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)

    # 1. Remove the entire topbar-links div block (multiline)
    $c = $c -replace '\r?\n\s+<div class="topbar-links">[\s\S]*?</div>', ''

    # 2. Remove the Minutes nav link (with its leading newline+indent)
    $c = $c -replace '\r?\n\s+<a class="nav-link" href="#">Minutes</a>', ''

    # 3. Fix "Meeting Calendar" -> "Calendar" (faq.html only, harmless on others)
    $c = $c.Replace('Meeting Calendar', 'Calendar')

    [System.IO.File]::WriteAllText($f.FullName, $c, $enc)
    Write-Output "Updated: $($f.Name)"
}
Write-Output 'Nav fix complete.'
