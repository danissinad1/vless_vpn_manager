#!/usr/bin/env python3
import importlib.machinery
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGER = importlib.machinery.SourceFileLoader(
    "vless_manager_dependencies", str(ROOT / "src/vless-vpn-manager")
).load_module()


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class DependencyPreflightTests(unittest.TestCase):
    def test_uses_existing_repository_and_installs_required_packages(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["apt-cache", "madison", "sing-box"]:
                return FakeResult(0, "sing-box | 1.14.0 | repository\n")
            if command[:2] == ["dpkg-query", "-W"]:
                return FakeResult(0, "installed\n")
            return FakeResult()

        with tempfile.TemporaryDirectory() as tmp:
            MANAGER.ensure_required_packages(
                run_command=fake_run,
                downloader=lambda *_: self.fail("repository key must not be downloaded"),
                keyring=Path(tmp) / "sagernet.asc",
                source_file=Path(tmp) / "sagernet.sources",
            )

        self.assertEqual(calls.count(["apt-get", "update"]), 1)
        self.assertIn(["apt-get", "install", "-y", "ca-certificates", "curl", "unzip"], calls)
        self.assertIn(["apt-get", "install", "-y", "--reinstall", "sing-box"], calls)

    def test_adds_official_repository_when_sing_box_is_unavailable(self):
        calls = []
        cache_checks = 0

        def fake_run(command, **kwargs):
            nonlocal cache_checks
            calls.append(command)
            if command[:3] == ["apt-cache", "madison", "sing-box"]:
                cache_checks += 1
                output = "sing-box | 1.14.0 | repository\n" if cache_checks > 1 else ""
                return FakeResult(0, output)
            if command[:2] == ["dpkg-query", "-W"]:
                return FakeResult(0, "installed\n")
            return FakeResult()

        def fake_download(url, destination):
            self.assertEqual(url, "https://sing-box.app/gpg.key")
            Path(destination).write_text("test-key")

        with tempfile.TemporaryDirectory() as tmp:
            keyring = Path(tmp) / "keyrings/sagernet.asc"
            source_file = Path(tmp) / "sources/sagernet.sources"
            MANAGER.ensure_required_packages(
                run_command=fake_run,
                downloader=fake_download,
                keyring=keyring,
                source_file=source_file,
            )

            self.assertEqual(keyring.read_text(), "test-key")
            self.assertIn("URIs: https://deb.sagernet.org/", source_file.read_text())
            self.assertIn(f"Signed-By: {keyring}", source_file.read_text())
            self.assertEqual(keyring.stat().st_mode & 0o777, 0o644)

        self.assertEqual(calls.count(["apt-get", "update"]), 2)
        self.assertIn(["apt-get", "install", "-y", "--reinstall", "sing-box"], calls)

    def test_fails_clearly_when_package_remains_unavailable(self):
        def fake_run(command, **kwargs):
            if command[:3] == ["apt-cache", "madison", "sing-box"]:
                return FakeResult(0, "")
            return FakeResult()

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "sing-box недоступен"):
                MANAGER.ensure_required_packages(
                    run_command=fake_run,
                    downloader=lambda _url, path: Path(path).write_text("test-key"),
                    keyring=Path(tmp) / "sagernet.asc",
                    source_file=Path(tmp) / "sagernet.sources",
                )


if __name__ == "__main__":
    unittest.main()
