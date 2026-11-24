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

    def test_v181_partner_17_delayed_ephemeral_reply_and_asymmetric_broadcast_diag(self):
        """1.8.1 (п. 1, 4, 6): зонд уходит строго с порта lan.port+1, ждёт 2–3 с ответа от эмулятора 1.7
        со случайного эфемерного порта без собственных broadcast, а диагностика отмечает старый протокол и односторонний broadcast."""
        my_lan_port = _free_port()
        while True:
            peer_lan_port = _free_port()
            if abs(peer_lan_port - my_lan_port) > 4:
                break
        peer_disc_port = peer_lan_port + 1

        cfg = settings.load()
        cfg["lan"] = {"enabled": True, "port": my_lan_port, "auto_discovery": True, "device_id": "", "device_name": "BB-Portable"}
        settings.save(cfg)
        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())

        # Эмулятор партнёра Olga (1.7): НЕ шлёт периодических RW_DISCOVER_V1 сам,
        # а на входящий RW_PROBE_V1 отвечает с задержкой 2.2 с и с НОВОГО случайного порта (без поля app_version).
        stop_olga = threading.Event()
        olga_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        olga_rx.bind(("127.0.0.1", peer_disc_port))
        olga_rx.settimeout(0.2)
        self.addCleanup(olga_rx.close)
        self.addCleanup(stop_olga.set)

        seen_probe_src_ports: list[int] = []
        seen_probe_versions: list[str] = []

        def _olga_loop():
            while not stop_olga.is_set():
                try:
                    raw, sender = olga_rx.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    pkt = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if pkt.get("magic") != "RW_PROBE_V1":
                    continue
                seen_probe_src_ports.append(sender[1])
                seen_probe_versions.append(str(pkt.get("app_version") or ""))
                reply_target_port = int(pkt.get("disc_port") or sender[1])

                def _delayed_reply(dst_port: int):
                    if stop_olga.wait(2.2):
                        return
                    ephem = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    try:
                        ephem.bind(("127.0.0.1", 0))
                        # Пакет 1.7 без app_version
                        rep = json.dumps({
                            "magic": "RW_DISCOVER_V1",
                            "device_id": "olga-v17-id",
                            "device_name": "Olga",
                            "port": peer_lan_port,
                            "platform": "windows",
                        }).encode("utf-8")
                        ephem.sendto(rep, ("127.0.0.1", dst_port))
                    finally:
                        ephem.close()

                threading.Thread(target=_delayed_reply, args=(reply_target_port,), daemon=True).start()

        t_olga = threading.Thread(target=_olga_loop, daemon=True)
        t_olga.start()

        # Вызываем discover_now с дефолтным ожиданием (4.5 с) — должен дождаться ответа через 2.2 с!
        res = lan_sync.discover_now(target_ports=[peer_disc_port])
        disc_by_id = {d["device_id"]: d for d in res["discovered"]}
        self.assertIn("olga-v17-id", disc_by_id, "партнёр 1.7 с задержкой 2.2 с и случайным портом должен находиться!")
        olga_d = disc_by_id["olga-v17-id"]
        self.assertEqual(olga_d["device_name"], "Olga")
        self.assertTrue(olga_d["legacy_protocol"])
        self.assertFalse(olga_d["seen_broadcast"])
        self.assertTrue(olga_d["via_probe_only"])
        # Проверяем п. 1: зонд ушёл строго с основного порта my_lan_port + 1 и содержит app_version!
        self.assertTrue(seen_probe_src_ports)
        self.assertTrue(all(p == my_lan_port + 1 for p in seen_probe_src_ports), f"зонды должны идти с порта {my_lan_port + 1}, было: {seen_probe_src_ports}")
        self.assertIn(lan_sync.current_lan_app_version(), seen_probe_versions)

        # Проверяем п. 4: diagnose_network видит ответ на my_lan_port+1 и выдаёт оба предупреждения (старый протокол + только по запросу)
        lan_sync.service._udp_started_mono = time.monotonic() - 10.0
        diag = lan_sync.diagnose_network(peer_ip=f"127.0.0.1:{peer_lan_port}", probe_wait_sec=3.0)
        by_id = {c["id"]: c for c in diag["checks"]}
        self.assertEqual(by_id["udp_probe"]["status"], "ok")
        self.assertIn("Olga", by_id["udp_probe"]["detail"])
        self.assertIn("peer_legacy_protocol", by_id)
        self.assertEqual(by_id["peer_legacy_protocol"]["status"], "warn")
        self.assertIn("отвечает по старому протоколу", by_id["peer_legacy_protocol"]["title"])
        self.assertIn("1.7", by_id["peer_legacy_protocol"]["title"])
        self.assertIn("peer_asymmetric_bcast", by_id)
        self.assertEqual(by_id["peer_asymmetric_bcast"]["status"], "warn")
        self.assertIn("найдено только по запросу", by_id["peer_asymmetric_bcast"]["title"])

    def test_v181_incoming_probe_sender_added_to_discovered_and_unicast_subnet_sweep(self):
        """1.8.1 (п. 2, 3, 4): входящий RW_PROBE_V1 от несопряжённого ПК добавляет его в discovered (с разбором app_version),
        служебный diag-* игнорируется, а unicast-обход подсети покрывает до /22 и ограничивает крупные сети своей /24."""
        # 1. Проверка расчёта адресов подсети (compute_subnet_unicast_hosts)
        h24 = lan_sync.compute_subnet_unicast_hosts("192.168.2.119", 24)
        self.assertEqual(len(h24), 253)
        self.assertNotIn("192.168.2.0", h24)
        self.assertNotIn("192.168.2.255", h24)
        self.assertNotIn("192.168.2.119", h24)
        self.assertIn("192.168.2.148", h24)

        h22 = lan_sync.compute_subnet_unicast_hosts("192.168.0.10", 22)
        self.assertEqual(len(h22), 1021)
        self.assertIn("192.168.3.254", h22)
        self.assertNotIn("192.168.0.10", h22)

        # Для /20 (крупнее /22) обходится только собственная /24!
        h20 = lan_sync.compute_subnet_unicast_hosts("172.20.5.14", 20)
        self.assertEqual(len(h20), 253)
        self.assertTrue(all(ip.startswith("172.20.5.") for ip in h20))

        self.assertEqual(lan_sync.compute_subnet_unicast_hosts("10.8.1.5", 32), [])

        # 2. Проверка п. 3 и разбора app_version при входящем RW_PROBE_V1
        my_port = _free_port()
        cfg = settings.load()
        cfg["lan"] = {"enabled": True, "port": my_port, "auto_discovery": False, "device_id": "", "device_name": "Main-PC"}
        settings.save(cfg)

        rx_dummy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx_dummy.bind(("127.0.0.1", 0))
        sender_dummy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender_dummy.bind(("127.0.0.1", 0))
        self.addCleanup(rx_dummy.close)
        self.addCleanup(sender_dummy.close)

        # Несопряжённое устройство само прислало RW_PROBE_V1 -> должно появиться в discovered!
        lan_sync.service._handle_discovery_packet(
            json.dumps({
                "magic": "RW_PROBE_V1",
                "device_id": "probe-sender-olga",
                "device_name": "Ноутбук Ольги",
                "port": 18766,
                "disc_port": sender_dummy.getsockname()[1],
                "platform": "windows",
                "app_version": "1.8.1",
            }).encode("utf-8"),
            "192.168.2.148",
            51154,
            my_lan_port=my_port,
            reply_sock=rx_dummy,
        )
        # Диагностический зонд diag-* в discovered попадать НЕ должен
        lan_sync.service._handle_discovery_packet(
            json.dumps({
                "magic": "RW_PROBE_V1",
                "device_id": "diag-deadbeef",
                "device_name": "diag@Other",
                "port": 18766,
                "disc_port": sender_dummy.getsockname()[1],
                "platform": "diag",
            }).encode("utf-8"),
            "192.168.2.149",
            51155,
            my_lan_port=my_port,
            reply_sock=rx_dummy,
        )

        disc = {d["device_id"]: d for d in lan_sync.service.list_discovered()}
        self.assertIn("probe-sender-olga", disc)
        self.assertNotIn("diag-deadbeef", disc)
        self.assertEqual(disc["probe-sender-olga"]["ip"], "192.168.2.148")
        self.assertEqual(disc["probe-sender-olga"]["port"], 18766)
        self.assertEqual(disc["probe-sender-olga"]["app_version"], "1.8.1")
        self.assertFalse(disc["probe-sender-olga"]["legacy_protocol"])

        # 3. Проверка unicast-обхода с моком сокета (и автоматического фолбэка в discover_now)
        sent_packets: list[tuple[dict, tuple[str, int]]] = []

        class _MockSock:
            def getsockname(self):
                return ("0.0.0.0", my_port + 1)

            def sendto(self, data: bytes, addr: tuple[str, int]):
                sent_packets.append((json.loads(data.decode("utf-8")), addr))
                return len(data)

        cnt = lan_sync.service._unicast_subnet_sweep(
            my_port,
            tx_sock=_MockSock(),
            interfaces=[{"ip": "192.168.2.119", "prefix": 24, "adapter": "Wi-Fi"}],
            batch_pause_sec=0.0,
        )
        # По 253 хостам на порты {my_port+1, 8767}
        expected_ports = {my_port + 1, 8767}
        self.assertEqual(cnt, 253 * len(expected_ports))
        target_ips = {addr[0] for _p, addr in sent_packets}
        self.assertEqual(len(target_ips), 253)
        self.assertIn("192.168.2.148", target_ips)
        self.assertNotIn("192.168.2.119", target_ips)
        expected_ver = lan_sync.current_lan_app_version()
        self.assertTrue(all(pkt["disc_port"] == my_port + 1 and pkt["app_version"] == expected_ver for pkt, _a in sent_packets))

    def test_v182_cloned_device_id_auto_heal_on_discovery_pairing_and_import_replace(self):
        """1.8.2: автоматическое устранение клона device_id (после копирования папки/settings.json или импорта БД replace):
        1) несовпадение machine_fp при запуске генерирует новый уникальный device_id;
        2) transfer.import_zip(..., mode='replace') сохраняет собственный device_id целевого компьютера;
        3) приход пакета RW_DISCOVER_V1 с нашим же device_id с чужого IP (192.168.2.148) автоматически меняет наш ID и добавляет партнёра в discovered;
        4) pair_with при ответе 400 «Нельзя сопрячь устройство с самим собой» от второго экземпляра (напр. 1.8.1) сам меняет ID и завершает сопряжение с первой попытки, а PIN живёт 10 минут (600 с)."""
        from app import transfer

        self.assertEqual(lan_sync.PIN_TTL_SEC, 600)

        # 1) Проверка смены device_id при переносе папки с чужим machine_fp
        cloned_id = "9a724ad8-413a-41a4-9c57-0926809f857e"
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": _free_port(),
            "auto_discovery": False,
            "device_id": cloned_id,
            "device_name": "Чужой-ПК",
            "machine_fp": "0000foreign000000",
        }
        settings.save(cfg)
        c = con()
        healed_ident = settings.ensure_device_identity(c)
        c.close()
        self.assertNotEqual(healed_ident["device_id"], cloned_id, "при чужом machine_fp должен создаться новый device_id")
        self.assertEqual(settings.load()["lan"]["machine_fp"], settings.machine_fingerprint())

        # 2) Проверка сохранения собственного device_id при transfer.import_zip(..., mode='replace')
        export_zip_path = self.dir / "exported_from_pc1.zip"
        transfer.export_zip(export_zip_path, with_images=False)
        pc1_id = healed_ident["device_id"]

        # Переключаем локальную машину на собственный ID pc2_id и импортируем архив PC1 в режиме replace
        c2 = con()
        pc2_ident = settings.rotate_device_id(c2)
        c2.close()
        pc2_id = pc2_ident["device_id"]
        self.assertNotEqual(pc2_id, pc1_id)

        transfer.import_zip(export_zip_path, mode="replace")
        c3 = con()
        after_replace = settings.ensure_device_identity(c3)
        meta_id = c3.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]
        c3.close()
        self.assertEqual(after_replace["device_id"], pc2_id, "импорт базы в режиме replace НЕ должен затирать device_id этого ПК!")
        self.assertEqual(meta_id, pc2_id)

        # 3) Живое автоустранение коллизии при приёме UDP RW_DISCOVER_V1 с тем же device_id с чужого IP (192.168.2.148)
        my_port = _free_port()
        cur_fp = settings.machine_fingerprint()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": my_port,
            "auto_discovery": False,
            "device_id": cloned_id,
            "device_name": "BB-Portable",
            "machine_fp": cur_fp,
        }
        settings.save(cfg)
        c4 = con()
        c4.execute("INSERT OR REPLACE INTO meta VALUES('device_id', ?)", (cloned_id,))
        c4.commit()
        c4.close()

        lan_sync.service._handle_discovery_packet(
            json.dumps({
                "magic": "RW_DISCOVER_V1",
                "device_id": cloned_id,
                "device_name": "Olga",
                "port": 8766,
                "disc_port": 8767,
                "platform": "windows",
                "app_version": "1.8.1",
            }).encode("utf-8"),
            "192.168.2.148",
            8767,
            my_lan_port=my_port,
        )
        new_my_id = lan_sync.service._identity()["device_id"]
        self.assertNotEqual(new_my_id, cloned_id, "при получении пакета с нашим ID с другого IP наш ID должен автоматически смениться")
        disc = {d["device_id"]: d for d in lan_sync.service.list_discovered()}
        self.assertIn(cloned_id, disc, "компьютер партнёра (Olga) должен сразу появиться в списке найденных устройств!")
        self.assertEqual(disc[cloned_id]["device_name"], "Olga")
        self.assertEqual(disc[cloned_id]["ip"], "192.168.2.148")

        # 4) Автоповтор pair_with при ответе 400 «Нельзя сопрячь устройство с самим собой» от партнёра 1.8.1 с таким же ID
        import http.server

        forced_same_id = "deadbeef-same-uuid-181"
        cfg = settings.load()
        cfg["lan"]["device_id"] = forced_same_id
        cfg["lan"]["machine_fp"] = cur_fp
        settings.save(cfg)
        c5 = con()
        c5.execute("INSERT OR REPLACE INTO meta VALUES('device_id', ?)", (forced_same_id,))
        c5.commit()
        c5.close()

        calls_seen: list[str] = []

        class _MockPeer181Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(ln).decode("utf-8"))
                calls_seen.append(body["device_id"])
                if body["device_id"] == forced_same_id:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"detail": "Нельзя сопрячь устройство с самим собой"}, ensure_ascii=False).encode("utf-8"))
                    return
                assert body["pin"] == "654321"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "device_id": forced_same_id,
                    "device_name": "Olga",
                    "port": self.server.server_port,
                    "platform": "windows",
                    "token": "peer-secret-token-181",
                }, ensure_ascii=False).encode("utf-8"))

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "device_id": forced_same_id,
                    "device_name": "Olga",
                    "port": self.server.server_port,
                    "platform": "windows",
                }).encode("utf-8"))

        srv181 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockPeer181Handler)
        threading.Thread(target=srv181.serve_forever, daemon=True).start()
        self.addCleanup(srv181.server_close)
        self.addCleanup(srv181.shutdown)

        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())

        pair_ok = lan_sync.pair_with("127.0.0.1", srv181.server_port, "654321")
        self.assertTrue(pair_ok["ok"])
        self.assertEqual(pair_ok["name"], "Olga")
        self.assertEqual(len(calls_seen), 2, "первый запрос поймал 400 клона ID, второй тут же прошёл с новым уникальным ID")
        self.assertEqual(calls_seen[0], forced_same_id)
        self.assertNotEqual(calls_seen[1], forced_same_id)

        # 5) Истинное подключение к самому себе (тот же IP + тот же порт) по-прежнему отклоняется!
        my_pin = lan_sync.create_pin()
        self.assertGreaterEqual(my_pin["remaining_sec"], 590)
        with self.assertRaises(ValueError) as ctx_self:
            lan_sync.pair_with("127.0.0.1", my_port, my_pin["pin"])
        self.assertIn("самим собой", str(ctx_self.exception).lower())

    @unittest.skipUnless(
        (paths.ROOT.parent / "dist" / "RealtyWatch-1.7-windows-portable.zip").is_file(),
        "архив dist/RealtyWatch-1.7-windows-portable.zip не найден (запуск вне репозитория)",
    )
    def test_v181_real_17_archive_subprocess_discovery_and_pin_pairing(self):
        """Интеграционный тест 1.8.1 с настоящим кодом 1.7 из dist/RealtyWatch-1.7-windows-portable.zip на изолированных портах."""
        import subprocess
        import sys
        import zipfile

        zip17 = paths.ROOT.parent / "dist" / "RealtyWatch-1.7-windows-portable.zip"
        peer17_dir = self.dir / "peer17"
        with zipfile.ZipFile(zip17, "r") as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.startswith("RealtyWatch/"):
                    continue
                rel = info.filename[len("RealtyWatch/"):]
                if not rel:
                    continue
                dst = peer17_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(zf.read(info.filename))

        p17 = _free_port()
        while True:
            p18 = _free_port()
            if abs(p18 - p17) > 6:
                break

        pin_file = self.dir / "peer17_pin.txt"
        worker_py = self.dir / "run_peer17.py"
        worker_py.write_text(
            "\n".join([
                "import logging, sys, time",
                "from pathlib import Path",
                "logging.getLogger().addHandler(logging.NullHandler())",
                f"p17_root = Path({str(peer17_dir)!r})",
                "(p17_root / 'database').mkdir(parents=True, exist_ok=True)",
                "(p17_root / 'data').mkdir(parents=True, exist_ok=True)",
                "(p17_root / 'logs').mkdir(parents=True, exist_ok=True)",
                f"sys.path = [str(p17_root)] + [p for p in sys.path if 'RealtyWatch_Win' not in p or 'runtime' in p]",
                "from app import db, lan_sync, runlog, settings",
                "assert str(Path(lan_sync.__file__).resolve()).startswith(str(p17_root.resolve())), lan_sync.__file__",
                "runlog.LOG_FILE = p17_root / 'logs' / 'app.log'",
                "db.connect().close()",
                "cfg = settings.load()",
                f"cfg['lan'] = {{'enabled': True, 'port': {p17}, 'auto_discovery': True, 'device_id': '', 'device_name': 'Olga-Real-1.7'}}",
                "settings.save(cfg)",
                "lan_sync.service._bind_host = '127.0.0.1'",
                "assert lan_sync.start_if_enabled()",
                "pin_info = lan_sync.create_pin()",
                f"Path({str(pin_file)!r}).write_text(pin_info['pin'], encoding='utf-8')",
                "while True: time.sleep(0.5)",
            ]),
            encoding="utf-8",
        )

        proc17 = subprocess.Popen(
            [sys.executable, str(worker_py)],
            cwd=str(peer17_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def _stop_p17():
            if proc17.poll() is None:
                proc17.terminate()
                try:
                    proc17.wait(timeout=4.0)
                except Exception:
                    proc17.kill()
            for s in (proc17.stdout, proc17.stderr):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

        self.addCleanup(_stop_p17)
        deadline = time.monotonic() + 10.0
        while not pin_file.is_file() and time.monotonic() < deadline:
            if proc17.poll() is not None:
                out, err = proc17.communicate()
                self.fail(f"1.7 завершился досрочно: {out.decode(errors='replace')} / {err.decode(errors='replace')}")
            time.sleep(0.1)
        self.assertTrue(pin_file.is_file(), "1.7 не успел стартовать за 10 с")
        pin_code = pin_file.read_text(encoding="utf-8").strip()

        cfg = settings.load()
        cfg["lan"] = {"enabled": True, "port": p18, "auto_discovery": True, "device_id": "", "device_name": "BB-1.8.1"}
        settings.save(cfg)
        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())

        disc_res = lan_sync.discover_now(target_ports=[p17 + 1])
        found17 = [d for d in disc_res["discovered"] if d["device_name"] == "Olga-Real-1.7"]
        self.assertEqual(len(found17), 1, f"реальный 1.7 должен находиться через зонд с порта {p18 + 1}: {disc_res['discovered']}")
        self.assertTrue(found17[0]["legacy_protocol"])

        pair_res = lan_sync.pair_with("127.0.0.1", p17, pin_code)
        self.assertTrue(pair_res["ok"])
        self.assertEqual(pair_res["name"], "Olga-Real-1.7")


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

        # 1.8.1 (п. 5): открываем второй локальный TCP-порт (имитируя доступный по TCP второй ПК при пустом discovered)
        peer_tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer_tcp_sock.bind(("127.0.0.1", 0))
        peer_tcp_sock.listen(2)
        peer_tcp_port = peer_tcp_sock.getsockname()[1]
        self.addCleanup(peer_tcp_sock.close)

        def _accept_once():
            try:
                conn, _ = peer_tcp_sock.accept()
                conn.recv(512)
                conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                conn.close()
            except OSError:
                pass

        threading.Thread(target=_accept_once, daemon=True).start()

        page.fill("#lanDiagPeerIp", f"127.0.0.1:{peer_tcp_port}")
        page.click("#btnLanRunDiagNow")
        page.wait_for_selector('[data-diag-act="pair_by_ip"]', state="visible", timeout=8000)
        # И в пустом блоке #lanDiscBox тоже появилась кнопка «Подключиться по IP и PIN»!
        page.wait_for_selector("#lanDiscBox .lan-open-manual-pair", state="visible", timeout=5000)
        page.click("#lanDiscBox .lan-open-manual-pair")
        self.assertEqual(page.input_value("#lanPairIp"), "127.0.0.1")
        self.assertEqual(page.input_value("#lanPairPort"), str(peer_tcp_port))
        self.assertTrue(page.evaluate("() => document.querySelector('details.lan-manual-pair')?.open === true"))

        # 1.8.2: проверяем, что уведомления (#toasts) лежат ПОВЕРХ размытого модального фона (#modal), а PIN действует 10 мин
        page.click("#btnLanCopyAddr")
        page.wait_for_selector("#toasts .toast", state="visible", timeout=5000)
        z_check = page.evaluate("""() => {
            const toastEl = document.querySelector('#toasts .toast');
            const rect = toastEl.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            const zToast = parseInt(getComputedStyle(document.getElementById('toasts')).zIndex || '0', 10);
            const zModal = parseInt(getComputedStyle(document.getElementById('modal')).zIndex || '0', 10);
            return { zToast, zModal, toastOnTop: !!(hit && toastEl.contains(hit)) };
        }""")
        self.assertGreater(z_check["zToast"], z_check["zModal"], f"уведомления должны быть выше модального окна: {z_check}")
        self.assertTrue(z_check["toastOnTop"], f"тост должен быть кликабелен поверх модального окна: {z_check}")
        page.click("#btnLanGenPin")
        page.wait_for_selector("#lanPinBox:not(.hidden)", timeout=5000)
        self.assertIn("мин", page.text_content("#lanPinTtl"))

        self.assertEqual(js_errors, [], f"ошибки JS в браузере: {js_errors}")


if __name__ == "__main__":
    unittest.main()
