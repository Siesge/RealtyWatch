"""Тесты сетевой диагностики, выбора физических адаптеров, масок broadcast, брандмауэра и VPN (B1–B7):
- B1: фильтрация VPN/виртуальных адаптеров и /32, приоритет физического Wi-Fi/Ethernet со шлюзом;
- B2: расчёт broadcast по реальной маске, привязка сокетов к каждому адаптеру и ответ на зонд с сокета rx (8767);
- B3: разбор правил брандмауэра Windows (TCP/UDP, Private+Public, блокирующие правила), согласие на удаление блока и скрипт UAC netsh;
- B4: обнаружение перехвата локальной подсети со стороны активного VPN (AmneziaVPN / WireGuard);
- B5: чек-лист /api/lan/diagnose и интерфейс «Проверить сеть» в Chromium;
- B6/B7: запись событий в изолированный logs/lan.log и неприкосновенность боевого RealtyWatch_Win/logs/app.log."""
import json
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi import HTTPException

from app import lan_sync, paths, runlog, server, settings, tiles, updater
from tests.support import Case, LocalBrowser, con, has_browser
from tests.test_lan_sync import _free_port
from tests.test_tiles import _make_png


def _sample_bb_portable_adapters() -> list[dict]:
    """Снимок реальной конфигурации адаптеров ПК пользователя (AmneziaVPN + VMware + WSL + Wi-Fi + второй Ethernet)."""
    return [
        {
            "if_index": 12,
            "oper_status": 2,  # Down
            "if_type": 6,
            "name": "Ethernet",
            "description": "Intel(R) Ethernet Connection (7) I219-LM",
            "addresses": [("169.254.68.145", 16)],
            "gateways": [],
        },
        {
            "if_index": 57,
            "oper_status": 1,  # Up
            "if_type": 53,     # PROP_VIRTUAL
            "name": "AmneziaVPN",
            "description": "WireGuard Tunnel",
            "addresses": [("10.8.1.5", 32)],
            "gateways": ["0.0.0.0"],
        },
        {
            "if_index": 9,
            "oper_status": 1,
            "if_type": 6,
            "name": "VMware Network Adapter VMnet1",
            "description": "VMware Virtual Ethernet Adapter for VMnet1",
            "addresses": [("192.168.132.1", 24)],
            "gateways": [],
        },
        {
            "if_index": 26,
            "oper_status": 1,
            "if_type": 6,
            "name": "VMware Network Adapter VMnet8",
            "description": "VMware Virtual Ethernet Adapter for VMnet8",
            "addresses": [("192.168.80.1", 24)],
            "gateways": [],
        },
        {
            "if_index": 41,
            "oper_status": 1,
            "if_type": 6,
            "name": "vEthernet (WSL)",
            "description": "Hyper-V Virtual Ethernet Adapter",
            "addresses": [("172.25.16.1", 20)],
            "gateways": [],
        },
        {
            "if_index": 15,
            "oper_status": 1,
            "if_type": 6,
            "name": "Ethernet 2",
            "description": "Realtek PCIe GbE Family Controller",
            "addresses": [("172.20.5.14", 20), ("192.168.99.5", 32)],
            "gateways": [],
        },
        {
            "if_index": 18,
            "oper_status": 1,
            "if_type": 71,
            "name": "Wi-Fi",
            "description": "Intel(R) Wi-Fi 6 AX200 160MHz",
            "addresses": [("192.168.2.119", 24)],
            "gateways": ["192.168.2.1"],
        },
    ]


class LanDiagnosticsAndRealNetUnitTests(Case):
    def setUp(self):
        super().setUp()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        self._orig_bind_host = lan_sync.service._bind_host

    def tearDown(self):
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        lan_sync.service._bind_host = self._orig_bind_host
        super().tearDown()

    def test_b1_b2_adapter_filtering_gateway_priority_subnet_broadcast_and_rx_probe_reply(self):
        # B1: проверяем отсев VPN/виртуальных адаптеров и /32, а также приоритет адаптера со шлюзом
        ifaces = lan_sync.local_ipv4s(_sample_bb_portable_adapters())
        self.assertEqual([x["ip"] for x in ifaces], ["192.168.2.119", "172.20.5.14"])
        self.assertEqual(ifaces[0]["adapter"], "Wi-Fi")
        self.assertEqual(ifaces[0]["kind"], "wifi")
        self.assertEqual(ifaces[0]["prefix"], 24)
        self.assertEqual(ifaces[0]["broadcast"], "192.168.2.255")
        self.assertTrue(ifaces[0]["has_gateway"])

        # B2: проверяем расчёт направленного broadcast по произвольной маске (/20, /16, /24, /32)
        self.assertEqual(ifaces[1]["adapter"], "Ethernet 2")
        self.assertEqual(ifaces[1]["kind"], "ethernet")
        self.assertEqual(ifaces[1]["prefix"], 20)
        self.assertEqual(ifaces[1]["broadcast"], "172.20.15.255")
        self.assertEqual(lan_sync.compute_subnet_broadcast("10.15.3.7", 16), "10.15.255.255")
        self.assertIsNone(lan_sync.compute_subnet_broadcast("10.8.1.5", 32))

        # B2: проверяем, что ответ на RW_PROBE_V1 уходит именно с сокета rx (порт 8767), а не со случайного порта!
        cfg = settings.load()
        cfg["lan"] = {"enabled": True, "port": 8766, "auto_discovery": True, "device_id": "", "device_name": "Тестовый ПК"}
        settings.save(cfg)
        c = con()
        my_ident = lan_sync.service._identity(c)
        c.close()

        rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            rx_sock.bind(("127.0.0.1", 0))
            probe_sock.bind(("127.0.0.1", 0))
            probe_sock.settimeout(2.0)
            rx_port = rx_sock.getsockname()[1]
            probe_port = probe_sock.getsockname()[1]

            probe_payload = json.dumps({
                "magic": "RW_PROBE_V1",
                "device_id": "remote-peer-uuid",
                "device_name": "Ноутбук Партнёра",
                "port": 8766,
                "disc_port": probe_port,
                "platform": "windows",
            }).encode("utf-8")

            lan_sync.service._handle_discovery_packet(
                probe_payload,
                "127.0.0.1",
                probe_port,
                my_lan_port=8766,
                reply_sock=rx_sock,
            )

            raw_reply, reply_sender = probe_sock.recvfrom(4096)
            reply_pkt = json.loads(raw_reply.decode("utf-8"))
            self.assertEqual(reply_pkt["magic"], "RW_DISCOVER_V1")
            self.assertEqual(reply_pkt["device_id"], my_ident["device_id"])
            # Ключевая проверка B2: порт-источник ответа обязан совпадать с портом сокета rx!
            self.assertEqual(reply_sender[1], rx_port)
        finally:
            rx_sock.close()
            probe_sock.close()

    def test_b4_vpn_route_hijack_detection_and_split_tunnel_clearance(self):
        adapters = _sample_bb_portable_adapters()

        # Сценарий 1: AmneziaVPN (if_index=57) перехватывает 192.168.2.1 и 192.168.2.255 вместо Wi-Fi (if_index=18)
        hijacked = lan_sync.detect_vpn_route_hijack(adapters, route_resolver=lambda _ip: 57)
        self.assertTrue(hijacked["hijacked"])
        self.assertEqual(hijacked["vpn_adapter"], "AmneziaVPN")
        self.assertEqual(hijacked["physical_adapter"], "Wi-Fi")
        self.assertIn("192.168.2.1", hijacked["intercepted_targets"])
        self.assertIn("192.168.0.0/16", hijacked["message"])

        # Сценарий 2: В VPN включено раздельное туннелирование — подсеть 192.168.2.0/24 идёт через Wi-Fi (if_index=18)
        clean = lan_sync.detect_vpn_route_hijack(
            adapters,
            route_resolver=lambda ip: 18 if ip.startswith("192.168.2.") else 57,
        )
        self.assertFalse(clean["hijacked"])
        self.assertEqual(clean["vpn_adapter"], "")

    def test_b3_firewall_parser_public_profile_blocking_consent_and_netsh_script(self):
        prog = r"D:\Apps\RealtyWatch\runtime\python\pythonw.exe"
        phys = [{"adapter": "Wi-Fi", "if_index": 18, "ip": "192.168.2.119", "prefix": 24, "kind": "wifi"}]

        # 1) Правил нет, сеть в профиле Public
        empty_fw = lan_sync.parse_firewall_and_profiles(
            {
                "profiles": [
                    {"Name": "T-Net 3", "InterfaceAlias": "Wi-Fi", "InterfaceIndex": 18, "Category": "Public"},
                    {"Name": "AmneziaVPN", "InterfaceAlias": "AmneziaVPN", "InterfaceIndex": 57, "Category": "Public"},
                ],
                "rules": [],
            },
            prog,
            8766,
            8767,
            phys,
        )
        self.assertFalse(empty_fw["ok"])
        self.assertFalse(empty_fw["tcp_allowed"])
        self.assertFalse(empty_fw["udp_allowed"])
        self.assertTrue(empty_fw["has_public_profile"])
        self.assertEqual([p["name"] for p in empty_fw["public_profiles"]], ["T-Net 3"])

        # 2) Разрешающие правила есть для Private+Public на TCP 8766 и UDP 8767
        ok_rules = [
            {
                "Name": "RW-TCP",
                "DisplayName": "RealtyWatch LAN TCP",
                "Program": prog,
                "Direction": "Inbound",
                "Action": "Allow",
                "Enabled": "True",
                "Profile": "Private, Public",
                "Protocol": "TCP",
                "LocalPort": "8766",
            },
            {
                "Name": "RW-UDP",
                "DisplayName": "RealtyWatch LAN UDP",
                "Program": prog,
                "Direction": "Inbound",
                "Action": "Allow",
                "Enabled": "True",
                "Profile": "Any",
                "Protocol": "UDP",
                "LocalPort": "8767",
            },
        ]
        good_fw = lan_sync.parse_firewall_and_profiles(
            {"profiles": [{"Name": "T-Net 3", "InterfaceAlias": "Wi-Fi", "InterfaceIndex": 18, "Category": "Private"}], "rules": ok_rules},
            prog,
            8766,
            8767,
            phys,
        )
        self.assertTrue(good_fw["ok"])
        self.assertFalse(good_fw["has_public_profile"])

        # 3) При наличии блокирующего правила ok=False и без согласия remove_blocking выбрасывается предупреждение
        with_block = ok_rules + [{
            "Name": "{BLOCK-GUID}",
            "DisplayName": "Block Pythonw",
            "Program": prog,
            "Direction": "Inbound",
            "Action": "Block",
            "Enabled": "True",
            "Profile": "Public",
            "Protocol": "Any",
            "LocalPort": "Any",
        }]
        blocked_fw = lan_sync.parse_firewall_and_profiles({"profiles": [], "rules": with_block}, prog, 8766, 8767, phys)
        self.assertFalse(blocked_fw["ok"])
        self.assertTrue(blocked_fw["has_block_rules"])

        with patch.object(lan_sync, "query_firewall_and_profiles", side_effect=[blocked_fw, good_fw]), \
             patch.object(lan_sync, "_run_elevated_powershell", return_value=0) as mock_uac:
            with self.assertRaises(HTTPException) as ctx400:
                server.lan_firewall_allow({"remove_blocking": False})
            self.assertEqual(ctx400.exception.status_code, 400)
            self.assertIn("блокирующие правила", ctx400.exception.detail)
            mock_uac.assert_not_called()

        with patch.object(lan_sync, "query_firewall_and_profiles", side_effect=[blocked_fw, good_fw]), \
             patch.object(lan_sync, "_run_elevated_powershell", return_value=0) as mock_uac2:
            res_allow = server.lan_firewall_allow({"remove_blocking": True})
            self.assertTrue(res_allow["ok"])
            self.assertTrue(res_allow["firewall"]["ok"])
            script_called = mock_uac2.call_args[0][0]
            self.assertIn("Remove-NetFirewallRule -Name '{BLOCK-GUID}'", script_called)
            self.assertIn("localport=8766 profile=private,public", script_called)
            self.assertIn("localport=8767 profile=private,public", script_called)

    def test_b5_b6_b7_diagnose_endpoint_lan_log_and_real_app_log_isolation(self):
        real_app_log = paths.LOGS / "app.log"
        before_exists = real_app_log.exists()
        before_stat = (real_app_log.stat().st_size, real_app_log.stat().st_mtime_ns) if before_exists else None

        lan_port = _free_port()
        cfg = settings.load()
        cfg["lan"] = {"enabled": True, "port": lan_port, "auto_discovery": True, "device_id": "", "device_name": "Диаг-ПК"}
        settings.save(cfg)
        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())

        # Диагностика с прямым TCP-пиром (указываем свой собственный открытый порт как доступный узел)
        diag = server.lan_diagnose({"peer_ip": f"127.0.0.1:{lan_port}"})
        by_id = {c["id"]: c for c in diag["checks"]}
        self.assertEqual(by_id["lan_ports"]["status"], "ok")
        self.assertEqual(by_id["peer_tcp"]["status"], "ok")
        self.assertIn("401", by_id["peer_tcp"]["detail"])

        # Проверяем, что изолированный logs/lan.log создался и содержит записи
        test_lan_log = runlog.LOGS_DIR / "lan.log"
        self.assertTrue(test_lan_log.is_file())
        lan_log_txt = test_lan_log.read_text(encoding="utf-8")
        self.assertIn("HTTP LAN-сервер запущен", lan_log_txt)
        self.assertIn("Диагностика сети завершена", lan_log_txt)

        # Проверяем B7: боевой RealtyWatch_Win/logs/app.log НЕ был затронут ни на байт!
        if before_exists:
            after_stat = (real_app_log.stat().st_size, real_app_log.stat().st_mtime_ns)
            self.assertEqual(after_stat, before_stat, "тесты не должны писать в боевой logs/app.log!")
        else:
            self.assertFalse(real_app_log.exists(), "тесты не должны создавать боевой logs/app.log!")


class LanDiagnosticsBrowserUITests(Case):
    @unittest.skipUnless(has_browser(), "Chromium не установлен (setup.bat)")
    def test_lan_modal_shows_physical_ip_vpn_hijack_firewall_card_and_checklist_in_browser(self):
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        orig_bind = lan_sync.service._bind_host
        self.addCleanup(setattr, lan_sync.service, "_bind_host", orig_bind)
        self.addCleanup(lan_sync.stop)

        lan_port = _free_port()
        cfg = settings.load()
        cfg["lan"] = {"enabled": True, "port": lan_port, "auto_discovery": True, "device_id": "", "device_name": "Основной Ноутбук"}
        settings.save(cfg)
        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())

        mock_ifaces = [{
            "ip": "192.168.2.119",
            "prefix": 24,
            "adapter": "Wi-Fi",
            "kind": "wifi",
            "description": "Intel(R) Wi-Fi 6 AX200 160MHz",
            "broadcast": "192.168.2.255",
            "gateway": "192.168.2.1",
            "has_gateway": True,
            "if_index": 18,
        }]
        mock_vpn = {
            "hijacked": True,
            "vpn_adapter": "AmneziaVPN",
            "physical_adapter": "Wi-Fi",
            "physical_ip": "192.168.2.119",
            "subnet": "192.168.2.0/24",
            "intercepted_targets": ["192.168.2.1", "192.168.2.255"],
            "message": "VPN «AmneziaVPN» перехватывает локальную сеть",
        }
        mock_fw = {
            "checked": True,
            "program": r"D:\RealtyWatch\runtime\python\pythonw.exe",
            "tcp_port": lan_port,
            "udp_port": lan_port + 1,
            "tcp_allowed": False,
            "udp_allowed": False,
            "has_block_rules": False,
            "block_rules": [],
            "ok": False,
            "profiles": [{"name": "T-Net 3", "adapter": "Wi-Fi", "if_index": 18, "category": "Public"}],
            "has_public_profile": True,
            "public_profiles": [{"name": "T-Net 3", "adapter": "Wi-Fi", "if_index": 18, "category": "Public"}],
        }

        orig_status = lan_sync.service.status

        def patched_status(refresh: bool = False):
            st = orig_status(refresh=refresh)
            st["local_interfaces"] = mock_ifaces
            st["local_ips"] = ["192.168.2.119"]
            st["vpn_hijack"] = mock_vpn
            st["firewall"] = mock_fw
            return st

        p_st = patch.object(lan_sync.service, "status", side_effect=patched_status)
        p_tile = patch.object(tiles, "_fetch_url", return_value=(200, _make_png()))
        p_upd = patch.object(updater, "_fetch_bytes", return_value=(404, b""))
        p_st.start()
        p_tile.start()
        p_upd.start()
        self.addCleanup(p_st.stop)
        self.addCleanup(p_tile.stop)
        self.addCleanup(p_upd.stop)

        ui_port = _free_port()
        uv_srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=ui_port, log_level="error"))
        t = threading.Thread(target=uv_srv.run, daemon=True)
        t.start()
        self.addCleanup(lambda: setattr(uv_srv, "should_exit", True) or t.join(timeout=5.0))
        deadline = time.time() + 8.0
        while not uv_srv.started and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(uv_srv.started)

        b = LocalBrowser("ui-lan-diag", lambda h, u: False, lambda m: None, None, window="hidden")
        self.addCleanup(b.close)
        b.start()
        page = b.page
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"http://127.0.0.1:{ui_port}/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof SETTINGS === 'object' && SETTINGS !== null && view === 'dashboard'", timeout=10000)

        page.click("#lanBtn")
        page.wait_for_selector("#lanModalWin", state="visible", timeout=5000)

        modal_txt = page.text_content("#lanModalWin")
        # B1: показан адаптер Wi-Fi и кнопка копирует именно физический IP 192.168.2.119 (а не VPN!)
        self.assertIn("Wi-Fi", modal_txt)
        self.assertIn("Копировать адрес (192.168.2.119)", page.text_content("#btnLanCopyAddr"))
        # B4: плашка перехвата AmneziaVPN с инструкцией про раздельное туннелирование
        self.assertIn("AmneziaVPN", page.text_content("#lanVpnWarn"))
        self.assertIn("192.168.0.0/16", page.text_content("#lanVpnWarn"))
        # B3: карточка брандмауэра и предупреждение о профиле Public «T-Net 3»
        self.assertIn("Брандмауэр блокирует подключения", page.text_content("#lanFwCard"))
        self.assertIn("T-Net 3", page.text_content("#lanPubNetCard"))

        # B5: жмём «Проверить сеть» и проверяем появление чек-листа #lanDiagPanel
        page.click("#btnLanDiagnose")
        page.wait_for_selector("#lanDiagPanel .lan-diag-row", state="visible", timeout=8000)
        self.assertGreaterEqual(page.locator("#lanDiagPanel .lan-diag-row").count(), 5)

        self.assertEqual(js_errors, [], f"ошибки JS в браузере: {js_errors}")


if __name__ == "__main__":
    unittest.main()
