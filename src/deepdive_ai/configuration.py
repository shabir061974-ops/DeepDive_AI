"""Private, bounded credential loading; no environment mutation or value logging."""

import base64
import os
import stat
import subprocess
from pathlib import Path
from typing import Mapping


PRIVATE_ENV_PATH = Path(r"D:\Developments\DeepDive_AI.env")
PROVIDER_KEYS = frozenset({"MESH_API_KEY", "BRAVE_SEARCH_API_KEY"})


class ConfigurationError(ValueError):
    """Fixed safe message only; never include contents or upstream errors."""


def _check_permissions(path: Path) -> None:
    if os.name == "nt":
        # Metadata only: never read contents or echo accounts, rules or errors.
        script = r"""& { param([string]$Target)
try {
  $acl = Get-Acl -LiteralPath $Target -ErrorAction Stop
  $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @($current, 'S-1-5-18', 'S-1-5-32-544')
  if (-not $acl.AreAccessRulesProtected) { exit 1 }
  $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
  if ($owner -notin $allowed) { exit 1 }
  foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -eq 'Allow') {
      $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
      if ($sid -notin $allowed) { exit 1 }
    }
  }
  exit 0
} catch { exit 1 }
} """
        encoded = base64.b64encode((script + "'" + str(path).replace("'", "''") + "'").encode("utf-16-le")).decode("ascii")
        command = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                   "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                   "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=5, check=False).returncode != 0:
            raise ConfigurationError("Private credential file permissions are unsafe")
    elif path.stat().st_mode & 0o077:
        raise ConfigurationError("Private credential file permissions are unsafe")


def _read_private_values() -> dict[str, str]:
    try:
        path = PRIVATE_ENV_PATH
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or path.is_symlink()
                or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ConfigurationError("Private credential file must be a regular file")
        _check_permissions(path)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ConfigurationError("Private credential file changed during loading")
            data = stream.read(16_385)
        if len(data) > 16_384:
            raise ConfigurationError("Private credential file exceeds its size limit")
        values = {}
        for line in data.decode("utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, separator, value = line.partition("=")
            name = name.strip()
            if not separator or name not in PROVIDER_KEYS or name in values:
                raise ConfigurationError("Private credential file format is invalid")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if not 1 <= len(value) <= 4096 or any(not 33 <= ord(char) <= 126 for char in value):
                raise ConfigurationError("Private credential file format is invalid")
            values[name] = value
        return values
    except FileNotFoundError:
        return {}
    except ConfigurationError:
        raise
    except Exception:
        raise ConfigurationError("Private credential configuration could not be loaded safely") from None


def provider_key(name: str, environment: Mapping[str, str] | None = None) -> str:
    if name not in PROVIDER_KEYS:
        raise ConfigurationError("Unsupported credential setting")
    if environment is not None:
        return environment.get(name, "")
    # Presence wins, including an empty value that explicitly disables a key.
    if name in os.environ:
        return os.environ[name]
    return _read_private_values().get(name, "")
