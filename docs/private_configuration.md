# Private persistent provider credentials

The only automatic file fallback is exactly
`D:\Developments\DeepDive_AI.env` (beside the repository, not inside it).
It is not `D:\Developments\DeepDive_AI\.env`. No parent-directory search,
working-directory discovery, interpolation or global Windows environment update
is performed. File contents are never evaluated as shell code. On Windows a
separate, bounded PowerShell process inspects ACL metadata only, as described
below. No new dependencies are required.

## One-time masked entry, Windows PowerShell 5.1

From a private interactive terminal, run:

```powershell
Set-Location -LiteralPath 'D:\Developments\DeepDive_AI'
& '.\scripts\Initialize-PrivateCredentials.ps1'
if (-not $?) { throw 'Private setup failed; stop without displaying the file.' }
```

Enter the Mesh key and Brave key only at their masked prompts. Never supply
keys as arguments, command literals, chat messages, transcript content or screenshots.
The script does not inspect process credentials or automatically copy them.
It refuses any existing path before prompting and uses exclusive `CreateNew`
again when creating the file, so concurrent creation also cannot overwrite it.

The protected file ACL is applied at creation, before writing. Inheritance is
disabled; the current account owns the file and only that account, SYSTEM and
the built-in Administrators group receive access. No parent directory or
project ACL is modified. The script clears its BSTR/character buffers and
disposes secure inputs; runtime and OS buffers can still contain plaintext.
This is ACL-protected plaintext storage, not encryption or a credential vault.
Protect local backups and administrator access accordingly.

If an existing file is present, it is left entirely untouched. The application
will reject an unsafe ACL; this update does not silently repair permissions or
overwrite existing credentials. Review an existing file privately under its
owner account rather than displaying it in terminal/log output. A failed new
write may leave a restricted partial file, which must be reviewed locally;
the script will not delete or overwrite it automatically on a later attempt.

## Resolution and validation

1. An explicitly supplied Python environment mapping is authoritative and never
   supplemented from the real process or disk (including an empty mapping).
2. Otherwise a present process variable wins, even an empty one. Empty values
   fail provider validation rather than unexpectedly reviving a stored key.
3. Only an absent process variable falls back to the fixed private file.
   Missing files/keys remain normal missing-credential failures.

The loader never mutates `os.environ`, writes files or logs values. Both Mesh
and Brave settings use it, so `deepdive_ai.live`, provider connectivity modes,
the documented Planner probe and directly constructed provider settings agree.
Source-only diagnostics and the fixture dashboard do not invoke it. Isolated
provider modes construct only their selected provider; the shared private file
contains both entries and is parsed as a bounded unit when fallback is needed.
This does not activate providers, authorize spending or change request limits.

Before reading contents, the loader rejects nonregular/symlink/reparse files,
checks protected Windows ACLs against the current account/SYSTEM/Administrators
and verifies that the opened file matches the inspected file identity. Windows
ACL checks use the installed Windows PowerShell executable, with a five-second
metadata-only subprocess timeout and no profile or interactive prompts. It uses
`.NET FileInfo.GetAccessControl` with access/owner sections to inspect metadata,
not `Get-Content` or provider API calls. Only its internal ACL-check result is
consumed; credential values are never printed. The one-time setup script sets
permissions, while this runtime check only verifies them. Unix
fallback, if used, rejects group/other permission bits. No ACL is modified by
the loader. An unreadable, oversized, invalid-UTF-8 or unsafe file fails closed
with a fixed sanitized error. Values never appear in diagnostic reports.

Supported file syntax: UTF-8 (optional BOM), blank lines, full-line comments,
and only the two `NAME=value` assignments. Optional matching single/double
quotes are removed; values otherwise remain literal. No `export`, escapes,
inline-comment stripping or variable expansion is supported. Duplicate/unknown
settings, invalid header characters, empty or >4,096-character values and files
over 16,384 bytes are rejected. Use the setup script rather than manual editing.

Git ignores `.env`, `.env.*`, `*.env`, `*.env.*`, private-key extensions and
private artifact directories. `MANIFEST.in` independently excludes credential
file patterns from source packages; package data stays explicitly scoped to
static assets and authored JSON fixtures. Ignore rules do not remove previously
tracked files and cannot recognize a secret deliberately placed in an arbitrary
source file; publication security checks remain required.

No real credential file was created, inspected, copied or altered during this
implementation. Tests use temporary synthetic files only. The actual setup is
an operator action; future CLI processes load it without restarting the existing
dashboard. Do not execute live providers as part of setup verification.

Historical configuration implementation verification: all 290 offline tests
passed, including 14 configuration tests. The latest documented baseline is in
the [README status table](../README.md#current-status); no private credential file
was inspected during the documentation consolidation.
The Windows PowerShell 5.1 setup was exercised only against synthetic temporary
credentials, validating protected ACLs, overwrite refusal and unsafe-ACL rejection.
Six browser scenarios, compilation, JavaScript/setup-script syntax, whitespace
and publication security checks passed. No existing dashboard was restarted.
