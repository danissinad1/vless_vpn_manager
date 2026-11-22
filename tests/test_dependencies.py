#!/usr/bin/env python3
import base64
import hashlib
import importlib.machinery
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MANAGER = importlib.machinery.SourceFileLoader(
    "vless_manager_dependencies", str(ROOT / "src/vless-vpn-manager")
).load_module()
CORE = importlib.machinery.SourceFileLoader(
    "vless_manager_core", str(ROOT / "src/update-remnawave-nodes")
).load_module()


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class InputTests(unittest.TestCase):
    def test_reads_from_controlling_terminal_when_script_arrives_on_stdin(self):
        piped_stdin = io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8")
        terminal = io.BytesIO(b"13\n")

        with patch.object(MANAGER.sys, "stdin", piped_stdin), patch(
            "builtins.open", return_value=terminal
        ) as open_terminal:
            self.assertEqual(MANAGER.safe_input("Выбор: "), "13")

        open_terminal.assert_called_once_with("/dev/tty", "rb", buffering=0)


class DashboardTests(unittest.TestCase):
    def test_service_status_values_start_in_same_column(self):
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            MANAGER, "SING_CONFIG", Path(tmp) / "missing-sing-box.json"
        ), patch.object(
            MANAGER, "RESULTS_FILE", Path(tmp) / "missing-results.json"
        ), patch.object(
            MANAGER, "source_lines", return_value=[]
        ), patch.object(
            MANAGER, "service_state", return_value=("inactive", "disabled")
        ), redirect_stdout(output):
            MANAGER.dashboard()

        lines = output.getvalue().splitlines()
        sing_box = next(line for line in lines if line.startswith("sing-box:"))
        xray = next(line for line in lines if line.startswith("xray:"))
        self.assertEqual(sing_box.index("●"), xray.index("●"))


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

        self.assertEqual(calls.count(["apt-get", "update"]), 0)
        self.assertNotIn(["apt-get", "install", "-y", "ca-certificates", "curl", "unzip"], calls)
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

        repository_updates = [c for c in calls if c[0] == "apt-get" and c[-1] == "update"]
        self.assertEqual(len(repository_updates), 1)
        self.assertIn(f"Dir::Etc::sourcelist={source_file}", repository_updates[0])
        self.assertIn(["apt-get", "install", "-y", "--reinstall", "sing-box"], calls)

    def test_installs_verified_github_deb_when_repository_remains_unavailable(self):
        payload = b"official-deb"
        digest = hashlib.sha256(payload).hexdigest()
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["apt-cache", "madison", "sing-box"]:
                return FakeResult(0, "")
            if command[:2] == ["dpkg-query", "-W"]:
                return FakeResult(0, "installed\n")
            return FakeResult()

        with tempfile.TemporaryDirectory() as tmp:
            def downloader(url, path):
                Path(path).write_bytes(payload if url.endswith(".deb") else b"test-key")

            MANAGER.ensure_required_packages(
                run_command=fake_run,
                downloader=downloader,
                keyring=Path(tmp) / "sagernet.asc",
                source_file=Path(tmp) / "sagernet.sources",
                architecture="amd64",
                release_loader=lambda: {
                    "tag_name": "v1.2.3",
                    "assets": [{
                        "name": "sing-box_1.2.3_linux_amd64.deb",
                        "browser_download_url": "https://github.test/sing-box.deb",
                        "digest": f"sha256:{digest}",
                    }],
                },
            )

        local_installs = [c for c in calls if c[:4] == ["apt-get", "install", "-y", "--reinstall"]]
        self.assertEqual(len(local_installs), 1)
        self.assertTrue(local_installs[0][-1].endswith(".deb"))

    def test_rejects_github_deb_with_wrong_digest(self):
        def fake_run(command, **kwargs):
            if command[:3] == ["apt-cache", "madison", "sing-box"]:
                return FakeResult(0, "")
            return FakeResult()

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "SHA256"):
                MANAGER.ensure_required_packages(
                    run_command=fake_run,
                    downloader=lambda url, path: Path(path).write_bytes(b"deb" if url.endswith(".deb") else b"key"),
                    keyring=Path(tmp) / "sagernet.asc",
                    source_file=Path(tmp) / "sagernet.sources",
                    architecture="amd64",
                    release_loader=lambda: {
                        "tag_name": "v1.2.3",
                        "assets": [{
                            "name": "sing-box_1.2.3_linux_amd64.deb",
                            "browser_download_url": "https://github.test/sing-box.deb",
                            "digest": "sha256:" + "0" * 64,
                        }],
                    },
                )


class SubscriptionTests(unittest.TestCase):
    def test_normalizes_url_annotation_copied_from_chat(self):
        annotated = "@url:`https://example.com/subscription-token`"
        self.assertEqual(
            MANAGER.normalize_pasted_link(annotated),
            "https://example.com/subscription-token",
        )

    def test_extracts_vless_links_from_base64_subscription(self):
        links = [
            "vless://first@example.com:443?security=tls&type=tcp#one",
            "vless://second@example.net:8443?security=reality&type=tcp&pbk=abc#two",
        ]
        encoded = base64.urlsafe_b64encode(("\n".join(links) + "\n").encode()).decode().rstrip("=")
        self.assertEqual(MANAGER.extract_subscription_links(encoded.encode()), links)

    def test_extracts_plain_subscription_and_removes_duplicates(self):
        link = "vless://first@example.com:443?security=tls&type=tcp#one"
        payload = f"# subscription\n{link}\n{link}\n".encode()
        self.assertEqual(MANAGER.extract_subscription_links(payload), [link])

    def test_extracts_separately_base64_encoded_subscription_lines(self):
        links = [
            f"vless://00000000-0000-0000-0000-{index:012d}@node{index}.example.com:443?security=tls&type=tcp#{index}"
            for index in range(1, 8)
        ]
        payload = "\n".join(
            base64.urlsafe_b64encode(link.encode()).decode().rstrip("=")
            for link in links
        ).encode()

        self.assertEqual(MANAGER.extract_subscription_links(payload), links)

    def test_extracts_line_wrapped_base64_subscription(self):
        links = [
            "vless://first@example.com:443?security=tls&type=tcp#one",
            "vless://second@example.net:8443?security=reality&type=tcp&pbk=abc#two",
        ]
        encoded = base64.urlsafe_b64encode(("\n".join(links) + "\n").encode()).decode().rstrip("=")
        payload = "\n".join(encoded[index:index + 76] for index in range(0, len(encoded), 76)).encode()

        self.assertEqual(MANAGER.extract_subscription_links(payload), links)

    def test_fetch_sends_stable_hwid_for_device_limited_subscriptions(self):
        link = "vless://first@example.com:443?security=tls&type=tcp#one"
        payload = base64.urlsafe_b64encode((link + "\n").encode())
        requests = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://example.com/subscription-token"

            def read(self, _size):
                return payload

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 30)
            requests.append(request)
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            with patch.object(MANAGER, "STATE_DIR", state_dir), patch.object(
                MANAGER.urllib.request, "urlopen", fake_urlopen
            ):
                self.assertEqual(MANAGER.fetch_subscription("https://example.com/subscription-token"), [link])
                self.assertEqual(MANAGER.fetch_subscription("https://example.com/subscription-token"), [link])

            hwids = [request.get_header("X-hwid") for request in requests]
            self.assertRegex(hwids[0], r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
            self.assertEqual(hwids, [hwids[0], hwids[0]])
            self.assertEqual((state_dir / "subscription-hwid").stat().st_mode & 0o777, 0o600)

    def test_skips_incompatible_vless_links_in_mixed_subscription(self):
        unsupported = "vless://plain@example.org:80?security=none&type=tcp#plain"
        supported = "vless://secure@example.com:443?security=tls&type=tcp#secure"
        payload = f"{unsupported}\n{supported}\n".encode()

        links, rejected = MANAGER.extract_subscription_links(payload, with_rejected=True)

        self.assertEqual(links, [supported])
        self.assertEqual(rejected, [(unsupported, "неподдерживаемая комбинация none/tcp")])


class ConnectivityTests(unittest.TestCase):
    def test_classifies_accessible_restricted_and_failed_services(self):
        statuses = {"chatgpt.com": "200", "gemini.google.com": "403", "youtube.com": "000"}

        def fake_run(command, **kwargs):
            url = command[-1]
            status = next(code for host, code in statuses.items() if host in url)
            return FakeResult(0 if status != "000" else 28, status, "timeout" if status == "000" else "")

        results = MANAGER.check_service_connectivity(run_command=fake_run, display=False)
        self.assertEqual([item["state"] for item in results], ["available", "restricted", "failed"])


class ServiceManagementTests(unittest.TestCase):
    def test_xray_uses_standard_paths_and_service_name(self):
        self.assertEqual(MANAGER.XRAY_CONFIG, Path("/etc/xray/config.json"))
        self.assertEqual(MANAGER.XRAY_UNIT, Path("/etc/systemd/system/xray.service"))
        self.assertEqual(MANAGER.XRAY_BIN, Path("/usr/local/bin/xray"))
        unit = MANAGER.xray_service_unit()
        self.assertIn("LoadCredential=config.json:/etc/xray/config.json", unit)
        self.assertIn("ExecStart=/usr/local/bin/xray run -c %d/config.json", unit)

    def test_service_control_creates_backup_runs_action_and_verifies_state(self):
        calls = []
        backups = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return FakeResult()

        state = MANAGER.control_service(
            "xray.service",
            "disable",
            run_command=fake_run,
            state_reader=lambda _service: ("active", "disabled"),
            backup=lambda: backups.append(True),
        )

        self.assertEqual(backups, [True])
        self.assertEqual(calls, [(["systemctl", "disable", "xray.service"], {"check": True})])
        self.assertEqual(state, ("active", "disabled"))

    def test_rejects_unknown_service_or_action(self):
        with self.assertRaisesRegex(ValueError, "служба"):
            MANAGER.control_service("ssh.service", "start")
        with self.assertRaisesRegex(ValueError, "действие"):
            MANAGER.control_service("xray.service", "mask")

    def test_migrates_legacy_xray_installation_and_preserves_service_state(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return FakeResult()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_config = root / "etc/xray-hermes"
            legacy_config.mkdir(parents=True)
            (legacy_config / "config.json").write_text("{}\n")
            legacy_bin = root / "usr/local/bin/xray-hermes"
            legacy_bin.parent.mkdir(parents=True)
            legacy_bin.write_text("binary")
            legacy_unit = root / "etc/systemd/system/xray-hermes.service"
            legacy_unit.parent.mkdir(parents=True)
            legacy_unit.write_text("legacy")
            new_config = root / "etc/xray/config.json"
            new_bin = root / "usr/local/bin/xray"
            new_unit = root / "etc/systemd/system/xray.service"

            with patch.multiple(
                MANAGER,
                LEGACY_XRAY_CONFIG_DIR=legacy_config,
                LEGACY_XRAY_BIN=legacy_bin,
                LEGACY_XRAY_UNIT=legacy_unit,
                XRAY_CONFIG=new_config,
                XRAY_BIN=new_bin,
                XRAY_UNIT=new_unit,
            ):
                migrated = MANAGER.migrate_legacy_xray(
                    run_command=fake_run,
                    state_reader=lambda _service: ("active", "enabled"),
                )

            self.assertTrue(migrated)
            self.assertEqual(new_config.read_text(), "{}\n")
            self.assertEqual(new_bin.read_text(), "binary")
            self.assertIn(f"ExecStart={new_bin}", new_unit.read_text())
            self.assertFalse(legacy_config.exists())
            self.assertFalse(legacy_bin.exists())
            self.assertFalse(legacy_unit.exists())
            self.assertIn(["systemctl", "stop", "xray-hermes.service"], calls)
            self.assertIn(["systemctl", "enable", "xray.service"], calls)
            self.assertIn(["systemctl", "restart", "xray.service"], calls)
    def test_migration_rejects_conflicting_old_and_new_paths(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_config = root / "etc/xray-hermes"
            current_config = root / "etc/xray"
            legacy_config.mkdir(parents=True)
            current_config.mkdir(parents=True)
            (legacy_config / "config.json").write_text("old")
            (current_config / "config.json").write_text("new")
            with patch.multiple(
                MANAGER,
                LEGACY_XRAY_CONFIG_DIR=legacy_config,
                LEGACY_XRAY_BIN=root / "usr/local/bin/xray-hermes",
                LEGACY_XRAY_UNIT=root / "etc/systemd/system/xray-hermes.service",
                XRAY_CONFIG=current_config / "config.json",
                XRAY_BIN=root / "usr/local/bin/xray",
                XRAY_UNIT=root / "etc/systemd/system/xray.service",
            ):
                with self.assertRaisesRegex(RuntimeError, "конфликтующие"):
                    MANAGER.migrate_legacy_xray(
                        run_command=lambda command, **_kwargs: calls.append(command),
                        state_reader=lambda _service: ("active", "enabled"),
                    )
            self.assertEqual(calls, [])
            self.assertEqual((legacy_config / "config.json").read_text(), "old")
            self.assertEqual((current_config / "config.json").read_text(), "new")


class RoutingTests(unittest.TestCase):
    def test_xray_service_can_set_bypass_socket_mark(self):
        unit = MANAGER.xray_service_unit()

        self.assertIn("CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW", unit)
        self.assertIn("AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW", unit)

    def test_applied_config_routes_tun_traffic_through_vpn_auto(self):
        native = [{
            "id": "test-node",
            "server": "vpn.example.com",
            "port": 443,
            "uuid": "11111111-1111-1111-1111-111111111111",
            "security": "tls",
            "flow": "",
            "sni": "vpn.example.com",
            "fp": "",
            "insecure": False,
            "alpn": [],
            "pbk": "",
            "sid": "",
        }]
        base = {
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {
                "rules": [{"inbound": ["tun-in"], "port": 53, "action": "hijack-dns"}],
                "final": "direct",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            original = CORE.SING_CONFIG
            CORE.SING_CONFIG = Path(tmp) / "sing-box.json"
            CORE.SING_CONFIG.write_text(json.dumps(base))
            try:
                sing, _ = CORE.build_configs(native, [])
            finally:
                CORE.SING_CONFIG = original

        self.assertEqual(sing["route"]["final"], "vpn-auto")
        self.assertEqual(sing["route"]["rules"], base["route"]["rules"])


if __name__ == "__main__":
    unittest.main()
