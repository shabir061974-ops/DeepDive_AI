"""Private configuration tests use synthetic files only, never the operator's file."""

import base64
import fnmatch
import io
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from deepdive_ai import configuration
from deepdive_ai.configuration import ConfigurationError, provider_key
from deepdive_ai.connectivity import TestOptions, run_connectivity
from deepdive_ai.live import run_live
from deepdive_ai.mesh import MeshSettings
from deepdive_ai.search import BraveSearchSettings
from deepdive_ai.source_connectivity import run_source_diagnostic
from test_live import OPTIONS


MESH = "offline-mesh-config-fixture"
BRAVE = "offline-brave-config-fixture"


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.env"
        for guard in (patch.object(configuration, "PRIVATE_ENV_PATH", self.path),
                      patch.dict("os.environ", {}, clear=True),
                      patch.object(configuration, "_check_permissions")):
            guard.start()
            self.addCleanup(guard.stop)

    def write(self, data=None):
        self.path.write_text(data if data is not None else f"MESH_API_KEY={MESH}\nBRAVE_SEARCH_API_KEY={BRAVE}\n",
                             encoding="utf-8")

    def test_both_settings_load_private_values_without_mutating_environment_or_repr(self):
        self.write()
        mesh, brave = MeshSettings.from_environment(), BraveSearchSettings.from_environment()
        self.assertEqual(mesh.api_key, MESH)
        self.assertEqual(brave.api_key, BRAVE)
        self.assertNotIn("MESH_API_KEY", os.environ)
        self.assertNotIn("BRAVE_SEARCH_API_KEY", os.environ)
        self.assertNotIn(MESH, repr(mesh))
        self.assertNotIn(BRAVE, repr(brave))

    def test_process_presence_including_empty_value_takes_precedence_without_file_reads(self):
        with patch.dict("os.environ", {"MESH_API_KEY": MESH, "BRAVE_SEARCH_API_KEY": ""}), \
                patch.object(configuration, "_read_private_values", side_effect=AssertionError("No file reads")):
            self.assertEqual(MeshSettings.from_environment().api_key, MESH)
            with self.assertRaises(ValueError):
                BraveSearchSettings.from_environment()

    def test_explicit_mapping_never_falls_back_to_process_or_file(self):
        self.write()
        with patch.dict("os.environ", {"MESH_API_KEY": MESH}), \
                patch.object(configuration, "_read_private_values", side_effect=AssertionError("No file reads")):
            with self.assertRaises(ValueError):
                MeshSettings.from_environment({})
            self.assertEqual(provider_key("BRAVE_SEARCH_API_KEY", {"BRAVE_SEARCH_API_KEY": BRAVE}), BRAVE)

    def test_missing_file_or_missing_requested_key_fails_without_secret_output(self):
        with self.assertRaises(ValueError):
            MeshSettings.from_environment()
        self.write(f"BRAVE_SEARCH_API_KEY={BRAVE}\n")
        with self.assertRaises(ValueError) as error:
            MeshSettings.from_environment()
        self.assertNotIn(BRAVE, str(error.exception))

    def test_comments_bom_quotes_and_equals_are_literal_without_interpolation(self):
        self.write("\ufeff# comment\n\nMESH_API_KEY='literal${NAME}=value'\nBRAVE_SEARCH_API_KEY=\"literal#value\"\n")
        self.assertEqual(provider_key("MESH_API_KEY"), "literal${NAME}=value")
        self.assertEqual(provider_key("BRAVE_SEARCH_API_KEY"), "literal#value")

    def test_invalid_duplicate_and_oversized_files_fail_safely(self):
        for data in (f"UNKNOWN={MESH}", f"MESH_API_KEY={MESH}\nMESH_API_KEY={MESH}",
                     f"MESH_API_KEY={MESH} bad", "MESH_API_KEY=", "MESH_API_KEY=" + "x" * 4097,
                     "x" * 16_385, "not-an-assignment"):
            self.write(data)
            with self.subTest(size=len(data)), redirect_stdout(io.StringIO()) as output, \
                    self.assertRaises(ConfigurationError) as error:
                provider_key("MESH_API_KEY")
            self.assertNotIn(MESH, str(error.exception))
            self.assertEqual(output.getvalue(), "")

    def test_permission_or_decode_failures_are_sanitized_and_do_not_change_file(self):
        self.write()
        original = self.path.read_bytes()
        with patch.object(configuration, "_check_permissions", side_effect=PermissionError(MESH)), \
                self.assertRaises(ConfigurationError) as error:
            provider_key("MESH_API_KEY")
        self.assertNotIn(MESH, str(error.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.path.write_bytes(b"\xff")
        with self.assertRaises(ConfigurationError):
            provider_key("MESH_API_KEY")

    def test_directory_is_rejected_and_only_allowlisted_names_are_supported(self):
        self.path.mkdir()
        with self.assertRaises(ConfigurationError):
            provider_key("MESH_API_KEY")
        with self.assertRaises(ConfigurationError):
            provider_key("UNSUPPORTED")


class ConfigurationEntryPointTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflights_share_loader_and_remain_offline(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(configuration, "_read_private_values", return_value={"MESH_API_KEY": MESH, "BRAVE_SEARCH_API_KEY": BRAVE}), \
                patch("deepdive_ai.live.PublicHTTPTransport") as live_transport, \
                patch("deepdive_ai.connectivity.PublicHTTPTransport") as connectivity_transport:
            live = await run_live(OPTIONS)
            connectivity = await run_connectivity(OPTIONS.approvals)
        self.assertTrue(live["ready"])
        self.assertTrue(connectivity["ready"])
        live_transport.assert_not_called()
        connectivity_transport.assert_not_called()
        for secret in (MESH, BRAVE):
            self.assertNotIn(secret, repr(live) + repr(connectivity))

    async def test_source_preflight_never_accesses_credentials(self):
        with patch.object(configuration, "_read_private_values", side_effect=AssertionError("No credential files")), \
                patch("deepdive_ai.source_connectivity.PublicHTTPTransport") as transport:
            result = await run_source_diagnostic()
        transport.assert_not_called()
        self.assertEqual(result["attempts"], 0)

    async def test_unsafe_file_failure_is_redacted_in_preflight(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(configuration, "_read_private_values", side_effect=ConfigurationError("Private configuration invalid")):
            result = await run_connectivity(TestOptions(brave_only=True))
        self.assertFalse(result["ready"])
        self.assertIn("brave_credential_missing_or_invalid", result["blockers"])


class PublicationConfigurationTests(unittest.TestCase):
    def test_git_ignores_private_file_variants_without_reading_them(self):
        names = [".env", ".env.local", "DeepDive_AI.env", "backup.env.copy", "nested/provider.env", ".secrets/key"]
        result = subprocess.run(["git", "check-ignore", "-z", "--stdin"], input=("\0".join(names) + "\0").encode(),
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(set(result.stdout.decode().strip("\0").split("\0")), set(names))

    def test_package_manifest_excludes_private_variants(self):
        words = Path("MANIFEST.in").read_text().split()
        self.assertEqual(words[0], "global-exclude")
        for name in (".env", "nested/provider.env", "backup.env.copy", "private.key"):
            self.assertTrue(any(fnmatch.fnmatch(Path(name).name, pattern) for pattern in words[1:]), name)
        self.assertFalse(any(fnmatch.fnmatch("README.md", pattern) for pattern in words[1:]))

    @unittest.skipUnless(os.name == "nt", "Windows-only protected ACL setup")
    def test_masked_setup_creates_protected_synthetic_file_and_refuses_overwrite(self):
        Path("artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="artifacts") as temporary:
            target = (Path(temporary) / "synthetic.env").resolve()
            script = Path("scripts/Initialize-PrivateCredentials.ps1").read_text()
            script = script.replace("D:\\Developments\\DeepDive_AI.env", str(target))
            mock_prompt = "function Read-Host { param($Prompt, [switch]$AsSecureString) $s=[Security.SecureString]::new(); foreach($c in 'offline-setup-fixture'.ToCharArray()) { $s.AppendChar($c) }; $s.MakeReadOnly(); return $s }; "
            encoded = base64.b64encode((mock_prompt + "& {\n" + script + "\n}").encode("utf-16-le")).decode("ascii")
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
            first = subprocess.run(command, capture_output=True, timeout=15, check=False)
            cause = re.search(rb'Private setup failed at ([a-z_]+) \(([A-Za-z]+)\), script line ([0-9]+)', first.stderr)
            safe_cause = cause.group(0).decode() if cause else "Synthetic setup failed; sanitized cause unavailable"
            self.assertEqual(first.returncode, 0, safe_cause)
            original = target.read_bytes()
            self.assertEqual(original.count(b"offline-setup-fixture"), 2)
            configuration._check_permissions(target)
            second = subprocess.run(command, capture_output=True, timeout=15, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(target.read_bytes(), original)
            self.assertNotIn(b"offline-setup-fixture", first.stdout + first.stderr + second.stdout + second.stderr)
            # Reject a newly broadened ACL before any synthetic file content is loaded.
            escaped = str(target).replace("'", "''")
            broaden = "$p='" + escaped + "'; $f=[IO.FileInfo]::new($p); $a=$f.GetAccessControl([Security.AccessControl.AccessControlSections]::Access); "
            broaden += "$r=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'); $a.AddAccessRule($r); $f.SetAccessControl($a)"
            broadened = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(broaden.encode("utf-16-le")).decode()], capture_output=True, timeout=15, check=False)
            self.assertEqual(broadened.returncode, 0)
            with self.assertRaises(ConfigurationError):
                configuration._check_permissions(target)
