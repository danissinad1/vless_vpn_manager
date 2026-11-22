#!/usr/bin/env python3
import base64
import hashlib
import importlib.machinery
import json
import tempfile
import unittest
from pathlib import Path

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
