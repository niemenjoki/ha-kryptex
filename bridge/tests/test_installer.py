"""Static checks of the Windows installer files and their README instructions.

The installer itself needs Windows (scheduled task, firewall), so these tests check what can be checked anywhere:
the promises the README makes about the scripts, and that the README and scripts agree.
"""
import json
import logging
from pathlib import Path
import re
import socket
import sys
import tempfile
import unittest

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

import kryptex_bridge as kb  # noqa: E402

INSTALL_CMD = (BRIDGE / "install.cmd").read_bytes()
INSTALL_PS1 = (BRIDGE / "install.ps1").read_text(encoding="ascii")
UNINSTALL_PS1 = (BRIDGE / "uninstall.ps1").read_text(encoding="ascii")
README = (BRIDGE.parent / "README.md").read_text(encoding="utf-8")
DOWNLOADERS = ("Invoke-WebRequest", "DownloadFile", "DownloadString", "Start-BitsTransfer", "curl", "wget", "iex",
               "Invoke-Expression")


def ps1_parameters(text: str) -> dict:
    block = re.search(r"^param\((.*?)^\)", text, re.S | re.M).group(1)
    return {m.group(1): m.group(2) for m in re.finditer(r"\$(\w+)(?:\s*=\s*\"?([^\",\s]*)\"?)?", block)}


class InstallCmdTests(unittest.TestCase):
    def test_is_ascii_with_windows_line_endings(self):
        # cmd.exe can misread labels in a file with LF line endings.
        INSTALL_CMD.decode("ascii")
        self.assertNotIn(b"\n", INSTALL_CMD.replace(b"\r\n", b""))

    def test_only_runs_install_ps1_from_its_own_folder(self):
        text = INSTALL_CMD.decode("ascii")
        self.assertIn('-File "%~dp0install.ps1"', text)
        for word in DOWNLOADERS + ("http",):
            self.assertNotIn(word.lower(), text.lower())

    def test_asks_for_administrator_rights_and_keeps_the_window_open(self):
        text = INSTALL_CMD.decode("ascii")
        self.assertIn("-Verb RunAs", text)
        self.assertIn("pause", text)


class InstallPs1Tests(unittest.TestCase):
    def test_scripts_are_ascii(self):
        # Windows PowerShell 5.1 reads a file without a BOM in the system code page; ASCII is read the same everywhere.
        for name in ("install.ps1", "uninstall.ps1"):
            (BRIDGE / name).read_text(encoding="ascii")

    def test_downloads_nothing_and_only_calls_this_pc(self):
        for word in DOWNLOADERS:
            self.assertNotIn(word, INSTALL_PS1)
        for url in re.findall(r"https?://[^\s\"']+", INSTALL_PS1):
            self.assertTrue(url.startswith("http://127.0.0.1:"), url)

    def test_firewall_defaults_to_the_local_network(self):
        params = ps1_parameters(INSTALL_PS1)
        self.assertEqual(params["FirewallRemoteAddress"], "LocalSubnet")
        self.assertIn("-Profile Private, Domain", INSTALL_PS1)

    def test_default_port_matches_the_bridge(self):
        self.assertEqual(int(ps1_parameters(INSTALL_PS1)["Port"]), kb.DEFAULTS["port"])

    def test_stops_the_old_bridge_before_choosing_the_port(self):
        self.assertLess(INSTALL_PS1.index("Stop-ScheduledTask"), INSTALL_PS1.index("Get-PortUser $Port"))

    def test_a_given_port_that_is_taken_stops_before_changes(self):
        stop = INSTALL_PS1.index('throw "Port $Port is in use')
        self.assertLess(stop, INSTALL_PS1.index("Copy-Item"))
        self.assertLess(stop, INSTALL_PS1.index("Set-Content"))

    def test_elevated_tasks_are_registered_and_removed(self):
        for name in ('"$TaskName start"', '"$TaskName stop"'):
            self.assertIn(f"Register-ScheduledTask -TaskName {name}", INSTALL_PS1)
            self.assertIn(f"Unregister-ScheduledTask -TaskName {name}", UNINSTALL_PS1)
        self.assertIn("-RunLevel Highest", INSTALL_PS1)
        # The bridge's own task stays without elevation.
        bridge_task = [line for line in INSTALL_PS1.splitlines() if "Register-ScheduledTask -TaskName $TaskName " in line]
        self.assertEqual(len(bridge_task), 1)
        self.assertNotIn("Principal", bridge_task[0])

    def test_task_names_match_the_bridge_defaults(self):
        for command in (kb.DEFAULTS["start_command"], kb.DEFAULTS["stop_command"]):
            self.assertEqual(command[:3], ["schtasks", "/run", "/tn"])
            self.assertTrue(command[3].startswith("Kryptex bridge "))

    def test_names_match_the_uninstaller(self):
        for text in (INSTALL_PS1, UNINSTALL_PS1):
            self.assertIn('$TaskName = "Kryptex bridge"', text)
        self.assertIn('Join-Path $env:LOCALAPPDATA "kryptex-bridge"', UNINSTALL_PS1)
        self.assertIn('Join-Path $env:APPDATA "kryptex-bridge"', UNINSTALL_PS1)


class StartupErrorTests(unittest.TestCase):
    """The bridge runs hidden, so startup errors must reach bridge.log."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.json"
        self.log = Path(self.tmp.name) / "bridge.log"

    def tearDown(self):
        logging.getLogger().handlers.clear()
        self.tmp.cleanup()

    def run_main(self) -> int:
        logging.getLogger().handlers.clear()  # basicConfig sets up logging only once per process
        result = kb.main(["--config", str(self.config), "--log", str(self.log)])
        logging.shutdown()
        return result

    def test_port_in_use_is_logged(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            self.config.write_text(json.dumps({"api_key": "k", "host": "127.0.0.1", "port": port}))
            self.assertEqual(self.run_main(), 1)
        self.assertIn(f"Not started: port {port} is already in use", self.log.read_text())

    def test_missing_api_key_is_logged(self):
        self.config.write_text("{}")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("Not started: api_key is missing", self.log.read_text())

    def test_windows_does_not_share_a_taken_port(self):
        self.assertEqual(kb.BridgeServer.allow_reuse_address, sys.platform != "win32")


class ReadmeTests(unittest.TestCase):
    def test_every_installer_option_is_documented(self):
        for name in ps1_parameters(INSTALL_PS1):
            self.assertIn(f"`-{name}`", README)

    def test_documented_defaults_match_the_installer(self):
        params = ps1_parameters(INSTALL_PS1)
        self.assertIn("| `-Port` | first free of 5500-5510 |", README)
        self.assertIn(f"| `-FirewallRemoteAddress` | `{params['FirewallRemoteAddress']}` |", README)

    def test_port_range_matches_the_installer(self):
        self.assertIn("5500..5510", INSTALL_PS1)
        self.assertIn("5501-5510", README)

    def test_my_home_assistant_buttons_point_to_this_integration(self):
        manifest = json.loads((BRIDGE.parent / "custom_components/kryptex/manifest.json").read_text(encoding="utf-8"))
        owner, repository = re.search(r"github\.com/([^/]+)/([^/\s)]+)", manifest["documentation"]).groups()
        self.assertIn("redirect/hacs_repository/?owner=" + owner + "&repository=" + repository
                      + "&category=integration", README)
        self.assertIn("redirect/config_flow_start/?domain=" + manifest["domain"], README)

    def test_linked_files_exist(self):
        for link in re.findall(r"\]\((bridge/[^)]+)\)", README):
            self.assertTrue((BRIDGE.parent / link).is_file(), link)

    def test_install_by_hand_uses_the_names_the_uninstaller_removes(self):
        section = README.split("### Install by hand", 1)[1].split("\n## ", 1)[0]
        for name in ("%LOCALAPPDATA%\\kryptex-bridge", "%APPDATA%\\kryptex-bridge", "Kryptex bridge"):
            self.assertIn(name, section)


if __name__ == "__main__":
    unittest.main()
