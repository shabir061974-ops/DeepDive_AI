# PowerShell 5.1. Creates only the specified private file; never reads existing keys.
$ErrorActionPreference = 'Stop'
$configPath = 'D:\Developments\DeepDive_AI.env'
if (Test-Path -LiteralPath $configPath) {
    throw 'Private configuration already exists; nothing was read or overwritten.'
}
$secureInputs = @()
$buffers = @()
$stream = $null
$writer = $null
try {
    foreach ($name in @('MESH_API_KEY', 'BRAVE_SEARCH_API_KEY')) {
        $secure = Read-Host ('Enter ' + $name + ' (masked)') -AsSecureString
        $secureInputs += $secure
        $pointer = [IntPtr]::Zero
        try {
            if ($secure.Length -lt 1 -or $secure.Length -gt 4096) {
                throw 'Invalid credential length; no values will be displayed.'
            }
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            $characters = New-Object char[] $secure.Length
            $buffers += ,$characters
            for ($index = 0; $index -lt $characters.Length; $index++) {
                $code = [Runtime.InteropServices.Marshal]::ReadInt16($pointer, 2 * $index)
                if ($code -lt 33 -or $code -gt 126) {
                    throw 'Invalid credential characters; no values will be displayed.'
                }
                $characters[$index] = [char]$code
            }
        } finally {
            if ($pointer -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
        }
    }
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($current)
    foreach ($sid in @($current, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
                                [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    # Apply the protected ACL atomically at exclusive file creation, before writing.
    $stream = [IO.FileStream]::new($configPath, [IO.FileMode]::CreateNew,
        [Security.AccessControl.FileSystemRights]::Write, [IO.FileShare]::None,
        4096, [IO.FileOptions]::None, $acl)
    $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
    $writer.Write('MESH_API_KEY=')
    $writer.Write($buffers[0])
    $writer.WriteLine()
    $writer.Write('BRAVE_SEARCH_API_KEY=')
    $writer.Write($buffers[1])
    $writer.WriteLine()
    $writer.Flush()
    $stream.Flush($true)
    Write-Output 'Private configuration created with restricted permissions. No keys displayed.'
} catch {
    throw 'Private setup failed. Existing files were not overwritten. Any partial file remains restricted; review locally without displaying its contents.'
} finally {
    if ($null -ne $writer) { $writer.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    foreach ($buffer in $buffers) { [Array]::Clear($buffer, 0, $buffer.Length) }
    foreach ($secure in $secureInputs) { $secure.Dispose() }
}
