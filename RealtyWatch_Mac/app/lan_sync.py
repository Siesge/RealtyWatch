"""Обмен и синхронизация по локальной сети (LAN): сервер /sync/* на отдельном порту,
UDP-обнаружение устройств рядом, сопряжение по одноразовому PIN-коду,
передача карточек, Избранного и полной базы с сохранением личных отметок."""
import base64
import ctypes
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import logging
import ntpath
import os
import re
import secrets
import select
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import db, dedupe, exchange, history, images, paths, runlog, runner, settings, transfer

log = logging.getLogger(__name__)

_DISCOVERY_MAGIC = "RW_DISCOVER_V1"
_PROBE_MAGIC = "RW_PROBE_V1"
_DB_SYNC_FORMAT = "realtywatch-db-sync"
_DB_SYNC_VERSION = 1
_SOURCES = ("cian", "avito", "yandex")
_VALID_IMG_EXTS = ("webp", "jpg", "jpeg", "png", "avif")

DEFAULT_LIVE_CFG = {
    "accept_spotlight": True,
    "fav_send": "off",       # "off" | "add_only" | "add_remove"
    "fav_recv": "ask",       # "ask" | "auto_add" | "auto_sync"
    "live_notes": False,
    "outbox_enabled": True,
    "live_merges": False,
}


def _normalize_live_cfg(raw) -> dict:
    """Нормализует per-peer настройки Live-синхронизации с безопасными значениями по умолчанию."""
    src = _parse_json(raw, {}) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    cfg = dict(DEFAULT_LIVE_CFG)
    if "accept_spotlight" in src:
        cfg["accept_spotlight"] = bool(src["accept_spotlight"])
    if src.get("fav_send") in ("off", "add_only", "add_remove"):
        cfg["fav_send"] = src["fav_send"]
    if src.get("fav_recv") in ("ask", "auto_add", "auto_sync"):
        cfg["fav_recv"] = src["fav_recv"]
    if "live_notes" in src:
        cfg["live_notes"] = bool(src["live_notes"])
    if "outbox_enabled" in src:
        cfg["outbox_enabled"] = bool(src["outbox_enabled"])
    if "live_merges" in src:
        cfg["live_merges"] = bool(src["live_merges"])
    return cfg

PIN_TTL_SEC = 600  # 10 минут — чтобы спокойно перейти к другому компьютеру и ввести PIN без спешки
PIN_MAX_TRIES = 5
DISCOVERY_TTL_SEC = 45.0
DISCOVERY_INTERVAL_SEC = 4.0
QUIET_PEER_REPROBE_SEC = 16.0
SUBNET_SWEEP_BATCH = 32
SUBNET_SWEEP_PAUSE_SEC = 0.025
HEARTBEAT_INTERVAL_SEC = 8.0
HEARTBEAT_TIMEOUT_SEC = 2.0
DB_SESSION_TTL_SEC = 600.0
MAX_CARDS_BYTES = 250 * 1024 * 1024
MAX_MARKS_BYTES = 64 * 1024 * 1024
MAX_DB_BYTES = 1024 * 1024 * 1024  # 1 ГБ для полной базы и дельты фото
_MARKS_SYNC_FORMAT = "realtywatch-marks-v1"
_MARKS_SYNC_VERSION = 1
MARK_CATEGORIES = ("likes", "dislikes", "notes", "merges")
DISCONNECT_MSG = "Связь с устройством прервалась во время передачи — данные не повреждены, повторите после восстановления сети"


class LanBusyError(RuntimeError):
    """Операция невозможна прямо сейчас, пока идёт сбор объявлений."""


class LanAuthError(RuntimeError):
    """Ошибка авторизации LAN-запроса."""


class LanNotFoundError(LookupError):
    """Запрашиваемый объект (запрос/устройство) не найден."""


def _now_ts():
    return db.now()


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _clean_ip_str(host: str) -> str:
    h = (host or "").strip()
    if h.startswith("[") and "]" in h:
        h = h[1:h.index("]")]
    if "%" in h:
        h = h.split("%", 1)[0]
    return h


def _is_lan_ip(host: str) -> bool:
    """True только для адресов локальной сети, loopback и link-local (публичные IP — False)."""
    h = _clean_ip_str(host)
    if not h:
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_multicast:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


_LAN_LOG_RATE_LOCK = threading.Lock()
_LAN_LOG_RATES: dict[str, float] = {}


def _lan_log(msg: str, level: int = logging.INFO, *, rate_key: str = "", min_interval: float = 0.0):
    """Пишет диагностические события и сетевые ошибки в ротируемый logs/lan.log с защитой от спама."""
    if rate_key and min_interval > 0:
        now_m = time.monotonic()
        with _LAN_LOG_RATE_LOCK:
            prev = _LAN_LOG_RATES.get(rate_key, 0.0)
            if (now_m - prev) < min_interval:
                return
            _LAN_LOG_RATES[rate_key] = now_m
            if len(_LAN_LOG_RATES) > 500:
                cutoff = now_m - 600.0
                for k in [k for k, ts in _LAN_LOG_RATES.items() if ts < cutoff]:
                    _LAN_LOG_RATES.pop(k, None)
    try:
        lg = runlog.setup_lan_logging()
        lg.log(level, msg)
    except Exception:
        pass


_VPN_SUBSTR_HINTS = (
    "vpn", "amnezia", "wireguard", "wintun", "openvpn", "tailscale", "zerotier",
    "hamachi", "radmin", "outline", "nordlynx", "nordvpn", "protonvpn", "sing-box",
    "nekoray", "clash", "v2ray", "xray", "cloudflare warp", "anyconnect", "fortissl",
    "globalprotect", "kaspersky vpn", "hidemy", "surfshark", "expressvpn",
)
_VIRT_ONLY_SUBSTR_HINTS = (
    "vmware", "vmnet", "virtualbox", "vbox", "hyper-v", "vethernet", "wsl", "docker",
    "loopback", "wi-fi direct virtual", "hosted network", "bluetooth", "npcap",
    "teredo", "isatap", "6to4", "virtual adapter", "pseudo-interface", "virtual ethernet",
)
_SHORT_TUN_RE = re.compile(r"\b(?:wg\d*|tap\d*|u?tun\d*|ipsec\d*|ppp\d*)\b", re.IGNORECASE)
_MAC_VIRT_IF_RE = re.compile(
    r"^(?:lo|awdl|llw|bridge|vmnet|vboxnet|vmenet|gif|stf|anpi|ap|pktap|feth|xhc)\d*$",
    re.IGNORECASE,
)
_MAC_PHYS_EN_RE = re.compile(r"^(?:.*\()?en\d+\)?$", re.IGNORECASE)


def is_virtual_or_vpn_adapter(name: str, description: str = "", if_type: int = 0) -> tuple[bool, bool]:
    """Возвращает (is_virtual, is_vpn) для сетевого адаптера (Windows и macOS) по имени, описанию и типу IANA."""
    raw_name = (name or "").strip()
    combined = f"{raw_name} {description or ''}".lower()
    is_mac_virt_only = bool(_MAC_VIRT_IF_RE.match(raw_name))
    is_vpn = (
        int(if_type or 0) in (23, 131)  # PPP, TUNNEL
        or any(h in combined for h in _VPN_SUBSTR_HINTS)
        or bool(_SHORT_TUN_RE.search(combined))
    )
    if int(if_type or 0) == 53 and not is_mac_virt_only and not any(h in combined for h in _VIRT_ONLY_SUBSTR_HINTS):
        # IF_TYPE_PROP_VIRTUAL (53) используется Wintun / AmneziaVPN / WireGuard
        is_vpn = True
    is_virt = (
        is_vpn
        or is_mac_virt_only
        or int(if_type or 0) in (24, 53)  # LOOPBACK, PROP_VIRTUAL
        or any(h in combined for h in _VIRT_ONLY_SUBSTR_HINTS)
    )
    return is_virt, is_vpn


def compute_subnet_broadcast(ip_str: str, prefix: int) -> str | None:
    """Считает направленный broadcast-адрес подсети по реальной маске (префиксу), исключая /31 и /32."""
    try:
        plen = int(prefix)
    except (TypeError, ValueError):
        return None
    if plen <= 0 or plen >= 31:
        return None
    try:
        iface = ipaddress.IPv4Interface(f"{(ip_str or '').strip()}/{plen}")
    except ValueError:
        return None
    bcast = str(iface.network.broadcast_address)
    return bcast if bcast and bcast != ip_str else None


def compute_subnet_unicast_hosts(ip_str: str, prefix: int, exclude_ips: set[str] | None = None) -> list[str]:
    """Возвращает список соседей в подсети адаптера для поадресного unicast-обхода:
    до /22 включительно — вся подсеть, для более крупных сетей (/21..8) — только своя /24;
    исключаются адрес сети, broadcast, собственный IP и exclude_ips."""
    clean = _clean_ip_str(ip_str)
    if not _is_lan_ip(clean):
        return []
    try:
        ip_obj = ipaddress.IPv4Address(clean)
    except ValueError:
        return []
    if ip_obj.is_loopback or ip_obj.is_link_local:
        return []
    try:
        plen = int(prefix)
    except (TypeError, ValueError):
        plen = 24
    if plen >= 31 or plen <= 0:
        return []
    eff_prefix = plen if plen >= 22 else 24
    try:
        net = ipaddress.IPv4Network(f"{clean}/{eff_prefix}", strict=False)
    except ValueError:
        return []
    skip = {clean}
    if exclude_ips:
        skip.update(_clean_ip_str(x) for x in exclude_ips if x)
    out: list[str] = []
    for host in net.hosts():
        hs = str(host)
        if hs not in skip:
            out.append(hs)
    return out


def _disable_udp_connreset(sock: socket.socket) -> None:
    """Отключает на Windows генерацию ConnectionResetError (WinError 10054) на UDP-сокете
    после прихода ICMP Port Unreachable при поадресном опросе закрытых портов подсети."""
    if os.name != "nt":
        return
    try:
        ws2 = ctypes.windll.ws2_32
        in_buf = ctypes.c_ulong(0)
        bytes_ret = ctypes.c_ulong(0)
        ws2.WSAIoctl(
            ctypes.c_size_t(sock.fileno()),
            ctypes.c_ulong(0x9800000C),  # SIO_UDP_CONNRESET
            ctypes.byref(in_buf),
            ctypes.c_ulong(ctypes.sizeof(in_buf)),
            None,
            ctypes.c_ulong(0),
            ctypes.byref(bytes_ret),
            None,
            None,
        )
    except Exception:
        pass


CURRENT_LAN_APP_VERSION = "1.8.2"
_LAN_VER_RE = re.compile(r"\bv?(\d+(?:\.\d+)+)\b")


def _local_platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return sys.platform or "desktop"


def current_lan_app_version(app_root: Path | None = None) -> str:
    """Возвращает строку текущей версии приложения из version.txt (с фолбэком '1.8.2')."""
    vf = Path(app_root or paths.ROOT) / "version.txt"
    try:
        first = vf.read_text(encoding="utf-8-sig").strip().splitlines()[0]
    except (OSError, IndexError):
        return CURRENT_LAN_APP_VERSION
    head = first.split(",")[0].strip()
    if head.lower().startswith("realtywatch"):
        head = head[len("realtywatch"):].strip()
    m = _LAN_VER_RE.search(head)
    return m.group(1) if m else (head or CURRENT_LAN_APP_VERSION)


def _raw_win32_adapters() -> list[dict]:
    """Читает список сетевых адаптеров, их IPv4/маски и шлюзы через Win32 GetAdaptersAddresses (< 1 мс)."""
    if os.name != "nt":
        return []
    try:
        import ctypes.wintypes as wt

        class SOCKET_ADDRESS(ctypes.Structure):
            _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]

        class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
            pass

        IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
            ("Length", wt.ULONG),
            ("Flags", wt.DWORD),
            ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
            ("Address", SOCKET_ADDRESS),
            ("PrefixOrigin", ctypes.c_int),
            ("SuffixOrigin", ctypes.c_int),
            ("DadState", ctypes.c_int),
            ("ValidLifetime", wt.ULONG),
            ("PreferredLifetime", wt.ULONG),
            ("LeaseLifetime", wt.ULONG),
            ("OnLinkPrefixLength", ctypes.c_ubyte),
        ]

        class IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
            pass

        IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
            ("Length", wt.ULONG),
            ("Reserved", wt.DWORD),
            ("Next", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
            ("Address", SOCKET_ADDRESS),
        ]

        class IP_ADAPTER_ADDRESSES(ctypes.Structure):
            pass

        IP_ADAPTER_ADDRESSES._fields_ = [
            ("Length", wt.ULONG),
            ("IfIndex", wt.DWORD),
            ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
            ("AdapterName", ctypes.c_char_p),
            ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
            ("FirstAnycastAddress", ctypes.c_void_p),
            ("FirstMulticastAddress", ctypes.c_void_p),
            ("FirstDnsServerAddress", ctypes.c_void_p),
            ("DnsSuffix", wt.LPWSTR),
            ("Description", wt.LPWSTR),
            ("FriendlyName", wt.LPWSTR),
            ("PhysicalAddress", ctypes.c_ubyte * 8),
            ("PhysicalAddressLength", wt.DWORD),
            ("Flags", wt.DWORD),
            ("Mtu", wt.DWORD),
            ("IfType", wt.DWORD),
            ("OperStatus", ctypes.c_int),
            ("Ipv6IfIndex", wt.DWORD),
            ("ZoneIndices", wt.DWORD * 16),
            ("FirstPrefix", ctypes.c_void_p),
            ("TransmitLinkSpeed", ctypes.c_uint64),
            ("ReceiveLinkSpeed", ctypes.c_uint64),
            ("FirstWinsServerAddress", ctypes.c_void_p),
            ("FirstGatewayAddress", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
        ]

        def _sock_ipv4(sa) -> str | None:
            if not sa.lpSockaddr or sa.iSockaddrLength < 8:
                return None
            raw = ctypes.string_at(sa.lpSockaddr, sa.iSockaddrLength)
            fam = struct.unpack_from("<H", raw, 0)[0]
            return socket.inet_ntoa(raw[4:8]) if fam == socket.AF_INET else None

        iphlp = ctypes.windll.iphlpapi
        flags = 0x0010 | 0x0080  # GAA_FLAG_INCLUDE_PREFIX | GAA_FLAG_INCLUDE_GATEWAYS
        sz = wt.ULONG(65536)
        buf = ctypes.create_string_buffer(sz.value)
        rc = iphlp.GetAdaptersAddresses(socket.AF_INET, flags, None, ctypes.byref(buf), ctypes.byref(sz))
        if rc == 111:  # ERROR_BUFFER_OVERFLOW
            buf = ctypes.create_string_buffer(sz.value)
            rc = iphlp.GetAdaptersAddresses(socket.AF_INET, flags, None, ctypes.byref(buf), ctypes.byref(sz))
        if rc != 0:
            return []

        out: list[dict] = []
        cur = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
        while cur:
            a = cur.contents
            addrs: list[tuple[str, int]] = []
            u = a.FirstUnicastAddress
            while u:
                ip_s = _sock_ipv4(u.contents.Address)
                if ip_s:
                    addrs.append((ip_s, int(u.contents.OnLinkPrefixLength or 24)))
                u = u.contents.Next
            gws: list[str] = []
            g = a.FirstGatewayAddress
            while g:
                gw_s = _sock_ipv4(g.contents.Address)
                if gw_s:
                    gws.append(gw_s)
                g = g.contents.Next
            out.append({
                "if_index": int(a.IfIndex),
                "oper_status": int(a.OperStatus),
                "if_type": int(a.IfType),
                "name": str(a.FriendlyName or ""),
                "description": str(a.Description or ""),
                "addresses": addrs,
                "gateways": gws,
            })
            cur = a.Next
        return out
    except Exception as e:
        _lan_log(f"Ошибка чтения адаптеров GetAdaptersAddresses: {e}", logging.WARNING, rate_key="gaa_err", min_interval=60.0)
        return []


_IFCONFIG_HDR_RE = re.compile(r"^([a-zA-Z0-9_.-]+):\s+flags=\d+<([^>]*)>")
_IFCONFIG_INET_RE = re.compile(
    r"\binet\s+(\d+\.\d+\.\d+\.\d+)\s+(?:-->\s+\S+\s+)?netmask\s+(0x[0-9a-fA-F]{8}|\d+\.\d+\.\d+\.\d+)"
)
_IFCONFIG_STATUS_RE = re.compile(r"^\s*status:\s*(\S+)", re.IGNORECASE)
_ROUTE_IFACE_RE = re.compile(r"^\s*interface:\s*(\S+)", re.MULTILINE | re.IGNORECASE)


def _netmask_to_prefix(mask_str: str) -> int:
    s = (mask_str or "").strip()
    if not s:
        return 24
    try:
        if s.lower().startswith("0x"):
            val = int(s, 16) & 0xFFFFFFFF
            dotted = socket.inet_ntoa(struct.pack("!I", val))
            return ipaddress.IPv4Network(f"0.0.0.0/{dotted}", strict=False).prefixlen
        return ipaddress.IPv4Network(f"0.0.0.0/{s}", strict=False).prefixlen
    except ValueError:
        if s.lower().startswith("0x"):
            try:
                return bin(int(s, 16) & 0xFFFFFFFF).count("1")
            except ValueError:
                pass
        return 24


def parse_darwin_hardware_ports(text: str) -> dict[str, str]:
    """Разбирает вывод `networksetup -listallhardwareports` в словарь {device: port_name} (напр. {'en0': 'Wi-Fi'})."""
    ports: dict[str, str] = {}
    cur_port = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line.lower().startswith("hardware port:"):
            cur_port = line.split(":", 1)[1].strip()
        elif line.lower().startswith("device:") and cur_port:
            dev = line.split(":", 1)[1].strip()
            if dev:
                ports[dev] = cur_port
            cur_port = ""
    return ports


def parse_darwin_default_gateways(netstat_text: str) -> dict[str, list[str]]:
    """Разбирает `netstat -rn -f inet` на macOS и возвращает {netif: [gateway_ipv4, ...]}."""
    gws: dict[str, list[str]] = {}
    for raw_line in (netstat_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith(("routing", "internet", "destination")):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        dest, gw, _flags, netif = parts[0], parts[1], parts[2], parts[3]
        if dest not in ("default", "0.0.0.0", "0.0.0.0/0"):
            continue
        try:
            gw_ip = ipaddress.ip_address(gw)
        except ValueError:
            continue
        if gw_ip.version == 4 and not gw_ip.is_unspecified and not gw_ip.is_loopback:
            lst = gws.setdefault(netif, [])
            if gw not in lst:
                lst.append(gw)
    return gws


def _darwin_if_index(ifname: str) -> int:
    name = (ifname or "").strip()
    if not name:
        return 0
    fn = getattr(socket, "if_nametoindex", None)
    if callable(fn):
        try:
            idx = int(fn(name))
            if idx > 0:
                return idx
        except OSError:
            pass
    # Детерминированный положительный индекс для синтетических имён в юнит-тестах
    return (zlib.crc32(name.encode("utf-8")) & 0x7FFF) + 1000


def parse_darwin_ifconfig_and_routes(
    ifconfig_text: str,
    netstat_text: str = "",
    hwports_text: str = "",
    if_index_fn=None,
) -> list[dict]:
    """Разбирает `ifconfig` + `netstat -rn -f inet` на macOS в единый формат адаптеров для local_ipv4s и VPN-детекта."""
    idx_resolver = _darwin_if_index if if_index_fn is None else if_index_fn
    hw_map = parse_darwin_hardware_ports(hwports_text)
    gw_map = parse_darwin_default_gateways(netstat_text)

    blocks: list[dict] = []
    cur: dict | None = None

    for line in (ifconfig_text or "").splitlines():
        m_hdr = _IFCONFIG_HDR_RE.match(line)
        if m_hdr:
            if cur is not None:
                blocks.append(cur)
            ifname = m_hdr.group(1).strip()
            flags = {f.strip().upper() for f in m_hdr.group(2).split(",") if f.strip()}
            cur = {
                "ifname": ifname,
                "flags": flags,
                "status": None,
                "addresses": [],
            }
            continue
        if cur is None:
            continue
        m_st = _IFCONFIG_STATUS_RE.match(line)
        if m_st:
            cur["status"] = m_st.group(1).strip().lower()
            continue
        m_in = _IFCONFIG_INET_RE.search(line)
        if m_in:
            ip_s = m_in.group(1).strip()
            pref = _netmask_to_prefix(m_in.group(2))
            cur["addresses"].append((ip_s, pref))

    if cur is not None:
        blocks.append(cur)

    out: list[dict] = []
    for b in blocks:
        ifname = b["ifname"]
        flags = b["flags"]
        status = b["status"]
        is_up = ("UP" in flags) and (status != "inactive")
        oper_status = 1 if is_up else 2

        hw_port = hw_map.get(ifname, "")
        hw_low = hw_port.lower()
        low = ifname.lower()

        if low.startswith("lo"):
            if_type = 24
        elif _SHORT_TUN_RE.search(low):
            if_type = 131
        elif _MAC_VIRT_IF_RE.match(low):
            if_type = 53
        elif any(w in hw_low for w in ("wi-fi", "wifi", "airport", "wireless")):
            if_type = 71
        elif low.startswith("en") and low[2:].isdigit():
            if_type = 6
        else:
            if_type = 0

        if hw_port and low.startswith("en") and low[2:].isdigit():
            disp_name = f"{hw_port} ({ifname})" if ifname.lower() not in hw_low else hw_port
            descr = f"{hw_port} ({ifname})"
        else:
            disp_name = ifname
            descr = hw_port

        out.append({
            "if_index": int(idx_resolver(ifname)),
            "bsd_name": ifname,
            "oper_status": oper_status,
            "if_type": if_type,
            "name": disp_name,
            "description": descr,
            "addresses": list(b["addresses"]),
            "gateways": list(gw_map.get(ifname, ())),
        })
    return out


def parse_darwin_route_get_interface(route_output: str) -> str:
    """Извлекает имя выходного интерфейса (напр. 'en0' или 'utun4') из вывода `route -n get <ip>`."""
    m = _ROUTE_IFACE_RE.search(route_output or "")
    return m.group(1).strip() if m else ""


def _run_darwin_cmd(args: list[str], timeout: float = 2.0) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.stdout or "" if proc.returncode == 0 else (proc.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return ""


_DARWIN_HWPORTS_CACHE: tuple[float, str] = (0.0, "")


def _get_darwin_hwports_cached() -> str:
    global _DARWIN_HWPORTS_CACHE
    now_m = time.monotonic()
    if _DARWIN_HWPORTS_CACHE[1] and (now_m - _DARWIN_HWPORTS_CACHE[0]) < 120.0:
        return _DARWIN_HWPORTS_CACHE[1]
    for bin_p in ("/usr/sbin/networksetup", "networksetup"):
        out = _run_darwin_cmd([bin_p, "-listallhardwareports"], timeout=2.0)
        if out:
            _DARWIN_HWPORTS_CACHE = (now_m, out)
            return out
    return ""


def _raw_darwin_adapters() -> list[dict]:
    """Читает список интерфейсов macOS через `ifconfig` + `netstat -rn -f inet`."""
    if sys.platform != "darwin":
        return []
    ifconfig_out = _run_darwin_cmd(["/sbin/ifconfig"], timeout=2.5) or _run_darwin_cmd(["ifconfig"], timeout=2.5)
    if not ifconfig_out:
        return []
    netstat_out = (
        _run_darwin_cmd(["/usr/sbin/netstat", "-rn", "-f", "inet"], timeout=2.5)
        or _run_darwin_cmd(["netstat", "-rn", "-f", "inet"], timeout=2.5)
    )
    hw_out = _get_darwin_hwports_cached()
    return parse_darwin_ifconfig_and_routes(ifconfig_out, netstat_out, hw_out)


def _raw_system_adapters() -> list[dict]:
    if os.name == "nt":
        return _raw_win32_adapters()
    if sys.platform == "darwin":
        return _raw_darwin_adapters()
    return []


def _best_route_if_index(dest_ip: str) -> int | None:
    """Определяет индекс интерфейса, через который ОС реально отправит пакет на dest_ip (Windows и macOS)."""
    target = (dest_ip or "").strip()
    if not target:
        return None
    if os.name == "nt":
        try:
            import ctypes.wintypes as wt

            packed = socket.inet_aton(target)
            sa = struct.pack("<HH4s8x", socket.AF_INET, 0, packed)
            idx = wt.DWORD(0)
            rc = ctypes.windll.iphlpapi.GetBestInterfaceEx(sa, ctypes.byref(idx))
            return int(idx.value) if rc == 0 else None
        except Exception:
            return None
    if sys.platform == "darwin":
        out = (
            _run_darwin_cmd(["/sbin/route", "-n", "get", target], timeout=1.8)
            or _run_darwin_cmd(["route", "-n", "get", target], timeout=1.8)
        )
        ifname = parse_darwin_route_get_interface(out)
        return _darwin_if_index(ifname) if ifname else None
    return None


def _classify_adapter_kind(name: str, description: str, if_type: int) -> str:
    raw_name = (name or "").strip()
    combined = f"{raw_name} {description or ''}".lower()
    if int(if_type or 0) == 71 or any(w in combined for w in ("wi-fi", "wifi", "airport", "wireless", "wlan", "802.11", "беспроводн")):
        return "wifi"
    if int(if_type or 0) == 6 or _MAC_PHYS_EN_RE.match(raw_name) or any(w in combined for w in ("ethernet", "thunderbolt", "gbe", "gigabit", "realtek pcie", "intel(r) ethernet", "локальн")):
        return "ethernet"
    return "lan"


def local_ipv4s(raw_adapters: list[dict] | None = None) -> list[dict]:
    """Возвращает физические LAN-интерфейсы машины [{ip, prefix, adapter, kind, ...}]:
    - исключает VPN и виртуальные адаптеры (Amnezia/WireGuard/Wintun/TAP/VMware/Hyper-V/WSL/Docker и др.) и /32;
    - первым ставит физический Wi-Fi/Ethernet, у которого есть шлюз по умолчанию."""
    adapters = _raw_system_adapters() if raw_adapters is None else list(raw_adapters)
    candidates: list[tuple[tuple, dict]] = []
    seen_ips: set[str] = set()

    for ad in adapters:
        if int(ad.get("oper_status") or 0) != 1:  # IfOperStatusUp == 1
            continue
        name = str(ad.get("name") or "").strip()
        descr = str(ad.get("description") or "").strip()
        if_type = int(ad.get("if_type") or 0)
        if_index = int(ad.get("if_index") or 0)
        is_virt, _ = is_virtual_or_vpn_adapter(name, descr, if_type)
        if is_virt:
            continue

        real_gws = [
            str(g).strip()
            for g in (ad.get("gateways") or [])
            if g and str(g).strip() not in ("", "0.0.0.0") and not str(g).strip().startswith("127.")
        ]
        has_gw = bool(real_gws)
        kind = _classify_adapter_kind(name, descr, if_type)

        for ip_raw, prefix_raw in (ad.get("addresses") or []):
            ip_str = str(ip_raw or "").strip()
            if not ip_str or ip_str in seen_ips:
                continue
            try:
                plen = int(prefix_raw)
                ip_obj = ipaddress.ip_address(ip_str)
            except (TypeError, ValueError):
                continue
            if ip_obj.version != 4 or plen >= 32 or plen <= 0:
                continue
            if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast or not ip_obj.is_private:
                continue
            seen_ips.add(ip_str)
            bcast = compute_subnet_broadcast(ip_str, plen) or ""
            item = {
                "ip": ip_str,
                "prefix": plen,
                "adapter": name or "Сеть",
                "kind": kind,
                "description": descr,
                "broadcast": bcast,
                "gateway": real_gws[0] if real_gws else "",
                "has_gateway": has_gw,
                "if_index": if_index,
            }
            sort_key = (
                0 if has_gw else 1,
                0 if kind in ("wifi", "ethernet") else 1,
                if_index,
                ip_str,
            )
            candidates.append((sort_key, item))

    if candidates:
        candidates.sort(key=lambda pair: pair[0])
        return [item for _, item in candidates]

    # Запасной кроссплатформенный путь (только если GetAdaptersAddresses недоступен и список не передан явно)
    if raw_adapters is not None or os.name == "nt" or (sys.platform == "darwin" and adapters):
        return []
    fallback_out: list[dict] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip_s = str(info[4][0]).strip()
            if ip_s in seen_ips:
                continue
            ip_o = ipaddress.ip_address(ip_s)
            if ip_o.version == 4 and ip_o.is_private and not ip_o.is_loopback and not ip_o.is_link_local:
                seen_ips.add(ip_s)
                fallback_out.append({
                    "ip": ip_s,
                    "prefix": 24,
                    "adapter": "LAN",
                    "kind": "lan",
                    "description": "",
                    "broadcast": compute_subnet_broadcast(ip_s, 24) or "",
                    "gateway": "",
                    "has_gateway": False,
                    "if_index": 0,
                })
    except OSError:
        pass
    return fallback_out


def local_ip_strings(raw_adapters: list[dict] | None = None) -> list[str]:
    """Список только IP-строк физических LAN-адаптеров в том же порядке приоритета."""
    return [x["ip"] for x in local_ipv4s(raw_adapters)]


def detect_vpn_route_hijack(
    raw_adapters: list[dict] | None = None,
    route_resolver=None,
    extra_targets=(),
) -> dict:
    """Определяет, перехватывает ли VPN-туннель маршруты к локальной подсети физического адаптера."""
    adapters = _raw_system_adapters() if raw_adapters is None else list(raw_adapters)
    resolver = _best_route_if_index if route_resolver is None else route_resolver
    phys_list = local_ipv4s(adapters)
    if not phys_list:
        return {"hijacked": False, "vpn_adapter": "", "intercepted_targets": [], "message": ""}

    by_idx = {int(a.get("if_index") or 0): a for a in adapters}
    primary = phys_list[0]
    phys_idx = int(primary.get("if_index") or 0)
    if not phys_idx:
        return {"hijacked": False, "vpn_adapter": "", "intercepted_targets": [], "message": ""}

    try:
        net_obj = ipaddress.IPv4Interface(f"{primary['ip']}/{primary['prefix']}").network
        subnet_str = str(net_obj)
    except ValueError:
        net_obj = None
        subnet_str = f"{primary['ip']}/{primary['prefix']}"

    probe_ip = primary.get("gateway") or ""
    if not probe_ip and net_obj is not None:
        for host_ip in net_obj.hosts():
            hs = str(host_ip)
            if hs != primary["ip"]:
                probe_ip = hs
                break

    subnet_targets: list[str] = []
    for t in [probe_ip, primary.get("broadcast") or ""]:
        if t and t not in subnet_targets:
            subnet_targets.append(t)
    for et in extra_targets or ():
        et_s = _clean_ip_str(str(et or ""))
        if et_s and _is_lan_ip(et_s) and not et_s.startswith("127.") and et_s not in subnet_targets:
            subnet_targets.append(et_s)

    intercepted: list[str] = []
    hijack_adapter_name = ""
    hijack_descr = ""

    for target in subnet_targets:
        out_idx = resolver(target)
        if out_idx is None or int(out_idx) == phys_idx:
            continue
        out_ad = by_idx.get(int(out_idx)) or {}
        out_name = str(out_ad.get("name") or f"ifIndex={out_idx}")
        out_descr = str(out_ad.get("description") or "")
        is_virt, is_vpn = is_virtual_or_vpn_adapter(out_name, out_descr, int(out_ad.get("if_type") or 0))
        if is_vpn or is_virt or out_ad:
            intercepted.append(target)
            if not hijack_adapter_name:
                hijack_adapter_name = out_name
                hijack_descr = out_descr

    if not intercepted:
        return {
            "hijacked": False,
            "vpn_adapter": "",
            "physical_adapter": primary["adapter"],
            "physical_ip": primary["ip"],
            "subnet": subnet_str,
            "intercepted_targets": [],
            "message": "",
        }

    # Если глобальный 255.255.255.255 тоже уходит в тот же туннель — добавим в список перехваченных для наглядности
    bcast_global_idx = resolver("255.255.255.255")
    if bcast_global_idx is not None and int(bcast_global_idx) != phys_idx:
        intercepted.append("255.255.255.255")

    msg = (
        f"VPN «{hijack_adapter_name}» перехватывает локальную сеть — второй компьютер недоступен. "
        f"Включите в VPN раздельное туннелирование: исключите 192.168.0.0/16 "
        f"(и 10.0.0.0/8, 172.16.0.0/12) или временно отключите VPN."
    )
    return {
        "hijacked": True,
        "vpn_adapter": hijack_adapter_name,
        "vpn_description": hijack_descr,
        "physical_adapter": primary["adapter"],
        "physical_ip": primary["ip"],
        "subnet": subnet_str,
        "intercepted_targets": intercepted,
        "message": msg,
    }


def _norm_win_path(p: str) -> str:
    raw = os.path.expandvars(str(p or "").strip().strip('"'))
    if not raw:
        return ""
    try:
        return ntpath.normcase(ntpath.normpath(raw))
    except Exception:
        return raw.replace("/", "\\").lower()


def _firewall_target_programs(app_root: Path | None = None) -> tuple[str, list[str]]:
    """Возвращает (основной_путь_pythonw, все_целевые_пути_python_для_правил)."""
    root = Path(app_root or paths.ROOT)
    pyw = root / "runtime" / "python" / "pythonw.exe"
    py = root / "runtime" / "python" / "python.exe"
    targets: list[str] = []
    if pyw.is_file():
        targets.append(str(pyw.resolve()))
    if py.is_file():
        targets.append(str(py.resolve()))
    cur_exe = Path(sys.executable)
    if cur_exe.is_file():
        cur_s = str(cur_exe.resolve())
        if cur_s not in targets:
            targets.append(cur_s)
    primary = targets[0] if targets else str(pyw)
    if primary not in targets:
        targets.insert(0, primary)
    return primary, targets


def _port_matches(rule_port, target_port: int) -> bool:
    if rule_port is None:
        return True
    if isinstance(rule_port, list):
        return any(_port_matches(x, target_port) for x in rule_port)
    s = str(rule_port).strip().lower()
    if not s or s == "any":
        return True
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            try:
                if int(lo_s) <= int(target_port) <= int(hi_s):
                    return True
            except ValueError:
                pass
        else:
            try:
                if int(part) == int(target_port):
                    return True
            except ValueError:
                pass
    return False


def _profile_covers_priv_pub(profile_val) -> tuple[bool, bool]:
    """Возвращает (covers_private, covers_public) для поля Profile из Get-NetFirewallRule."""
    s = str(profile_val or "").strip().lower()
    if not s or "any" in s:
        return True, True
    if s.isdigit():
        mask = int(s)
        if mask == 0 or mask >= 0x7FFFFFFF or mask == 65535:
            return True, True
        return bool(mask & 2), bool(mask & 4)
    return ("private" in s), ("public" in s)


def parse_firewall_and_profiles(
    ps_data: dict | None,
    primary_program: str,
    tcp_port: int,
    udp_port: int,
    phys_adapters: list[dict] | None = None,
) -> dict:
    """Разбирает JSON от Get-NetConnectionProfile + Get-NetFirewallApplicationFilter (чистая функция для тестов)."""
    data = ps_data if isinstance(ps_data, dict) else {}
    raw_profiles = data.get("profiles") or []
    if isinstance(raw_profiles, dict):
        raw_profiles = [raw_profiles]
    raw_rules = data.get("rules") or []
    if isinstance(raw_rules, dict):
        raw_rules = [raw_rules]

    phys_indices = {int(a.get("if_index") or 0) for a in (phys_adapters or []) if a.get("if_index")}
    phys_names = {str(a.get("adapter") or "").strip().lower() for a in (phys_adapters or []) if a.get("adapter")}

    cat_map = {"0": "Public", "1": "Private", "2": "DomainAuthenticated"}
    parsed_profiles: list[dict] = []
    public_profiles: list[dict] = []
    for pr in raw_profiles:
        if not isinstance(pr, dict):
            continue
        alias = str(pr.get("InterfaceAlias") or "").strip()
        net_name = str(pr.get("Name") or alias).strip()
        if_idx = int(pr.get("InterfaceIndex") or 0)
        cat_raw = str(pr.get("Category") if pr.get("Category") is not None else pr.get("NetworkCategory") or "").strip()
        cat = cat_map.get(cat_raw, cat_raw)
        is_virt, _ = is_virtual_or_vpn_adapter(alias, "", 0)
        if is_virt:
            continue
        if (phys_indices or phys_names) and (if_idx not in phys_indices and alias.lower() not in phys_names):
            continue
        item = {"name": net_name, "adapter": alias, "if_index": if_idx, "category": cat}
        parsed_profiles.append(item)
        if cat.lower() == "public":
            public_profiles.append(item)

    target_norm = _norm_win_path(primary_program)
    tcp_priv = tcp_pub = False
    udp_priv = udp_pub = False
    block_rules: list[dict] = []

    for r in raw_rules:
        if not isinstance(r, dict):
            continue
        prog_norm = _norm_win_path(r.get("Program") or "")
        if not prog_norm or prog_norm != target_norm:
            continue
        enabled_s = str(r.get("Enabled") or "").strip().lower()
        if enabled_s not in ("true", "1"):
            continue
        dir_s = str(r.get("Direction") or "").strip().lower()
        if dir_s not in ("inbound", "1"):
            continue
        act_s = str(r.get("Action") or "").strip().lower()
        cov_priv, cov_pub = _profile_covers_priv_pub(r.get("Profile"))
        proto_s = str(r.get("Protocol") or "Any").strip().lower()
        lport = r.get("LocalPort")

        if act_s in ("block", "4"):
            if cov_priv or cov_pub:
                block_rules.append({
                    "name": str(r.get("Name") or ""),
                    "display_name": str(r.get("DisplayName") or r.get("Name") or "Блокирующее правило"),
                    "profile": str(r.get("Profile") or ""),
                })
            continue

        if act_s not in ("allow", "2"):
            continue

        if proto_s in ("any", "tcp", "6") and _port_matches(lport, int(tcp_port)):
            if cov_priv:
                tcp_priv = True
            if cov_pub:
                tcp_pub = True
        if proto_s in ("any", "udp", "17") and _port_matches(lport, int(udp_port)):
            if cov_priv:
                udp_priv = True
                # Примечание: Any покрывает оба протокола
            if cov_pub:
                udp_pub = True

    tcp_ok = tcp_priv and tcp_pub
    udp_ok = udp_priv and udp_pub
    has_block = bool(block_rules)
    return {
        "checked": bool(ps_data is not None),
        "program": primary_program,
        "tcp_port": int(tcp_port),
        "udp_port": int(udp_port),
        "tcp_allowed": tcp_ok,
        "udp_allowed": udp_ok,
        "has_block_rules": has_block,
        "block_rules": block_rules,
        "ok": bool(tcp_ok and udp_ok and not has_block),
        "profiles": parsed_profiles,
        "has_public_profile": bool(public_profiles),
        "public_profiles": public_profiles,
    }


_MAC_FW_STATE_RE = re.compile(r"State\s*=\s*(\d+)", re.IGNORECASE)


def parse_darwin_socketfilterfw_state(output: str) -> tuple[str, bool]:
    """Разбирает вывод `socketfilterfw --getglobalstate` -> (state_name, block_all_incoming)."""
    txt_s = (output or "").strip()
    if not txt_s:
        return "unknown", False
    m = _MAC_FW_STATE_RE.search(txt_s)
    if m:
        code = int(m.group(1))
        if code >= 2:
            return "block_all", True
        if code == 1:
            return "enabled", False
        return "disabled", False
    low = txt_s.lower()
    if "block all" in low:
        return "block_all", True
    if "enabled" in low:
        return "enabled", False
    if "disabled" in low:
        return "disabled", False
    return "unknown", False


def _query_darwin_firewall_state() -> tuple[str, bool]:
    if sys.platform != "darwin":
        return "unknown", False
    out = _run_darwin_cmd(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"], timeout=2.0)
    return parse_darwin_socketfilterfw_state(out)


def query_firewall_and_profiles(lan_port: int, app_root: Path | None = None, phys_adapters: list[dict] | None = None) -> dict:
    """Проверяет состояние межсетевого экрана (macOS socketfilterfw / Windows netsh) без запроса прав администратора."""
    tcp_p = int(lan_port)
    udp_p = tcp_p + 1
    if os.name != "nt":
        mac_state, mac_block_all = _query_darwin_firewall_state()
        return {
            "supported": False,
            "platform": _local_platform_name(),
            "checked": False,
            "macos_state": mac_state,
            "macos_block_all": mac_block_all,
            "program": sys.executable,
            "tcp_port": tcp_p,
            "udp_port": udp_p,
            "tcp_allowed": not mac_block_all,
            "udp_allowed": not mac_block_all,
            "has_block_rules": False,
            "block_rules": [],
            "ok": not mac_block_all,
            "profiles": [],
            "has_public_profile": False,
            "public_profiles": [],
        }
    primary_prog, _ = _firewall_target_programs(app_root)
    eff_phys = local_ipv4s() if phys_adapters is None else phys_adapters

    ps_cmd = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$profs = @(Get-NetConnectionProfile | Select-Object Name,InterfaceAlias,InterfaceIndex,@{N='Category';E={[string]$_.NetworkCategory}}); "
        "$rules = @(Get-NetFirewallApplicationFilter | Where-Object { $_.Program -like '*python*' } | ForEach-Object { "
        "$app = $_; $r = Get-NetFirewallRule -AssociatedNetFirewallApplicationFilter $app; $pf = $r | Get-NetFirewallPortFilter; "
        "[pscustomobject]@{Name=$r.Name;DisplayName=$r.DisplayName;Program=$app.Program;Direction=[string]$r.Direction;"
        "Action=[string]$r.Action;Enabled=[string]$r.Enabled;Profile=[string]$r.Profile;Protocol=[string]$pf.Protocol;LocalPort=$pf.LocalPort} "
        "}); "
        "[pscustomobject]@{profiles=$profs;rules=$rules} | ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12.0,
            creationflags=cflags,
        )
        txt = (res.stdout or "").strip()
        ps_json = json.loads(txt) if txt else {"profiles": [], "rules": []}
    except Exception as e:
        _lan_log(f"Не удалось проверить правила брандмауэра через PowerShell: {e}", logging.WARNING, rate_key="fw_q", min_interval=60.0)
        return {
            "checked": False,
            "program": primary_prog,
            "tcp_port": tcp_p,
            "udp_port": udp_p,
            "tcp_allowed": True,
            "udp_allowed": True,
            "has_block_rules": False,
            "block_rules": [],
            "ok": True,
            "profiles": [],
            "has_public_profile": False,
            "public_profiles": [],
        }
    return parse_firewall_and_profiles(ps_json, primary_prog, tcp_p, udp_p, eff_phys)


def build_firewall_netsh_script(
    program_paths: list[str],
    tcp_port: int,
    udp_port: int,
    *,
    remove_blocking: bool = False,
    block_rule_names: list[str] | None = None,
) -> str:
    """Формирует скрипт для однократного UAC-запуска netsh advfirewall (TCP lan.port + UDP lan.port+1, Private+Public)."""
    q = lambda s: str(s).replace("'", "''")
    lines = ["$ErrorActionPreference = 'Continue'"]
    if remove_blocking:
        for rname in block_rule_names or []:
            if rname:
                lines.append(f"Remove-NetFirewallRule -Name '{q(rname)}' -ErrorAction SilentlyContinue")
        for prog in program_paths:
            lines.append(f"& netsh.exe advfirewall firewall delete rule name=all dir=in program=\"{prog}\" | Out-Null")

    for idx, prog in enumerate(program_paths):
        suffix = "" if idx == 0 else f" ({Path(prog).name})"
        tcp_name = f"RealtyWatch LAN TCP{suffix}"
        udp_name = f"RealtyWatch LAN UDP{suffix}"
        lines.extend([
            f"& netsh.exe advfirewall firewall delete rule name=\"{tcp_name}\" program=\"{prog}\" | Out-Null",
            f"& netsh.exe advfirewall firewall delete rule name=\"{udp_name}\" program=\"{prog}\" | Out-Null",
            f"& netsh.exe advfirewall firewall add rule name=\"{tcp_name}\" dir=in action=allow program=\"{prog}\" protocol=TCP localport={int(tcp_port)} profile=private,public",
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
            f"& netsh.exe advfirewall firewall add rule name=\"{udp_name}\" dir=in action=allow program=\"{prog}\" protocol=UDP localport={int(udp_port)} profile=private,public",
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
        ])
    lines.append("exit 0")
    return "\n".join(lines)


def _run_elevated_powershell(ps_script: str, timeout_sec: float = 30.0) -> int:
    """Запускает PowerShell через ShellExecuteExW('runas') с одним запросом UAC и ждёт код возврата."""
    if os.name != "nt":
        raise RuntimeError("Настройка брандмауэра Windows доступна только на Windows")
    import ctypes.wintypes as wt

    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    params = f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("fMask", wt.ULONG),
            ("hwnd", wt.HWND),
            ("lpVerb", wt.LPCWSTR),
            ("lpFile", wt.LPCWSTR),
            ("lpParameters", wt.LPCWSTR),
            ("lpDirectory", wt.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wt.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wt.LPCWSTR),
            ("hkeyClass", wt.HKEY),
            ("dwHotKey", wt.DWORD),
            ("hIconOrMonitor", wt.HANDLE),
            ("hProcess", wt.HANDLE),
        ]

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = "powershell.exe"
    sei.lpParameters = params
    sei.lpDirectory = None
    sei.nShow = 0  # SW_HIDE

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wt.BOOL

    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        err = ctypes.get_last_error() or kernel32.GetLastError()
        if err == 1223:  # ERROR_CANCELLED
            raise ValueError("Запрос прав администратора (UAC) был отменён пользователем")
        raise RuntimeError(f"Не удалось запустить настройку брандмауэра (код Win32: {err})")

    hproc = sei.hProcess
    if not hproc:
        return 0
    try:
        wait_ms = max(1000, int(timeout_sec * 1000))
        wres = kernel32.WaitForSingleObject(hproc, wait_ms)
        if wres == 0x00000102:  # WAIT_TIMEOUT
            raise TimeoutError("Превышено время ожидания завершения настройки брандмауэра")
        exit_code = wt.DWORD(1)
        kernel32.GetExitCodeProcess(hproc, ctypes.byref(exit_code))
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(hproc)


def _probe_gateway_reachable(gw_ip: str, timeout_sec: float = 0.9) -> bool:
    """Проверяет доступность шлюза по умолчанию (ICMP ping + быстрый TCP-фолбэк на случай запрета ICMP)."""
    gw = (gw_ip or "").strip()
    if not gw or gw == "0.0.0.0":
        return False
    if os.name == "nt":
        try:
            cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["ping", "-n", "1", "-w", str(int(timeout_sec * 1000)), gw],
                capture_output=True,
                text=True,
                encoding="cp866",
                errors="replace",
                timeout=timeout_sec + 1.5,
                creationflags=cflags,
            )
            out_up = (r.stdout or "").upper()
            if "TTL=" in out_up:
                return True
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            wait_ms = str(max(250, int(timeout_sec * 1000)))
            r = subprocess.run(
                ["/sbin/ping", "-n", "-c", "1", "-W", wait_ms, gw],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec + 1.5,
            )
            if r.returncode == 0 or "ttl=" in (r.stdout or "").lower():
                return True
        except Exception:
            pass
    for p in (80, 443, 53):
        try:
            with socket.create_connection((gw, p), timeout=0.45):
                return True
        except ConnectionRefusedError:
            # Ответ RST от роутера доказывает, что он физически доступен по L2/L3!
            return True
        except OSError:
            continue
    return False


def _safe_img_rel(rel: str):
    """Проверяет и нормализует относительный путь фото вида 'cian/12345.jpg'."""
    norm = str(rel or "").replace("\\", "/").strip("/")
    parts = norm.split("/")
    if len(parts) != 2:
        return None
    src, fname = parts
    if src not in _SOURCES or not fname or ".." in fname or "/" in fname or "\\" in fname:
        return None
    if "." not in fname:
        return None
    stem, _, ext = fname.rpartition(".")
    ext = ext.lower()
    if not stem or ext not in _VALID_IMG_EXTS:
        return None
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', stem):
        return None
    return src, stem, ext, fname


def _parse_json(raw, default):
    if raw is None:
        return default
    if isinstance(raw, type(default)):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, type(default)) else default
    except (TypeError, ValueError):
        return default


def _scan_images_manifest():
    """Список всех локальных фото основной базы для расчёта дельты при полной передаче."""
    root = images.IMAGES
    out = []
    if not root.exists():
        return out
    for src in _SOURCES:
        folder = root / src
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            if not p.is_file():
                continue
            ext = p.suffix.lower().lstrip(".")
            if ext not in _VALID_IMG_EXTS or not p.stem:
                continue
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz <= 0:
                continue
            out.append({"source": src, "source_id": p.stem, "ext": ext, "size": sz})
    return out


def _copy_common_table(con, src_schema: str, table: str):
    """Копирует все совпадающие колонки таблицы из подключённой БД src_schema в main."""
    main_cols = [r[1] for r in con.execute(f"PRAGMA main.table_info({table})")]
    src_cols = {r[1] for r in con.execute(f"PRAGMA {src_schema}.table_info({table})")}
    cols = [c for c in main_cols if c in src_cols]
    if not cols:
        return
    col_sql = ", ".join(cols)
    con.execute(f"INSERT INTO main.{table} ({col_sql}) SELECT {col_sql} FROM {src_schema}.{table}")


def _max_freshness_ts(freshness: dict) -> str | None:
    vals = [str(v) for v in (freshness or {}).values() if v]
    return max(vals) if vals else None


def _compare_freshness(sender_f: dict, local_f: dict):
    """Сравнивает свежесть сборов отправителя и текущей машины по каждому сайту."""
    older_sources = []
    for s in _SOURCES:
        loc_ts = (local_f or {}).get(s)
        snd_ts = (sender_f or {}).get(s)
        if loc_ts and (not snd_ts or str(loc_ts) > str(snd_ts)):
            older_sources.append({
                "source": s,
                "label": exchange.SOURCE_LABEL.get(s, s),
                "local_ts": loc_ts,
                "sender_ts": snd_ts,
            })
    s_max = _max_freshness_ts(sender_f)
    l_max = _max_freshness_ts(local_f)
    is_older = bool(older_sources or (l_max and (not s_max or l_max > s_max)))
    warning = ""
    if is_older:
        parts = [
            f"{o['label']} (у вас: {o['local_ts'][:16].replace('T', ' ')}"
            + (f", во входящей: {o['sender_ts'][:16].replace('T', ' ')})" if o["sender_ts"] else ", во входящей: нет)")
            for o in older_sources
        ]
        if parts:
            warning = "Входящая база старее вашей по сайтам: " + "; ".join(parts)
        else:
            warning = "Входящая база собрана раньше вашей текущей базы"
    return {
        "is_older": is_older,
        "older_sources": older_sources,
        "sender_max": s_max,
        "local_max": l_max,
        "warning": warning,
    }


def _validate_sqlite_snapshot(db_path: Path):
    """Проверяет целостность SQLite-снимка (quick_check + наличие таблицы listings)."""
    chk_con = sqlite3.connect(db_path)
    try:
        res_chk = chk_con.execute("PRAGMA quick_check").fetchone()
        if not res_chk or res_chk[0] != "ok":
            raise ValueError("повреждённый файл базы данных")
        has_tbl = chk_con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listings'"
        ).fetchone()
        if has_tbl is None:
            raise ValueError("в снимке базы отсутствует таблица listings")
    except sqlite3.Error as e:
        raise ValueError(f"повреждённый файл базы данных: {e}") from e
    finally:
        chk_con.close()


async def _read_checked_body(request: Request, max_bytes: int, empty_msg: str | None = None) -> bytes:
    """Читает тело HTTP-запроса с проверкой обрыва связи и соответствия Content-Length."""
    cl_hdr = request.headers.get("content-length")
    expected_len = None
    if cl_hdr is not None and str(cl_hdr).strip() != "":
        try:
            expected_len = int(cl_hdr)
        except (TypeError, ValueError):
            raise HTTPException(400, "Некорректный заголовок Content-Length")
        if expected_len < 0:
            raise HTTPException(400, "Некорректный заголовок Content-Length")
        if expected_len > max_bytes:
            raise HTTPException(413, "Размер передаваемых данных превышает допустимый лимит")
    try:
        raw = await request.body()
    except Exception:
        raise HTTPException(400, "Передача прервалась до завершения")
    if expected_len is not None and len(raw) != expected_len:
        raise HTTPException(400, "Передача прервалась (получены неполные данные)")
    if len(raw) > max_bytes:
        raise HTTPException(413, "Размер передаваемых данных превышает допустимый лимит")
    if empty_msg and not raw:
        raise HTTPException(400, empty_msg)
    return raw


def _http_raw(ip: str, port: int, method: str, path: str, body: bytes | None = None,
              headers: dict | None = None, timeout: float = 10.0):
    """Прямой HTTP-вызов к устройству в LAN (без системного прокси): возвращает (status, headers, bytes)."""
    if not _is_lan_ip(ip):
        raise ValueError(f"IP-адрес {ip!r} не относится к локальной сети")
    p = int(port)
    if not (1 <= p <= 65535):
        raise ValueError(f"некорректный порт {port!r}")
    hdrs = dict(headers or {})
    hdrs.setdefault("Connection", "close")
    if body is not None and "Content-Length" not in hdrs:
        hdrs["Content-Length"] = str(len(body))
    conn = http.client.HTTPConnection(_clean_ip_str(ip), p, timeout=timeout)
    try:
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
        except (
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            http.client.HTTPException,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            TimeoutError,
            socket.timeout,
            OSError,
        ) as e:
            raise ConnectionError(DISCONNECT_MSG) from e
        resp_hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, resp_hdrs, raw
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _http_call(ip: str, port: int, method: str, path: str, body: bytes | None = None,
               headers: dict | None = None, timeout: float = 10.0):
    """Прямой HTTP-вызов с разбором JSON-ответа."""
    status_code, _, raw = _http_raw(ip, port, method, path, body=body, headers=headers, timeout=timeout)
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        data = {"raw": raw.decode("utf-8", errors="replace")}
    return status_code, data


def _outbox_log(con, peer_id: str, kind: str, event_id: str = "", payload_path: str = ""):
    try:
        con.execute(
            "INSERT INTO sync_outbox(peer_id, kind, event_id, payload_path, created_at, tries) VALUES(?,?,?,?,?,1)",
            (peer_id, kind, event_id, payload_path, _now_ts()),
        )
        con.commit()
    except Exception:
        pass


def _normalize_mark_items(raw, *, allow_empty: bool = False) -> list[str]:
    """Нормализует список выбранных категорий отметок (likes, dislikes, notes, merges)."""
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple, set)):
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
    else:
        parts = []
    seen = set(parts)
    ordered = [cat for cat in MARK_CATEGORIES if cat in seen]
    if not ordered and not allow_empty:
        raise ValueError("выберите хотя бы один параметр (лайки, дизлайки, заметки или ручные объединения)")
    return ordered


def _marks_counts(con) -> dict:
    """Количество объектов с лайками, дизлайками, заметками и групп ручного объединения в БД."""
    likes_n = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(l.object_id, k.source || ':' || k.source_id)) "
        "FROM likes k LEFT JOIN listings l ON l.source = k.source AND l.source_id = k.source_id"
    ).fetchone()[0]
    dislikes_n = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(l.object_id, k.source || ':' || k.source_id)) "
        "FROM dislikes k LEFT JOIN listings l ON l.source = k.source AND l.source_id = k.source_id"
    ).fetchone()[0]
    notes_n = con.execute(
        """SELECT COUNT(DISTINCT obj_key) FROM (
               SELECT COALESCE(l.object_id, n.source || ':' || n.source_id) AS obj_key
               FROM notes n LEFT JOIN listings l ON l.source = n.source AND l.source_id = n.source_id
               WHERE TRIM(COALESCE(n.text, '')) != ''
               UNION
               SELECT COALESCE(l.object_id, inr.source || ':' || inr.source_id) AS obj_key
               FROM imported_notes inr LEFT JOIN listings l ON l.source = inr.source AND l.source_id = inr.source_id
               WHERE TRIM(COALESCE(inr.text, '')) != ''
           )"""
    ).fetchone()[0]
    merges_n = len(dedupe.active_manual_merges(con)[0])
    return {
        "likes": int(likes_n or 0),
        "dislikes": int(dislikes_n or 0),
        "notes": int(notes_n or 0),
        "merges": int(merges_n or 0),
    }


def _union_manual_merges_rows(main_rows: list[dict], inc_rows: list[dict]) -> list[tuple]:
    """Объединяет списки manual_merges двух машин без разрыва пересекающихся связок (приоритет primary у main)."""
    if not inc_rows and not main_rows:
        return []
    if not main_rows:
        return [
            (str(r["source"]), str(r["source_id"]), str(r["group_id"]), 1 if r.get("is_primary") else 0, str(r.get("ts") or ""))
            for r in inc_rows
            if r.get("source") and r.get("source_id") and r.get("group_id")
        ]
    by_key, by_tag, info = {}, {}, {}
    for side, rows in (("main", main_rows), ("imp", inc_rows)):
        for r in rows:
            src = str(r.get("source") or "").strip()
            sid = str(r.get("source_id") or "").strip()
            gid = str(r.get("group_id") or "").strip()
            if not src or not sid or not gid:
                continue
            k = (src, sid)
            tag = (side, gid)
            by_key.setdefault(k, set()).add(tag)
            by_tag.setdefault(tag, set()).add(k)
            info.setdefault(k, []).append((side, r))
    seen_k, used_gids, out = set(), set(), []
    for k0 in sorted(by_key):
        if k0 in seen_k:
            continue
        comp_keys, comp_tags = set(), set()
        stack = [k0]
        seen_k.add(k0)
        while stack:
            k = stack.pop()
            comp_keys.add(k)
            for tag in by_key[k]:
                if tag in comp_tags:
                    continue
                comp_tags.add(tag)
                for nk in by_tag[tag]:
                    if nk not in seen_k:
                        seen_k.add(nk)
                        stack.append(nk)
        if len(comp_keys) < 2:
            continue
        cands = [
            (1 if r.get("is_primary") else 0, str(r.get("ts") or ""), 1 if side == "main" else 0, k, str(r.get("group_id") or ""))
            for k in comp_keys
            for side, r in info[k]
        ]
        _, win_ts, _, prim_key, win_gid = max(cands)
        if not win_gid or win_gid in used_gids:
            win_gid = f"{prim_key[0]}:{prim_key[1]}"
        used_gids.add(win_gid)
        ts = max((str(r.get("ts") or "") for k in comp_keys for _, r in info[k]), default=win_ts)
        for src, sid in sorted(comp_keys):
            out.append((src, sid, win_gid, 1 if (src, sid) == prim_key else 0, ts))
    return out


def _export_marks_payload(con, ident: dict, items: list[str]) -> dict:
    """Экспортирует выбранные категории пользовательского слоя (лайки, дизлайки, заметки, ручные объединения)."""
    sel = _normalize_mark_items(items)
    full_counts = _marks_counts(con)
    counts = {cat: full_counts[cat] if cat in sel else 0 for cat in MARK_CATEGORIES}

    likes_rows, dislikes_rows, notes_rows, imp_notes_rows, merges_rows = [], [], [], [], []
    ref_keys: set[tuple[str, str]] = set()

    if "likes" in sel:
        for r in con.execute("SELECT source, source_id, ts FROM likes"):
            s, sid = str(r["source"]), str(r["source_id"])
            likes_rows.append({"source": s, "source_id": sid, "ts": str(r["ts"] or "")})
            ref_keys.add((s, sid))

    if "dislikes" in sel:
        for r in con.execute("SELECT source, source_id, ts FROM dislikes"):
            s, sid = str(r["source"]), str(r["source_id"])
            dislikes_rows.append({"source": s, "source_id": sid, "ts": str(r["ts"] or "")})
            ref_keys.add((s, sid))

    if "notes" in sel:
        for r in con.execute("SELECT source, source_id, text, ts FROM notes WHERE TRIM(COALESCE(text, '')) != ''"):
            s, sid = str(r["source"]), str(r["source_id"])
            notes_rows.append({"source": s, "source_id": sid, "text": str(r["text"]).strip()[:2000], "ts": str(r["ts"] or "")})
            ref_keys.add((s, sid))
        for r in con.execute(
            "SELECT source, source_id, origin_id, origin_name, text, ts, updated_at "
            "FROM imported_notes WHERE TRIM(COALESCE(text, '')) != ''"
        ):
            s, sid = str(r["source"]), str(r["source_id"])
            imp_notes_rows.append({
                "source": s,
                "source_id": sid,
                "origin_id": str(r["origin_id"] or ""),
                "origin_name": str(r["origin_name"] or ""),
                "text": str(r["text"]).strip()[:2000],
                "ts": str(r["ts"] or ""),
                "updated_at": str(r["updated_at"] or ""),
            })
            ref_keys.add((s, sid))

    if "merges" in sel:
        by_group, primaries = dedupe.active_manual_merges(con)
        active_gids = set(by_group.keys())
        if active_gids:
            for r in con.execute("SELECT source, source_id, group_id, is_primary, ts FROM manual_merges"):
                gid = str(r["group_id"] or "")
                if gid not in active_gids:
                    continue
                s, sid = str(r["source"]), str(r["source_id"])
                merges_rows.append({
                    "source": s,
                    "source_id": sid,
                    "group_id": gid,
                    "is_primary": 1 if r["is_primary"] else 0,
                    "ts": str(r["ts"] or ""),
                })
                ref_keys.add((s, sid))

    ref_listings = []
    ref_history = []
    if ref_keys:
        for src, sid in sorted(ref_keys):
            row = con.execute("SELECT * FROM listings WHERE source=? AND source_id=?", (src, sid)).fetchone()
            if row is not None:
                d = dict(row)
                d.pop("object_id", None)
                ref_listings.append(d)
            for hr in con.execute(
                "SELECT source, source_id, ts, price FROM price_history WHERE source=? AND source_id=?",
                (src, sid),
            ):
                ref_history.append({
                    "source": str(hr["source"]),
                    "source_id": str(hr["source_id"]),
                    "ts": str(hr["ts"]),
                    "price": hr["price"],
                })

    return {
        "format": _MARKS_SYNC_FORMAT,
        "version": _MARKS_SYNC_VERSION,
        "created_at": _now_ts(),
        "from_device_id": ident["device_id"],
        "from_device_name": ident["device_name"],
        "items": sel,
        "counts": counts,
        "likes": likes_rows,
        "dislikes": dislikes_rows,
        "notes": notes_rows,
        "imported_notes": imp_notes_rows,
        "manual_merges": merges_rows,
        "ref_listings": ref_listings,
        "ref_price_history": ref_history,
    }


def _pack_marks_zip(payload: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("marks.json", json.dumps(payload, ensure_ascii=False))
    return buf.getvalue()


def _unpack_marks_zip(raw_or_path) -> dict:
    try:
        zf_ctx = zipfile.ZipFile(io.BytesIO(raw_or_path), "r") if isinstance(raw_or_path, (bytes, bytearray)) else zipfile.ZipFile(raw_or_path, "r")
    except zipfile.BadZipFile as e:
        raise ValueError("повреждён архив отметок") from e
    with zf_ctx as zf:
        bad = zf.testzip()
        if bad:
            raise ValueError(f"повреждён файл внутри архива отметок: {bad}")
        if "marks.json" not in zf.namelist():
            raise ValueError("в пакете отсутствует marks.json")
        try:
            payload = json.loads(zf.read("marks.json").decode("utf-8"))
        except Exception as e:
            raise ValueError("некорректный JSON в пакете отметок") from e
    if not isinstance(payload, dict) or payload.get("format") != _MARKS_SYNC_FORMAT:
        raise ValueError("неподдерживаемый формат пакета отметок")
    _normalize_mark_items(payload.get("items") or [])
    return payload


class LanService:
    """Управляет LAN-сервером, UDP-обнаружением, PIN-сопряжением, очередью входящих и отправкой."""

    def __init__(self, bind_host: str = "0.0.0.0"):
        self._bind_host = bind_host
        self._lock = threading.RLock()
        self._server = None
        self._server_thread = None
        self._hb_thread = None
        self._disc_thread = None
        self._stop_evt = threading.Event()
        self._running_port: int | None = None
        self._running_disc: bool = False
        self._pin_state: dict | None = None
        self._discovered: dict[str, dict] = {}
        self._peer_runtime: dict[str, dict] = {}
        self._inbox: dict[str, dict] = {}
        self._inbox_manifest_key: str | None = None
        self._db_sessions: dict[str, dict] = {}
        self._outbox_thread = None
        self._outbox_evt = threading.Event()
        self._hb_evt = threading.Event()
        self._bootstrapped_peers: set[str] = set()
        self._probe_reply_ts: dict[str, float] = {}
        self._flush_lock = threading.Lock()
        self._live_events: deque[dict] = deque(maxlen=200)
        self._live_seq: int = 0
        self._seen_events: deque[str] = deque(maxlen=2000)
        self._seen_set: set[str] = set()
        self._data_rev: int = 0
        self._outbox_count: int = 0
        self._newer_db_peer: dict | None = None
        self._history_rev: int = 0
        self._history_unread: int = 0
        self._history_db_key: str | None = None
        self._rx_sock = None
        self._udp_listening: bool = False
        self._udp_started_mono: float = 0.0
        self._last_bcast_probe_mono: float = 0.0
        self._last_unicast_probe_by_ip: dict[str, float] = {}
        self._diag_listeners: list = []
        self._scanning_subnet: bool = False
        self._fw_cache: dict | None = None
        self._fw_cache_ts: float = 0.0
        self._fw_refreshing: bool = False
        self.sync_app = self._build_app()

    # ---------- хранилище очереди входящих (привязано к текущему db.DB_FILE) ----------

    def _inbox_dir(self) -> Path:
        return exchange.import_staging_dir() / "lan_inbox"

    def _manifest_path(self) -> Path:
        return self._inbox_dir() / "manifest.json"

    def _ensure_inbox_loaded(self):
        mpath = self._manifest_path()
        key = str(mpath.resolve()) if mpath.parent else str(mpath)
        if self._inbox_manifest_key == key:
            return
        self._inbox_manifest_key = key
        self._inbox = {}
        self._db_sessions = {}
        if not mpath.is_file():
            return
        try:
            items = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict) or not it.get("req_id"):
                continue
            # Проверяем, что файлы полезной нагрузки всё ещё на диске
            if it.get("kind") == "db":
                dbp = Path(it.get("db_path") or "")
                if not dbp.is_file():
                    continue
            else:
                zp = Path(it.get("zip_path") or "")
                if not zp.is_file():
                    continue
            self._inbox[it["req_id"]] = it

    def _save_inbox_manifest(self):
        mpath = self._manifest_path()
        mpath.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(self._inbox.values(), key=lambda x: x.get("received_at") or "", reverse=True)
        tmp = mpath.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(mpath)

    def reset_runtime_cache(self):
        """Сбрасывает оперативные кэши (например, после clear_all или переключения базы в тестах)."""
        with self._lock:
            self._inbox = {}
            self._inbox_manifest_key = None
            self._db_sessions = {}
            self._discovered = {}
            self._peer_runtime = {}
            self._pin_state = None
            self._live_events.clear()
            self._seen_events.clear()
            self._seen_set.clear()
            self._bootstrapped_peers.clear()
            self._probe_reply_ts.clear()
            self._outbox_count = 0
            self._newer_db_peer = None
            self._history_rev = 0
            self._history_unread = 0
            self._history_db_key = None
            self._fw_cache = None
            self._fw_cache_ts = 0.0
            self._fw_refreshing = False
            self._last_bcast_probe_mono = 0.0
            self._last_unicast_probe_by_ip.clear()
            self._diag_listeners.clear()
            self._scanning_subnet = False

    def _ensure_history_unread(self) -> int:
        cur_key = str(db.DB_FILE)
        with self._lock:
            if self._history_db_key == cur_key:
                return self._history_unread
        cnt = 0
        try:
            con = db.connect()
            try:
                cnt = int(con.execute("SELECT COUNT(*) FROM sync_history WHERE COALESCE(seen, 0)=0").fetchone()[0] or 0)
            finally:
                con.close()
        except Exception:
            cnt = 0
        with self._lock:
            self._history_db_key = cur_key
            self._history_unread = cnt
            return self._history_unread

    def _build_preview_items(self, con, keys, fallback_rows: dict | None = None,
                             notes_map: dict | None = None, summary_hint: dict | None = None,
                             limit: int = 30) -> list[dict]:
        """Собирает компактные карточки предпросмотра для боковой панели истории сети
        (по локальной БД listings с откатом на данные пакета, включая прямую ссылку на сайт)."""
        clean = []
        seen_k = set()
        for k in keys or []:
            if isinstance(k, (list, tuple)) and len(k) == 2:
                pair = (str(k[0]), str(k[1]))
                if pair[0] and pair[1] and pair not in seen_k:
                    seen_k.add(pair)
                    clean.append(pair)
        if not clean:
            return []

        fb = fallback_rows or {}
        nmap = notes_map or {}
        grouped: dict[str, dict] = {}
        order_ids: list[str] = []

        for src, sid in clean:
            row = con.execute(
                "SELECT object_id, source, source_id, url, title, address, rooms, area, price "
                "FROM listings WHERE source=? AND source_id=?",
                (src, sid),
            ).fetchone()
            if row is not None:
                oid = str(row["object_id"] or f"{src}:{sid}")
                rdict = dict(row)
            else:
                oid = f"{src}:{sid}"
                rdict = dict(fb.get((src, sid)) or {})
                if not rdict and summary_hint:
                    rdict = dict(summary_hint)
                rdict.setdefault("source", src)
                rdict.setdefault("source_id", sid)

            note_txt = str(nmap.get((src, sid)) or "").strip()
            if not note_txt:
                nr = con.execute("SELECT text FROM notes WHERE source=? AND source_id=?", (src, sid)).fetchone()
                if nr and nr[0]:
                    note_txt = str(nr[0]).strip()
                else:
                    inr = con.execute(
                        "SELECT text FROM imported_notes WHERE source=? AND source_id=? ORDER BY updated_at DESC LIMIT 1",
                        (src, sid),
                    ).fetchone()
                    if inr and inr[0]:
                        note_txt = str(inr[0]).strip()

            if oid not in grouped:
                order_ids.append(oid)
                grouped[oid] = {
                    "object_id": oid,
                    "source": str(rdict.get("source") or src),
                    "source_id": str(rdict.get("source_id") or sid),
                    "url": str(rdict.get("url") or ""),
                    "title": str(rdict.get("title") or ""),
                    "address": str(rdict.get("address") or ""),
                    "rooms": rdict.get("rooms"),
                    "area": rdict.get("area"),
                    "price": rdict.get("price"),
                    "note": note_txt[:240],
                }
            else:
                g = grouped[oid]
                if not g["url"] and rdict.get("url"):
                    g["url"] = str(rdict["url"])
                if not g["address"] and rdict.get("address"):
                    g["address"] = str(rdict["address"])
                if not g["title"] and rdict.get("title"):
                    g["title"] = str(rdict["title"])
                if rdict.get("price") and (not g["price"] or rdict["price"] < g["price"]):
                    g["price"] = rdict["price"]
                if not g["note"] and note_txt:
                    g["note"] = note_txt[:240]

            if len(order_ids) >= limit:
                break

        return [grouped[oid] for oid in order_ids[:limit]]

    def _record_history(self, con=None, *, kind: str, peer_id: str = "", peer_name: str = "",
                        title: str = "", detail: str = "", keys=None, cats: dict | None = None,
                        preview: list[dict] | None = None, req_id: str | None = None,
                        batch_id: int | None = None, mark_seen: bool = False, direction: str = "in") -> int:
        """Записывает (или обновляет по req_id) событие партнёра в таблицу sync_history."""
        clean_keys = []
        seen_k = set()
        for k in keys or []:
            if isinstance(k, (list, tuple)) and len(k) == 2:
                pair = [str(k[0]), str(k[1])]
                tk = (pair[0], pair[1])
                if pair[0] and pair[1] and tk not in seen_k:
                    seen_k.add(tk)
                    clean_keys.append(pair)

        clean_cats = {}
        for ck, cv in (cats or {}).items():
            c_list = []
            c_seen = set()
            for p in cv or []:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    tp = (str(p[0]), str(p[1]))
                    if tp[0] and tp[1] and tp not in c_seen:
                        c_seen.add(tp)
                        c_list.append([tp[0], tp[1]])
            if c_list:
                clean_cats[str(ck)] = c_list

        own = con is None
        c = db.connect() if own else con
        try:
            now_s = _now_ts()
            prev_list = preview if preview is not None else self._build_preview_items(c, clean_keys)
            keys_s = json.dumps(clean_keys, ensure_ascii=False)
            cats_s = json.dumps(clean_cats, ensure_ascii=False)
            prev_s = json.dumps(prev_list, ensure_ascii=False)

            row_id = None
            if req_id:
                ex = c.execute("SELECT id, keys_json, cats_json, preview_json, seen FROM sync_history WHERE req_id=?", (req_id,)).fetchone()
                if ex is not None:
                    row_id = int(ex["id"])
                    eff_keys_s = keys_s if clean_keys else (ex["keys_json"] or "[]")
                    eff_cats_s = cats_s if clean_cats else (ex["cats_json"] or "{}")
                    eff_prev_s = prev_s if prev_list else (ex["preview_json"] or "[]")
                    eff_seen = 1 if (mark_seen or ex["seen"]) else 0
                    c.execute(
                        """UPDATE sync_history
                           SET ts=?, kind=?, peer_id=COALESCE(NULLIF(?,''), peer_id),
                               peer_name=COALESCE(NULLIF(?,''), peer_name),
                               batch_id=COALESCE(?, batch_id),
                               title=?, detail=?, keys_json=?, cats_json=?, preview_json=?, seen=?
                           WHERE id=?""",
                        (now_s, kind, peer_id, peer_name[:80], batch_id, title[:200], detail[:1000],
                         eff_keys_s, eff_cats_s, eff_prev_s, eff_seen, row_id),
                    )
            if row_id is None:
                cur = c.execute(
                    """INSERT INTO sync_history
                       (ts, direction, kind, peer_id, peer_name, req_id, batch_id,
                        title, detail, keys_json, cats_json, preview_json, seen)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        now_s,
                        direction,
                        kind,
                        peer_id,
                        (peer_name or "Партнёр")[:80],
                        req_id,
                        batch_id,
                        title[:200],
                        detail[:1000],
                        keys_s,
                        cats_s,
                        prev_s,
                        1 if mark_seen else 0,
                    ),
                )
                row_id = int(cur.lastrowid or 0)
                c.execute(
                    "DELETE FROM sync_history WHERE id NOT IN (SELECT id FROM sync_history ORDER BY id DESC LIMIT 500)"
                )

            unread = int(c.execute("SELECT COUNT(*) FROM sync_history WHERE COALESCE(seen, 0)=0").fetchone()[0] or 0)
            if own:
                c.commit()
        finally:
            if own:
                c.close()

        with self._lock:
            self._history_db_key = str(db.DB_FILE)
            self._history_unread = unread
            self._history_rev += 1
        return row_id

    def list_history(self, limit: int = 100) -> dict:
        lim = max(1, min(int(limit or 100), 300))
        with self._lock:
            self._ensure_inbox_loaded()
            active_reqs = set(self._inbox.keys())
        con = db.connect()
        try:
            rows = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM sync_history ORDER BY id DESC LIMIT ?",
                    (lim,),
                )
            ]
            unread = int(con.execute("SELECT COUNT(*) FROM sync_history WHERE COALESCE(seen, 0)=0").fetchone()[0] or 0)
        finally:
            con.close()

        items = []
        for r in rows:
            req_id = r.get("req_id")
            items.append({
                "id": r["id"],
                "ts": r["ts"],
                "direction": r.get("direction") or "in",
                "kind": r["kind"],
                "peer_id": r.get("peer_id") or "",
                "peer_name": r.get("peer_name") or "Партнёр",
                "req_id": req_id,
                "batch_id": r.get("batch_id"),
                "pending_inbox": bool(req_id and req_id in active_reqs),
                "title": r.get("title") or "",
                "detail": r.get("detail") or "",
                "keys": _parse_json(r.get("keys_json"), []),
                "cats": _parse_json(r.get("cats_json"), {}),
                "preview": _parse_json(r.get("preview_json"), []),
                "seen": bool(r.get("seen")),
            })
        with self._lock:
            self._history_db_key = str(db.DB_FILE)
            self._history_unread = unread
            hrev = self._history_rev
        return {"items": items, "unread": unread, "rev": hrev}

    def mark_history_seen(self, ids=None) -> dict:
        con = db.connect()
        try:
            if isinstance(ids, list) and ids:
                clean_ids = [int(x) for x in ids if str(x).isdigit() or isinstance(x, int)]
                if clean_ids:
                    ph = ",".join("?" * len(clean_ids))
                    con.execute(f"UPDATE sync_history SET seen=1 WHERE id IN ({ph})", clean_ids)
            else:
                con.execute("UPDATE sync_history SET seen=1 WHERE COALESCE(seen, 0)=0")
            con.commit()
            unread = int(con.execute("SELECT COUNT(*) FROM sync_history WHERE COALESCE(seen, 0)=0").fetchone()[0] or 0)
        finally:
            con.close()
        with self._lock:
            self._history_db_key = str(db.DB_FILE)
            self._history_unread = unread
        return {"ok": True, "unread": unread}

    def clear_history(self) -> dict:
        con = db.connect()
        try:
            con.execute("DELETE FROM sync_history")
            con.commit()
        finally:
            con.close()
        with self._lock:
            self._history_db_key = str(db.DB_FILE)
            self._history_unread = 0
            self._history_rev += 1
        return {"ok": True, "unread": 0}

    def _outbox_dir(self) -> Path:
        return exchange.import_staging_dir() / "lan_outbox"

    def _mark_seen_event(self, event_id: str) -> bool:
        """Возвращает True, если event_id уже встречался ранее (защита от петель)."""
        eid = str(event_id or "").strip()
        if not eid:
            return False
        with self._lock:
            if eid in self._seen_set:
                return True
            if len(self._seen_events) >= (self._seen_events.maxlen or 2000):
                old = self._seen_events.popleft()
                self._seen_set.discard(old)
            self._seen_events.append(eid)
            self._seen_set.add(eid)
            return False

    def _push_live_event(self, kind: str, payload: dict) -> dict:
        with self._lock:
            self._live_seq += 1
            ev = {"seq": self._live_seq, "kind": kind, "ts": _now_ts(), **payload}
            self._live_events.append(ev)
            return ev

    def _bump_data_rev(self):
        with self._lock:
            self._data_rev += 1

    # ---------- конфигурация и идентификация ----------

    def _identity(self, con=None):
        own = con is None
        c = db.connect() if own else con
        try:
            return settings.ensure_device_identity(c)
        finally:
            if own:
                c.close()

    def _all_local_ips(self) -> set[str]:
        """Возвращает множество ВСЕХ IPv4-адресов текущего компьютера (включая loopback, Wi-Fi, Ethernet, VPN и виртуальные адаптеры),
        чтобы безошибочно отличать собственные пакеты от пакетов другого ПК с клонированным device_id."""
        ips = {"127.0.0.1", "0.0.0.0", "::1"}
        try:
            for ad in _raw_system_adapters():
                for ip_s, _pref in ad.get("addresses") or ():
                    if ip_s:
                        ips.add(_clean_ip_str(ip_s))
        except Exception:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                addr = info[4][0]
                if addr:
                    ips.add(_clean_ip_str(addr))
        except OSError:
            pass
        return ips

    def _is_self_endpoint(self, ip_str: str, port_val: int, my_lan_port: int) -> bool:
        """True только если (ip_str, port_val) указывает на ЭТОТ ЖЕ экземпляр RealtyWatch."""
        clean = _clean_ip_str(ip_str)
        if clean not in self._all_local_ips():
            return False
        if port_val and int(port_val) != int(my_lan_port):
            return False
        return True

    def _heal_collided_device_id(self, remote_ip: str, remote_name: str = "") -> dict:
        """Если папка/база была перенесена на другой ПК и в сети встретился клон нашего device_id,
        автоматически назначает этому компьютеру новый уникальный device_id и оповещает сеть."""
        con = db.connect()
        try:
            new_ident = settings.rotate_device_id(con)
        finally:
            con.close()
        who = f"«{remote_name}» ({remote_ip})" if remote_name else remote_ip
        _lan_log(
            f"Обнаружено совпадение device_id с другим компьютером {who} (перенос базы/настроек) — "
            f"этому ПК назначен новый уникальный ID {new_ident['device_id']}"
        )
        runner.manager.log(
            "lan",
            f"обнаружен одинаковый ID устройства с {who} — автоматически назначен новый уникальный ID",
        )
        return new_ident

    def _lan_cfg(self):
        cfg = settings.load()
        lan = cfg.get("lan") or {}
        try:
            port = int(lan.get("port") or 8766)
        except (TypeError, ValueError):
            port = 8766
        if not (1024 <= port <= 65534):
            port = 8766
        return {
            "enabled": bool(lan.get("enabled", False)),
            "port": port,
            "auto_discovery": bool(lan.get("auto_discovery", True)),
            "device_id": str(lan.get("device_id") or "").strip(),
            "device_name": str(lan.get("device_name") or "").strip(),
        }

    # ---------- жизненный цикл сервера, heartbeat и UDP discovery ----------

    def start_if_enabled(self):
        cfg = self._lan_cfg()
        if not cfg["enabled"]:
            self.stop()
            return False
        self._identity()
        return self._start(cfg["port"], cfg["auto_discovery"])

    def reconfigure(self):
        cfg = self._lan_cfg()
        with self._lock:
            running = self._server_thread is not None and self._server_thread.is_alive()
            if not cfg["enabled"]:
                if running:
                    self.stop()
                return False
            self._identity()
            if running and self._running_port == cfg["port"] and self._running_disc == cfg["auto_discovery"]:
                return True
            if running:
                self.stop()
            return self._start(cfg["port"], cfg["auto_discovery"])

    def cleanup_stale_sessions(self, max_age_sec: float = DB_SESSION_TTL_SEC) -> int:
        """Удаляет незавершённые сессии передачи базы и осиротевшие временные файлы (.part / db_*)."""
        now_m = time.monotonic()
        now_w = time.time()
        evicted_dirs: list[str] = []
        cleaned = 0
        with self._lock:
            self._ensure_inbox_loaded()
            stale_ids = [
                rid for rid, sess in self._db_sessions.items()
                if max_age_sec <= 0 or (now_m - float(sess.get("created_mono", now_m))) >= max_age_sec
            ]
            for rid in stale_ids:
                sess = self._db_sessions.pop(rid, None)
                if sess and sess.get("sess_dir"):
                    evicted_dirs.append(str(sess["sess_dir"]))
            active_inbox_dirs = {
                str(Path(it["sess_dir"]).resolve())
                for it in self._inbox.values()
                if it.get("sess_dir")
            }
            active_sess_dirs = {
                str(Path(s["sess_dir"]).resolve())
                for s in self._db_sessions.values()
                if s.get("sess_dir")
            }
            idir = self._inbox_dir()

        for sd in evicted_dirs:
            shutil.rmtree(sd, ignore_errors=True)
            cleaned += 1

        if idir.is_dir():
            age_thr = 0.0 if max_age_sec <= 0 else max(30.0, float(max_age_sec))
            for entry in idir.iterdir():
                try:
                    st = entry.stat()
                except OSError:
                    continue
                if entry.is_file() and entry.name.endswith(".part"):
                    if max_age_sec <= 0 or (now_w - st.st_mtime) >= age_thr:
                        try:
                            entry.unlink(missing_ok=True)
                            cleaned += 1
                        except OSError:
                            pass
                elif entry.is_dir() and entry.name.startswith("db_"):
                    res_p = str(entry.resolve())
                    if res_p not in active_inbox_dirs and res_p not in active_sess_dirs:
                        if max_age_sec <= 0 or (now_w - st.st_mtime) >= age_thr:
                            shutil.rmtree(entry, ignore_errors=True)
                            cleaned += 1
        return cleaned

    def _start(self, port: int, auto_discovery: bool):
        self.cleanup_stale_sessions(max_age_sec=0)
        with self._lock:
            if self._server_thread is not None and self._server_thread.is_alive():
                if self._running_port == port:
                    return True
                self.stop()
            self._stop_evt.clear()
            self._hb_evt.clear()
            ucfg = uvicorn.Config(
                self.sync_app,
                host=self._bind_host,
                port=int(port),
                log_level="warning",
                access_log=False,
            )
            srv = uvicorn.Server(ucfg)
            srv.install_signal_handlers = lambda: None
            self._server = srv
            self._running_port = int(port)
            self._running_disc = bool(auto_discovery)

            t = threading.Thread(target=srv.run, daemon=True, name="lan-http")
            self._server_thread = t
            t.start()

            hb = threading.Thread(target=self._heartbeat_loop, daemon=True, name="lan-hb")
            self._hb_thread = hb
            hb.start()

            ob = threading.Thread(target=self._outbox_loop, daemon=True, name="lan-outbox")
            self._outbox_thread = ob
            ob.start()

            if auto_discovery:
                dc = threading.Thread(target=self._discovery_loop, args=(int(port),), daemon=True, name="lan-udp")
                self._disc_thread = dc
                dc.start()

        # Ждём реального старта uvicorn (до 4 секунд), чтобы статус/тесты не ловили гонку
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if srv.started:
                break
            if not t.is_alive():
                _lan_log(f"ОШИБКА запуска HTTP LAN-сервера на {self._bind_host}:{int(port)}", logging.ERROR)
                self.stop()
                return False
            time.sleep(0.02)
        if srv.started:
            ifaces_desc = ", ".join(f"{x['adapter']}={x['ip']}/{x['prefix']}" for x in local_ipv4s()) or "нет физических адаптеров"
            _lan_log(f"HTTP LAN-сервер запущен на {self._bind_host}:{int(port)} (auto_discovery={bool(auto_discovery)}; адаптеры: {ifaces_desc})")
            if self._bind_host != "127.0.0.1":
                self.refresh_firewall_async(int(port))
        return bool(srv.started)

    def stop(self):
        with self._lock:
            was_port = self._running_port
            self._stop_evt.set()
            self._hb_evt.set()
            self._outbox_evt.set()
            srv = self._server
            threads = [self._server_thread, self._hb_thread, self._outbox_thread, self._disc_thread]
            self._server = None
            self._server_thread = None
            self._hb_thread = None
            self._outbox_thread = None
            self._disc_thread = None
            self._running_port = None
            self._running_disc = False
            self._udp_listening = False
            self._rx_sock = None
            self._udp_started_mono = 0.0
            self._scanning_subnet = False
            self._newer_db_peer = None
            self._bootstrapped_peers.clear()
            for rt in self._peer_runtime.values():
                rt["online"] = False
                rt["has_newer_db"] = False
                rt["rtt_ms"] = None
                rt["error"] = "LAN выключена"
            if srv is not None:
                srv.should_exit = True
        for thr in threads:
            if thr is not None and thr.is_alive():
                thr.join(timeout=2.5)
        self.cleanup_stale_sessions(max_age_sec=0)
        if was_port is not None:
            _lan_log(f"LAN-сервер на порту {was_port} остановлен")

    def is_listening(self) -> bool:
        with self._lock:
            return bool(
                self._server is not None
                and self._server.started
                and self._server_thread is not None
                and self._server_thread.is_alive()
            )

    def is_udp_listening(self) -> bool:
        with self._lock:
            return bool(
                self._udp_listening
                and self._disc_thread is not None
                and self._disc_thread.is_alive()
            )

    def _broadcast_targets(self) -> set[str]:
        if self._bind_host == "127.0.0.1":
            return {"127.0.0.1"}
        targets = {"255.255.255.255", "127.0.0.1"}
        for iface in local_ipv4s():
            bcast = iface.get("broadcast") or compute_subnet_broadcast(iface.get("ip") or "", int(iface.get("prefix") or 24))
            if bcast:
                targets.add(bcast)
        return targets

    def _open_bound_bcast_sockets(self, bind_port: int = 0) -> list[tuple[str, list[str], socket.socket]]:
        """Открывает по одному UDP-сокету с SO_BROADCAST, привязанному к IP каждого физического адаптера.
        Если задан bind_port (например, 8767), привязывается к (ip, bind_port) с SO_REUSEADDR."""
        out: list[tuple[str, list[str], socket.socket]] = []
        eff_port = int(bind_port or 0)
        if self._bind_host == "127.0.0.1":
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                if eff_port:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                _disable_udp_connreset(s)
                try:
                    s.bind(("127.0.0.1", eff_port))
                except OSError:
                    s.bind(("127.0.0.1", 0))
                s.settimeout(0.25)
                out.append(("127.0.0.1", ["127.0.0.1"], s))
            except OSError as e:
                _lan_log(f"Ошибка открытия loopback UDP-сокета: {e}", logging.WARNING, rate_key="bcast_lo", min_interval=30.0)
            return out

        ifaces = local_ipv4s()
        for iface in ifaces:
            ip_s = str(iface.get("ip") or "").strip()
            if not ip_s:
                continue
            bcast = str(iface.get("broadcast") or "").strip()
            dsts = []
            if bcast:
                dsts.append(bcast)
            if "255.255.255.255" not in dsts:
                dsts.append("255.255.255.255")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                if eff_port:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                _disable_udp_connreset(s)
                try:
                    s.bind((ip_s, eff_port))
                except OSError:
                    s.bind((ip_s, 0))
                s.settimeout(0.25)
                out.append((f"{iface.get('adapter') or 'LAN'}({ip_s})", dsts, s))
            except OSError as e:
                _lan_log(
                    f"Ошибка привязки UDP broadcast-сокета к {iface.get('adapter')} ({ip_s}): {e}",
                    logging.WARNING,
                    rate_key=f"bind_bcast_{ip_s}",
                    min_interval=30.0,
                )

        if not out:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                if eff_port:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                _disable_udp_connreset(s)
                try:
                    s.bind(("", eff_port))
                except OSError:
                    s.bind(("", 0))
                s.settimeout(0.25)
                out.append(("fallback(0.0.0.0)", ["255.255.255.255", "127.0.0.1"], s))
            except OSError as e:
                _lan_log(f"Ошибка открытия резервного UDP-сокета: {e}", logging.WARNING, rate_key="bcast_fb", min_interval=30.0)
        return out

    def _wait_udp_ready(self, max_wait: float = 0.5):
        """Ждёт привязки основного сокета self._rx_sock сразу после запуска потока _discovery_loop."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline and not self._stop_evt.is_set():
            with self._lock:
                if self._rx_sock is not None and self._udp_listening:
                    return self._rx_sock
            time.sleep(0.02)
        with self._lock:
            return self._rx_sock

    def _send_limited_bcast_per_adapter(self, lan_port: int, disc_port: int, payload: bytes, ports: set[int]):
        """Дополнительно отправляет 255.255.255.255 с IP:disc_port каждого вторичного физического адаптера,
        мгновенно закрывая сокет после вычитки буфера, чтобы все входящие ответы шли в основной self._rx_sock."""
        if self._bind_host == "127.0.0.1":
            return
        ifaces = local_ipv4s()
        for iface in ifaces:
            ip_s = str(iface.get("ip") or "").strip()
            if not ip_s:
                continue
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                _disable_udp_connreset(s)
                s.bind((ip_s, int(disc_port)))
                s.setblocking(False)
                for p in ports:
                    try:
                        s.sendto(payload, ("255.255.255.255", int(p)))
                    except OSError:
                        pass
                for _ in range(4):
                    try:
                        raw_d, addr_d = s.recvfrom(4096)
                        self._handle_discovery_packet(raw_d, addr_d[0], addr_d[1], my_lan_port=lan_port)
                    except (BlockingIOError, OSError):
                        break
            except OSError:
                pass
            finally:
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def _reprobe_quiet_discovered(self, lan_port: int, disc_port: int, rx_sock):
        """Поддерживает актуальность записей устройств с односторонним broadcast (например, 1.7 с VPN на стороне партнёра),
        пока окно сопряжения открыто и пользователь вводит PIN."""
        if rx_sock is None:
            return
        cutoff = time.time() - DISCOVERY_TTL_SEC
        with self._lock:
            quiet_targets = [
                (str(v.get("ip") or ""), int(v.get("port") or 8766))
                for v in self._discovered.values()
                if v.get("seen_ts", 0) >= cutoff and not v.get("seen_broadcast") and v.get("ip")
            ]
        if not quiet_targets:
            return
        try:
            ident = self._identity()
        except Exception:
            return
        probe_b = json.dumps({
            "magic": _PROBE_MAGIC,
            "device_id": ident["device_id"],
            "device_name": ident["device_name"],
            "port": int(lan_port),
            "disc_port": int(disc_port),
            "platform": _local_platform_name(),
            "app_version": current_lan_app_version(),
            "probe_kind": "unicast",
        }, ensure_ascii=False).encode("utf-8")
        now_m = time.monotonic()
        for ip_s, pport in quiet_targets:
            if not _is_lan_ip(ip_s):
                continue
            with self._lock:
                self._last_unicast_probe_by_ip[ip_s] = now_m
            dst_ports = {pport + 1}
            if self._bind_host != "127.0.0.1":
                dst_ports.add(8767)
            for dp in dst_ports:
                if 1 <= dp <= 65535:
                    try:
                        rx_sock.sendto(probe_b, (ip_s, dp))
                    except OSError:
                        pass

    def _discovery_loop(self, lan_port: int):
        disc_port = lan_port + 1
        bind_ip = self._bind_host if self._bind_host == "127.0.0.1" else ""
        rx = None
        try:
            rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                rx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            _disable_udp_connreset(rx)
            rx.bind((bind_ip, disc_port))
            rx.settimeout(0.8)
            with self._lock:
                self._rx_sock = rx
                self._udp_listening = True
                self._udp_started_mono = time.monotonic()
            _lan_log(f"UDP discovery слушает {bind_ip or '0.0.0.0'}:{disc_port}")
        except OSError as e:
            _lan_log(f"ОШИБКА bind UDP discovery на {bind_ip or '0.0.0.0'}:{disc_port}: {e}", logging.ERROR)
            if rx is not None:
                try:
                    rx.close()
                except OSError:
                    pass
            rx = None
            with self._lock:
                self._rx_sock = None
                self._udp_listening = False
                self._udp_started_mono = 0.0

        # Активный зонд сразу при запуске LAN (с порта disc_port=8767) + публикация собственного присутствия
        self._send_udp_probe(lan_port, reply_disc_port=disc_port, tx_sock=rx)
        self._broadcast_presence(lan_port, disc_port, tx_sock=rx)
        last_bcast = time.monotonic()
        last_quiet_reprobe = last_bcast

        while not self._stop_evt.is_set():
            now_m = time.monotonic()
            if now_m - last_bcast >= DISCOVERY_INTERVAL_SEC:
                last_bcast = now_m
                self._broadcast_presence(lan_port, disc_port, tx_sock=rx)
            if rx is not None and (now_m - last_quiet_reprobe) >= QUIET_PEER_REPROBE_SEC:
                last_quiet_reprobe = now_m
                self._reprobe_quiet_discovered(lan_port, disc_port, rx)
            if rx is not None:
                try:
                    raw, addr = rx.recvfrom(4096)
                    self._handle_discovery_packet(raw, addr[0], addr[1], my_lan_port=lan_port, reply_sock=rx)
                except socket.timeout:
                    pass
                except ConnectionResetError:
                    # На Windows ICMP Port Unreachable от закрытых портов при unicast-обходе не должен рвать цикл приёма
                    continue
                except OSError as e:
                    if getattr(e, "winerror", None) in (10054, 10052, 10065):
                        continue
                    if not self._stop_evt.is_set():
                        _lan_log(f"Ошибка чтения UDP discovery сокета: {e}", logging.WARNING, rate_key="rx_recv", min_interval=30.0)
                    break
            else:
                self._stop_evt.wait(0.8)

            # Чистим устаревшие записи обнаружения
            cutoff = time.time() - DISCOVERY_TTL_SEC
            with self._lock:
                stale = [k for k, v in self._discovered.items() if v.get("seen_ts", 0) < cutoff]
                for k in stale:
                    self._discovered.pop(k, None)

        with self._lock:
            self._udp_listening = False
            self._rx_sock = None
            self._udp_started_mono = 0.0
        if rx is not None:
            try:
                rx.close()
            except OSError:
                pass

    def _presence_payload(self, lan_port: int, *, is_bcast: bool = False, is_reply: bool = False) -> bytes | None:
        try:
            ident = self._identity()
        except Exception:
            return None
        obj = {
            "magic": _DISCOVERY_MAGIC,
            "device_id": ident["device_id"],
            "device_name": ident["device_name"],
            "port": int(lan_port),
            "disc_port": int(lan_port) + 1,
            "platform": _local_platform_name(),
            "app_version": current_lan_app_version(),
        }
        if is_bcast:
            obj["is_bcast"] = True
        if is_reply:
            obj["is_reply"] = True
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def _send_probe_reply(
        self,
        lan_port: int,
        primary_dst: tuple[str, int],
        *,
        reply_sock=None,
        extra_dst: tuple[str, int] | None = None,
    ):
        """Отвечает на RW_PROBE_V1 с того же сокета rx (порт 8767), на который пришёл зонд,
        чтобы ответ проходил брандмауэр и доходил на открытый порт ищущего компьютера."""
        payload = self._presence_payload(lan_port, is_reply=True)
        if not payload:
            return
        sock = reply_sock
        if sock is None:
            with self._lock:
                sock = self._rx_sock
        own_sock = False
        if sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                _disable_udp_connreset(sock)
                sock.settimeout(0.5)
                own_sock = True
            except OSError as e:
                _lan_log(f"Не удалось создать UDP-сокет для ответа на зонд {primary_dst}: {e}", logging.WARNING, rate_key="rep_sock", min_interval=30.0)
                return
        try:
            targets = [primary_dst]
            if extra_dst and extra_dst != primary_dst and 1 <= int(extra_dst[1]) <= 65535:
                targets.append(extra_dst)
            for dst in targets:
                try:
                    sock.sendto(payload, dst)
                    src_port = sock.getsockname()[1] if hasattr(sock, "getsockname") else "?"
                    _lan_log(f"Ответ RW_DISCOVER_V1 отправлен с порта {src_port} -> {dst[0]}:{dst[1]}", rate_key=f"rep_{dst[0]}_{dst[1]}", min_interval=2.0)
                except OSError as e:
                    _lan_log(f"Ошибка отправки ответа на зонд -> {dst[0]}:{dst[1]}: {e}", logging.WARNING, rate_key=f"rep_err_{dst[0]}", min_interval=30.0)
        finally:
            if own_sock and sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _broadcast_presence(self, lan_port: int, disc_port: int, unicast_to: tuple[str, int] | None = None,
                            reply_sock=None, tx_sock=None):
        payload = self._presence_payload(lan_port, is_bcast=True)
        if not payload:
            return
        if unicast_to is not None:
            self._send_probe_reply(lan_port, unicast_to, reply_sock=reply_sock)

        ports = {int(disc_port)}
        if self._bind_host != "127.0.0.1":
            ports.add(8767)

        sock = tx_sock
        if sock is None:
            with self._lock:
                sock = self._rx_sock

        if sock is not None:
            for dst in self._broadcast_targets():
                for p in ports:
                    try:
                        sock.sendto(payload, (dst, p))
                    except OSError as e:
                        if dst == "255.255.255.255" and getattr(e, "winerror", None) in (10065, 10051):
                            continue
                        _lan_log(
                            f"Ошибка broadcast присутствия с порта {disc_port} -> {dst}:{p}: {e}",
                            logging.WARNING,
                            rate_key=f"bcast_rx_{dst}",
                            min_interval=60.0,
                        )
            self._send_limited_bcast_per_adapter(lan_port, disc_port, payload, ports)
            return

        bound_list = self._open_bound_bcast_sockets(bind_port=disc_port)
        try:
            for label, dst_ips, tx in bound_list:
                for dst in dst_ips:
                    for p in ports:
                        try:
                            tx.sendto(payload, (dst, p))
                        except OSError as e:
                            _lan_log(
                                f"Ошибка broadcast присутствия с {label} -> {dst}:{p}: {e}",
                                logging.WARNING,
                                rate_key=f"bcast_err_{label}_{dst}",
                                min_interval=60.0,
                            )
        finally:
            for _lbl, _dsts, tx in bound_list:
                try:
                    tx.close()
                except OSError:
                    pass

    def _send_udp_probe(self, lan_port: int, reply_disc_port: int | None = None,
                        extra_ports=(), tx_sock=None, log_probe: bool = False):
        """Отправляет активный UDP-запрос RW_PROBE_V1 с основного порта disc_port (8767),
        чтобы ответы от версий 1.6/1.7 (со случайного порта и с задержкой 1–4 с) приходили на открытый в брандмауэре localport 8767."""
        con = db.connect()
        try:
            ident = self._identity(con)
            peer_rows = [dict(r) for r in con.execute("SELECT ip, port FROM sync_peers")]
        except Exception:
            return
        finally:
            con.close()

        disc_port = int(lan_port) + 1
        rport = int(reply_disc_port or disc_port)
        ports = {disc_port}
        if self._bind_host != "127.0.0.1":
            ports.add(8767)
        for ep in (extra_ports or ()):
            try:
                ep_i = int(ep)
                if 1 <= ep_i <= 65535:
                    ports.add(ep_i)
            except (TypeError, ValueError):
                pass

        peer_unicast: set[tuple[str, int]] = set()
        now_m = time.monotonic()
        for pr in peer_rows:
            pip = _clean_ip_str(pr.get("ip") or "")
            if not _is_lan_ip(pip):
                continue
            try:
                pport = int(pr.get("port") or 8766)
                if 1 <= pport + 1 <= 65535:
                    peer_unicast.add((pip, pport + 1))
                if self._bind_host != "127.0.0.1":
                    peer_unicast.add((pip, 8767))
            except (TypeError, ValueError):
                pass

        payload = json.dumps({
            "magic": _PROBE_MAGIC,
            "device_id": ident["device_id"],
            "device_name": ident["device_name"],
            "port": int(lan_port),
            "disc_port": int(rport),
            "platform": _local_platform_name(),
            "app_version": current_lan_app_version(),
            "probe_kind": "broadcast",
        }, ensure_ascii=False).encode("utf-8")

        with self._lock:
            self._last_bcast_probe_mono = now_m
            for pip, _pp in peer_unicast:
                self._last_unicast_probe_by_ip[pip] = now_m
            sock = tx_sock if tx_sock is not None else self._rx_sock

        if sock is not None:
            dests = {(dst, p) for dst in self._broadcast_targets() for p in ports} | peer_unicast
            src_p = sock.getsockname()[1] if hasattr(sock, "getsockname") else rport
            for addr_pair in sorted(dests):
                try:
                    sock.sendto(payload, addr_pair)
                    if log_probe:
                        _lan_log(f"UDP зонд с порта {src_p} -> {addr_pair[0]}:{addr_pair[1]} (disc_port={rport})")
                except OSError as e:
                    _lan_log(
                        f"Ошибка отправки UDP зонда -> {addr_pair[0]}:{addr_pair[1]}: {e}",
                        logging.WARNING,
                        rate_key=f"probe_err_{addr_pair[0]}",
                        min_interval=30.0,
                    )
            if tx_sock is None or sock is self._rx_sock:
                self._send_limited_bcast_per_adapter(lan_port, rport, payload, ports)
            return

        bound_list = self._open_bound_bcast_sockets(bind_port=rport)
        try:
            for idx_s, (label, dst_ips, bsock) in enumerate(bound_list):
                targets = {(dst, p) for dst in dst_ips for p in ports}
                if idx_s == 0:
                    targets |= peer_unicast
                src_p = bsock.getsockname()[1] if hasattr(bsock, "getsockname") else rport
                for addr_pair in sorted(targets):
                    try:
                        bsock.sendto(payload, addr_pair)
                        if log_probe:
                            _lan_log(f"UDP зонд с {label}:{src_p} -> {addr_pair[0]}:{addr_pair[1]} (disc_port={rport})")
                    except OSError as e:
                        _lan_log(
                            f"Ошибка отправки UDP зонда с {label} -> {addr_pair[0]}:{addr_pair[1]}: {e}",
                            logging.WARNING,
                            rate_key=f"probe_err_{label}_{addr_pair[0]}",
                            min_interval=30.0,
                        )
        finally:
            for _lbl, _dsts, bsock in bound_list:
                try:
                    bsock.close()
                except OSError:
                    pass

    def _unicast_subnet_sweep(
        self,
        lan_port: int,
        *,
        target_ports=(),
        tx_sock=None,
        interfaces: list[dict] | None = None,
        batch_size: int = SUBNET_SWEEP_BATCH,
        batch_pause_sec: float = SUBNET_SWEEP_PAUSE_SEC,
        probe_device_id: str | None = None,
        probe_device_name: str | None = None,
        probe_platform: str | None = None,
    ) -> int:
        """Поадресный unicast-обход подсети каждого физического адаптера с порта disc_port (8767).
        Используется как надёжный фолбэк, когда широковещательные пакеты режутся Wi-Fi роутером или виртуальным коммутатором."""
        disc_port = int(lan_port) + 1
        ports = {disc_port}
        if self._bind_host != "127.0.0.1" or interfaces is not None:
            ports.add(8767)
        for ep in (target_ports or ()):
            try:
                ep_i = int(ep)
                if 1 <= ep_i <= 65535:
                    ports.add(ep_i)
            except (TypeError, ValueError):
                pass

        if interfaces is not None:
            ifaces = list(interfaces)
        elif self._bind_host == "127.0.0.1":
            ifaces = []
        else:
            ifaces = local_ipv4s()
        if not ifaces:
            return 0

        my_ips = {_clean_ip_str(str(x.get("ip") or "")) for x in ifaces if x.get("ip")}
        all_hosts: list[str] = []
        seen_h: set[str] = set()
        for iface in ifaces:
            ip_s = str(iface.get("ip") or "").strip()
            pref = int(iface.get("prefix") or 24)
            for h in compute_subnet_unicast_hosts(ip_s, pref, exclude_ips=my_ips):
                if h not in seen_h:
                    seen_h.add(h)
                    all_hosts.append(h)
        if not all_hosts:
            return 0

        if not probe_device_id or not probe_device_name:
            con = db.connect()
            try:
                ident = self._identity(con)
            finally:
                con.close()
            eff_id = probe_device_id or ident["device_id"]
            eff_name = probe_device_name or ident["device_name"]
        else:
            eff_id = probe_device_id
            eff_name = probe_device_name

        probe_b = json.dumps({
            "magic": _PROBE_MAGIC,
            "device_id": eff_id,
            "device_name": eff_name,
            "port": int(lan_port),
            "disc_port": disc_port,
            "platform": probe_platform or _local_platform_name(),
            "app_version": current_lan_app_version(),
            "probe_kind": "unicast",
        }, ensure_ascii=False).encode("utf-8")

        own_sock = False
        sock = tx_sock
        if sock is None:
            with self._lock:
                sock = self._rx_sock
        if sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                _disable_udp_connreset(sock)
                try:
                    sock.bind(("", disc_port))
                except OSError:
                    sock.bind(("", 0))
                own_sock = True
            except OSError as e:
                _lan_log(f"Не удалось открыть сокет для unicast-обхода подсети: {e}", logging.WARNING)
                return 0

        sent = 0
        sorted_ports = sorted(ports)
        bsize = max(1, int(batch_size or SUBNET_SWEEP_BATCH))
        bpause = max(0.0, float(batch_pause_sec if batch_pause_sec is not None else SUBNET_SWEEP_PAUSE_SEC))
        was_running = self.is_listening()
        try:
            src_p = sock.getsockname()[1] if hasattr(sock, "getsockname") else disc_port
            _lan_log(f"Старт unicast-обхода подсети с порта {src_p}: хостов={len(all_hosts)}, порты={sorted_ports}")
            for idx, host_ip in enumerate(all_hosts):
                if was_running and self._stop_evt.is_set():
                    break
                now_m = time.monotonic()
                with self._lock:
                    self._last_unicast_probe_by_ip[host_ip] = now_m
                for dp in sorted_ports:
                    try:
                        sock.sendto(probe_b, (host_ip, dp))
                        sent += 1
                    except OSError:
                        pass
                if bpause > 0 and (idx + 1) % bsize == 0 and (idx + 1) < len(all_hosts):
                    if was_running and self._stop_evt.is_set():
                        break
                    time.sleep(bpause)
        finally:
            if own_sock and sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        _lan_log(f"Unicast-обход подсети отправлен: пакетов={sent} по {len(all_hosts)} адресам")
        return sent

    def _verify_and_update_peer_endpoint(self, dev_id: str, cand_ip: str, cand_port: int, hint_name: str = "") -> bool:
        """Проверяет новый IP/порт уже сопряжённого устройства по токену и /sync/status перед обновлением БД."""
        if not _is_lan_ip(cand_ip) or not (1 <= int(cand_port) <= 65535):
            return False
        con = db.connect()
        try:
            ident = self._identity(con)
            row = con.execute(
                "SELECT device_id, name, ip, port, peer_token FROM sync_peers WHERE device_id=?",
                (dev_id,),
            ).fetchone()
        finally:
            con.close()
        if row is None or not row["peer_token"]:
            return False

        eff_port = int(self._running_port or self._lan_cfg()["port"])
        try:
            st, data = _http_call(
                cand_ip, int(cand_port), "GET", "/sync/status",
                headers={
                    "X-RW-Device": ident["device_id"],
                    "X-RW-Port": str(eff_port),
                    "Authorization": f"Bearer {row['peer_token']}",
                },
                timeout=HEARTBEAT_TIMEOUT_SEC,
            )
        except Exception as e:
            _lan_log(f"Проверка нового адреса пира {cand_ip}:{cand_port} не прошла: {e}", logging.WARNING, rate_key=f"reconn_{cand_ip}", min_interval=30.0)
            return False

        if st != 200 or not isinstance(data, dict) or not data.get("ok"):
            return False
        if str(data.get("device_id") or "") != dev_id:
            return False

        new_name = str(data.get("device_name") or hint_name or row["name"] or dev_id)[:80]
        now_s = _now_ts()
        con = db.connect()
        try:
            con.execute(
                "UPDATE sync_peers SET ip=?, port=?, name=?, last_seen=? WHERE device_id=?",
                (cand_ip, int(cand_port), new_name, now_s, dev_id),
            )
            con.commit()
        finally:
            con.close()

        _lan_log(f"Обновлён адрес сопряжённого устройства «{new_name}» -> {cand_ip}:{int(cand_port)}")
        runner.manager.log("lan", f"обновлён адрес устройства «{new_name}» -> {cand_ip}:{int(cand_port)}")
        self._hb_evt.set()
        self._outbox_evt.set()
        return True

    def _handle_discovery_packet(self, raw: bytes, sender_ip: str, sender_udp_port: int = 0,
                                 my_lan_port: int | None = None, sync_verify: bool = False, reply_sock=None):
        if not _is_lan_ip(sender_ip):
            return
        try:
            pkt = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        if not isinstance(pkt, dict):
            return
        magic = pkt.get("magic")
        if magic not in (_DISCOVERY_MAGIC, _PROBE_MAGIC):
            return
        dev_id = str(pkt.get("device_id") or "").strip()
        if not dev_id:
            return
        clean_ip = _clean_ip_str(sender_ip)
        src_udp = int(sender_udp_port or 0)

        with self._lock:
            listeners = list(self._diag_listeners)
        for cb in listeners:
            try:
                cb(pkt, clean_ip, src_udp)
            except Exception:
                pass

        con = db.connect()
        try:
            ident = self._identity(con)
            paired_row = con.execute(
                "SELECT device_id, name, ip, port FROM sync_peers WHERE device_id=?",
                (dev_id,),
            ).fetchone()
        except Exception:
            return
        finally:
            con.close()

        eff_my_port = int(my_lan_port or self._running_port or self._lan_cfg()["port"])
        try:
            pkt_port_raw = int(pkt.get("port") or 0)
        except (TypeError, ValueError):
            pkt_port_raw = 0

        if dev_id == ident["device_id"]:
            if self._is_self_endpoint(clean_ip, pkt_port_raw, eff_my_port):
                return
            # На другом компьютере в сети клонирован наш device_id (например, после копирования папки или импорта базы):
            # автоматически меняем свой ID на уникальный и продолжаем обработку пакета партнёра!
            ident = self._heal_collided_device_id(clean_ip, str(pkt.get("device_name") or ""))
            paired_row = None
            if self._lan_cfg()["enabled"]:
                self._broadcast_presence(eff_my_port, eff_my_port + 1)

        if magic == _PROBE_MAGIC:
            # Отвечаем с того же сокета rx (порт 8767), на который поступил зонд
            if self._lan_cfg()["enabled"]:
                try:
                    reply_port = int(pkt.get("disc_port") or src_udp or (eff_my_port + 1))
                except (TypeError, ValueError):
                    reply_port = int(src_udp or (eff_my_port + 1))
                tkey = f"{clean_ip}:{dev_id}:{reply_port}"
                now_m = time.monotonic()
                with self._lock:
                    prev_ts = self._probe_reply_ts.get(tkey, 0.0)
                    should_reply = (now_m - prev_ts) >= 0.2
                    if should_reply:
                        self._probe_reply_ts[tkey] = now_m
                if should_reply and (1 <= reply_port <= 65535):
                    extra_dst = (
                        (clean_ip, src_udp)
                        if src_udp and 1 <= src_udp <= 65535 and src_udp != reply_port
                        else None
                    )
                    self._send_probe_reply(
                        eff_my_port,
                        (clean_ip, reply_port),
                        reply_sock=reply_sock,
                        extra_dst=extra_dst,
                    )
            # Служебные диагностические зонды diag-* не добавляем в список реальных устройств,
            # а настоящие несопряжённые компьютеры, сами приславшие RW_PROBE_V1, добавляем в discovered!
            if dev_id.startswith("diag-") or str(pkt.get("platform") or "") == "diag":
                return

        try:
            port = int(pkt.get("port") or 8766)
        except (TypeError, ValueError):
            return
        if not (1 <= port <= 65535):
            return
        name = str(pkt.get("device_name") or dev_id)[:80]

        if paired_row is not None:
            with self._lock:
                self._discovered.pop(dev_id, None)
                was_online = bool((self._peer_runtime.get(dev_id) or {}).get("online"))
            if clean_ip != paired_row["ip"] or port != int(paired_row["port"]):
                if sync_verify:
                    self._verify_and_update_peer_endpoint(dev_id, clean_ip, port, name)
                else:
                    threading.Thread(
                        target=self._verify_and_update_peer_endpoint,
                        args=(dev_id, clean_ip, port, name),
                        daemon=True,
                        name="lan-reconn",
                    ).start()
            elif not was_online:
                self._hb_evt.set()
            return

        raw_ver = str(pkt.get("app_version") or "").strip()
        expected_disc = port + 1
        from_fixed_port = (src_udp == expected_disc)
        now_m = time.monotonic()
        if magic == _DISCOVERY_MAGIC:
            if pkt.get("is_bcast") is True:
                spontaneous_bcast = True
            elif pkt.get("is_reply") is True:
                spontaneous_bcast = False
            else:
                with self._lock:
                    last_p = max(self._last_bcast_probe_mono, self._last_unicast_probe_by_ip.get(clean_ip, 0.0))
                spontaneous_bcast = (now_m - last_p) > 5.0
        else:
            spontaneous_bcast = False

        with self._lock:
            # Если тот же узел (ip:port) сменил device_id (например, при автоустранении клона ID), убираем старую запись
            for old_id, old_v in list(self._discovered.items()):
                if old_id != dev_id and old_v.get("ip") == clean_ip and int(old_v.get("port") or 0) == port:
                    self._discovered.pop(old_id, None)
            prev = self._discovered.get(dev_id) or {}
            is_new = dev_id not in self._discovered
            app_ver = raw_ver or str(prev.get("app_version") or "")
            seen_bcast = bool(prev.get("seen_broadcast")) or spontaneous_bcast
            seen_fixed = bool(prev.get("seen_fixed_port")) or (magic == _DISCOVERY_MAGIC and from_fixed_port)
            if app_ver or seen_fixed:
                legacy_proto = False
            elif magic == _DISCOVERY_MAGIC and src_udp not in (0, expected_disc):
                legacy_proto = True
            else:
                legacy_proto = bool(prev.get("legacy_protocol", False))
            self._discovered[dev_id] = {
                "device_id": dev_id,
                "device_name": name,
                "ip": clean_ip,
                "port": port,
                "platform": str(pkt.get("platform") or prev.get("platform") or "desktop"),
                "app_version": app_ver,
                "legacy_protocol": legacy_proto,
                "seen_broadcast": seen_bcast,
                "seen_fixed_port": seen_fixed,
                "via_probe_only": not seen_bcast,
                "last_udp_port": src_udp or int(prev.get("last_udp_port") or 0),
                "seen_ts": time.time(),
            }
        if is_new:
            ver_tag = f", v{app_ver}" if app_ver else (", legacy<=1.7" if legacy_proto else "")
            _lan_log(f"Обнаружено устройство в сети: «{name}» ({clean_ip}:{port}, id={dev_id}, src_udp={src_udp}{ver_tag})")

    def discover_now(
        self,
        wait_sec: float = 4.5,
        auto_enable: bool = True,
        target_ports=(),
        mode: str = "auto",
        interfaces: list[dict] | None = None,
    ):
        """Активный поиск устройств с порта disc_port (8767) и автоматическим поадресным обходом подсети при отсутствии ответов на broadcast."""
        wait_f = max(0.2, min(float(wait_sec if wait_sec is not None else 4.5), 10.0))
        eff_mode = str(mode or "auto").strip().lower()
        if eff_mode not in ("auto", "broadcast_only", "subnet_only"):
            eff_mode = "auto"

        cfg = self._lan_cfg()
        if not cfg["enabled"] and auto_enable:
            full_cfg = settings.load()
            lan_sec = dict(full_cfg.get("lan") or {})
            lan_sec["enabled"] = True
            lan_sec["auto_discovery"] = True
            full_cfg["lan"] = lan_sec
            settings.save(full_cfg)
            self.reconfigure()
            cfg = self._lan_cfg()

        if cfg["enabled"]:
            lan_port = int(self._running_port or cfg["port"])
            disc_port = lan_port + 1
            rx_sock = self._wait_udp_ready(0.5) if cfg.get("auto_discovery") else None

            con = db.connect()
            try:
                my_id = self._identity(con)["device_id"]
            finally:
                con.close()

            with self._lock:
                before_ids = set(self._discovered.keys())
                before_online = sum(1 for v in self._peer_runtime.values() if v.get("online"))

            wake_evt = threading.Event()
            seen_remote: set[str] = set()

            def _on_pkt(pkt: dict, _ip: str, _sp: int):
                did = str(pkt.get("device_id") or "")
                if did and did != my_id and not did.startswith("diag-") and pkt.get("platform") != "diag":
                    seen_remote.add(did)
                    wake_evt.set()

            with self._lock:
                self._diag_listeners.append(_on_pkt)

            temp_socks: list[tuple[str, list[str], socket.socket]] = []
            was_running_disc = self.is_listening()
            try:
                def _wait_for_replies(phase_timeout: float, resend_cb=None):
                    deadline = time.monotonic() + phase_timeout
                    mid = time.monotonic() + min(1.6, phase_timeout * 0.45)
                    resent = False
                    settle_until: float | None = None
                    while time.monotonic() < deadline and not (was_running_disc and self._stop_evt.is_set()):
                        now_t = time.monotonic()
                        if settle_until is not None and now_t >= settle_until:
                            break
                        if resend_cb is not None and not resent and now_t >= mid and not seen_remote:
                            resent = True
                            resend_cb()
                        if temp_socks:
                            s_list = [s for _l, _d, s in temp_socks]
                            try:
                                rdy, _, _ = select.select(s_list, [], [], 0.12)
                            except (OSError, ValueError):
                                break
                            for rs in rdy:
                                try:
                                    raw, addr = rs.recvfrom(4096)
                                    self._handle_discovery_packet(raw, addr[0], addr[1], my_lan_port=lan_port, sync_verify=True, reply_sock=rs)
                                except OSError:
                                    pass
                        else:
                            rem = min(0.15, max(0.01, deadline - now_t))
                            wake_evt.wait(rem)
                            wake_evt.clear()
                        with self._lock:
                            cur_new = set(self._discovered.keys()) - before_ids
                        if (cur_new or seen_remote) and settle_until is None:
                            settle_until = min(deadline, time.monotonic() + 0.30)

                if rx_sock is None:
                    temp_socks = self._open_bound_bcast_sockets(bind_port=disc_port)

                primary_tx = rx_sock if rx_sock is not None else (temp_socks[0][2] if temp_socks else None)

                if eff_mode in ("auto", "broadcast_only"):
                    _lan_log(f"Старт активного поиска (mode={eff_mode}, ожидание до {wait_f:.1f} с, порт={disc_port})")
                    self._send_udp_probe(lan_port, reply_disc_port=disc_port, extra_ports=target_ports, tx_sock=rx_sock, log_probe=True)
                    self._broadcast_presence(lan_port, disc_port, tx_sock=rx_sock)
                    _wait_for_replies(
                        wait_f,
                        resend_cb=lambda: self._send_udp_probe(lan_port, reply_disc_port=disc_port, extra_ports=target_ports, tx_sock=rx_sock),
                    )

                with self._lock:
                    found_after_bcast = len(self._discovered) > len(before_ids) or bool(seen_remote)
                    cur_online = sum(1 for v in self._peer_runtime.values() if v.get("online"))

                need_sweep = (
                    eff_mode == "subnet_only"
                    or (eff_mode == "auto" and not found_after_bcast and cur_online <= before_online and (self._bind_host != "127.0.0.1" or interfaces is not None))
                )
                if need_sweep:
                    with self._lock:
                        self._scanning_subnet = True
                    try:
                        sent_cnt = self._unicast_subnet_sweep(
                            lan_port,
                            target_ports=target_ports,
                            tx_sock=primary_tx,
                            interfaces=interfaces,
                        )
                        if sent_cnt > 0:
                            wake_evt.clear()
                            _wait_for_replies(min(wait_f, 4.5))
                    finally:
                        with self._lock:
                            self._scanning_subnet = False
            finally:
                with self._lock:
                    if _on_pkt in self._diag_listeners:
                        self._diag_listeners.remove(_on_pkt)
                for _lbl, _dsts, s in temp_socks:
                    try:
                        s.close()
                    except OSError:
                        pass
            try:
                self.poll_peers_once()
            except Exception:
                pass

        res = self.status(refresh=False)
        _lan_log(f"Активный поиск завершён (mode={eff_mode}): новых={len(res.get('discovered') or [])}, онлайн сопряжённых={res.get('online_peers', 0)}")
        return res

    def _heartbeat_loop(self):
        # Первый опрос почти сразу после старта
        self._stop_evt.wait(0.25)
        while not self._stop_evt.is_set():
            try:
                self.poll_peers_once()
                self.cleanup_stale_sessions()
            except Exception:
                pass
            self._hb_evt.wait(timeout=HEARTBEAT_INTERVAL_SEC)
            self._hb_evt.clear()

    def poll_peers_once(self):
        """Опрашивает /sync/status у всех сопряжённых устройств и обновляет их онлайн-статус."""
        con = db.connect()
        try:
            ident = self._identity(con)
            peers = [dict(r) for r in con.execute("SELECT * FROM sync_peers")]
        finally:
            con.close()
        if not peers:
            return {}

        eff_port = str(self._running_port or self._lan_cfg()["port"])

        def check(p):
            dev_id = p["device_id"]
            t0 = time.monotonic()
            try:
                st, data = _http_call(
                    p["ip"], p["port"], "GET", "/sync/status",
                    headers={
                        "X-RW-Device": ident["device_id"],
                        "X-RW-Port": eff_port,
                        "Authorization": f"Bearer {p['peer_token'] or ''}",
                    },
                    timeout=HEARTBEAT_TIMEOUT_SEC,
                )
                if st == 200 and data.get("ok"):
                    return dev_id, {
                        "online": True,
                        "last_check": _now_ts(),
                        "rtt_ms": round((time.monotonic() - t0) * 1000),
                        "device_name": data.get("device_name") or p["name"],
                        "latest_db_ts": data.get("latest_db_ts"),
                        "freshness": data.get("freshness") or {},
                        "listings_count": data.get("listings_count"),
                        "likes_count": data.get("likes_count"),
                        "marks_counts": data.get("marks_counts") if isinstance(data.get("marks_counts"), dict) else None,
                        "busy": bool(data.get("busy")),
                        "platform": data.get("platform") or "",
                        "error": None,
                    }
                return dev_id, {
                    "online": False,
                    "last_check": _now_ts(),
                    "rtt_ms": None,
                    "device_name": p["name"],
                    "error": data.get("detail") or f"HTTP {st}",
                }
            except Exception as e:
                _lan_log(
                    f"Не удалось опросить пира «{p['name']}» ({p['ip']}:{p['port']}): {e}",
                    logging.WARNING,
                    rate_key=f"hb_err_{dev_id}_{p['ip']}",
                    min_interval=120.0,
                )
                return dev_id, {
                    "online": False,
                    "last_check": _now_ts(),
                    "rtt_ms": None,
                    "device_name": p["name"],
                    "error": str(e),
                }

        results = {}
        with ThreadPoolExecutor(max_workers=min(6, len(peers))) as pool:
            for dev_id, rt in pool.map(check, peers):
                results[dev_id] = rt

        now_s = _now_ts()
        came_online = False
        con = db.connect()
        try:
            local_f = exchange._db_freshness(con)
            best_newer = None
            for dev_id, rt in results.items():
                if rt.get("online"):
                    con.execute(
                        "UPDATE sync_peers SET name=?, last_seen=?, last_db_ts=? WHERE device_id=?",
                        (rt.get("device_name") or dev_id, now_s, rt.get("latest_db_ts"), dev_id),
                    )
                    peer_f = rt.get("freshness") or {}
                    cmp_res = _compare_freshness(local_f, peer_f)
                    rt["has_newer_db"] = bool(cmp_res["is_older"])
                    rt["newer_sources"] = [o["label"] for o in cmp_res.get("older_sources") or []]
                    if rt["has_newer_db"]:
                        cand = {
                            "device_id": dev_id,
                            "name": rt.get("device_name") or dev_id,
                            "latest_db_ts": rt.get("latest_db_ts"),
                            "newer_sources": rt["newer_sources"],
                        }
                        if best_newer is None or str(cand["latest_db_ts"] or "") > str(best_newer.get("latest_db_ts") or ""):
                            best_newer = cand
                else:
                    rt["has_newer_db"] = False
                    rt["newer_sources"] = []
            con.commit()
            q_cnt = con.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE payload_path IS NOT NULL AND payload_path != ''"
            ).fetchone()[0]
        finally:
            con.close()

        transitions: list[tuple[str, str, bool]] = []
        with self._lock:
            for dev_id, rt in results.items():
                now_on = bool(rt.get("online"))
                prev_on = bool((self._peer_runtime.get(dev_id) or {}).get("online"))
                was_bootstrapped = dev_id in self._bootstrapped_peers
                self._bootstrapped_peers.add(dev_id)
                if was_bootstrapped and prev_on != now_on:
                    transitions.append((dev_id, str(rt.get("device_name") or dev_id), now_on))
                if now_on and (not prev_on or q_cnt > 0):
                    came_online = True
                self._peer_runtime[dev_id] = rt
            self._newer_db_peer = best_newer
            self._outbox_count = q_cnt
        for dev_id, pname, now_on in transitions:
            self._push_live_event("peer_state", {"device_id": dev_id, "name": pname, "online": now_on})
        if came_online:
            self._outbox_evt.set()
        return results

    # ---------- офлайн-очередь sync_outbox ----------

    def _outbox_loop(self):
        self._stop_evt.wait(0.6)
        while not self._stop_evt.is_set():
            try:
                self.flush_outbox()
            except Exception:
                pass
            self._outbox_evt.wait(timeout=12.0)
            self._outbox_evt.clear()

    def _cleanup_orphan_outbox_files(self, con):
        odir = self._outbox_dir()
        if not odir.is_dir():
            return
        keep = {
            str(r[0]) for r in con.execute("SELECT DISTINCT payload_path FROM sync_outbox WHERE payload_path IS NOT NULL AND payload_path != ''")
        }
        for f in odir.iterdir():
            if f.is_file() and str(f) not in keep:
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass

    def flush_outbox(self):
        """Доставляет отложенные live-события пирам из sync_outbox, когда устройства в сети."""
        with self._flush_lock:
            return self._flush_outbox_locked()

    def _flush_outbox_locked(self):
        if not self._lan_cfg()["enabled"]:
            return {"delivered": 0, "pending": 0}
        con = db.connect()
        try:
            ident = self._identity(con)
            rows = [dict(r) for r in con.execute(
                """SELECT o.id, o.peer_id, o.kind, o.event_id, o.payload_path, o.tries,
                          p.ip, p.port, p.peer_token, p.live_cfg_json
                   FROM sync_outbox o
                   LEFT JOIN sync_peers p ON p.device_id = o.peer_id
                   WHERE o.payload_path IS NOT NULL AND o.payload_path != ''
                   ORDER BY o.id"""
            )]
        finally:
            con.close()

        route_map = {
            "live_fav": "/sync/live/favorite",
            "live_note": "/sync/live/note",
            "live_merge": "/sync/live/merge",
        }
        delivered = 0
        dropped_ids = []
        inc_tries_ids = []
        for row in rows:
            oid = row["id"]
            route = route_map.get(row["kind"])
            ppath = Path(row["payload_path"])
            if not route or not row["ip"] or not ppath.is_file():
                dropped_ids.append(oid)
                continue
            try:
                body = ppath.read_bytes()
                hdrs = {
                    "Content-Type": "application/json",
                    "X-RW-Device": ident["device_id"],
                    "X-RW-Port": str(self._lan_cfg()["port"]),
                    "Authorization": f"Bearer {row['peer_token'] or ''}",
                }
                st, data = _http_call(row["ip"], row["port"], "POST", route, body=body, headers=hdrs, timeout=2.5)
                if st == 200 and data.get("ok") is not None:
                    delivered += 1
                    dropped_ids.append(oid)
                    continue
            except Exception:
                pass
            # Офлайн или ошибка: оставляем только если у пира включена офлайн-очередь
            lcfg = _normalize_live_cfg(row.get("live_cfg_json"))
            if lcfg.get("outbox_enabled", True):
                inc_tries_ids.append(oid)
            else:
                dropped_ids.append(oid)

        con = db.connect()
        try:
            for oid in dropped_ids:
                con.execute("DELETE FROM sync_outbox WHERE id=?", (oid,))
            for oid in inc_tries_ids:
                con.execute("UPDATE sync_outbox SET tries = COALESCE(tries,0) + 1 WHERE id=?", (oid,))
            con.commit()
            self._cleanup_orphan_outbox_files(con)
            q_cnt = con.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE payload_path IS NOT NULL AND payload_path != ''"
            ).fetchone()[0]
        finally:
            con.close()

        with self._lock:
            self._outbox_count = q_cnt
        return {"delivered": delivered, "pending": q_cnt}

    def _enqueue_live(self, targets: list[dict], kind: str, payload: dict):
        """Записывает live-событие в файл, ставит в sync_outbox для целевых пиров и будит воркер."""
        if not targets or not self._lan_cfg()["enabled"]:
            return
        odir = self._outbox_dir()
        odir.mkdir(parents=True, exist_ok=True)
        event_id = str(payload.get("event_id") or uuid.uuid4().hex)
        payload["event_id"] = event_id
        self._mark_seen_event(event_id)
        pfile = odir / f"{kind}_{event_id}.json"
        pfile.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        now_s = _now_ts()
        con = db.connect()
        try:
            for p in targets:
                con.execute(
                    "INSERT INTO sync_outbox(peer_id, kind, event_id, payload_path, created_at, tries) VALUES(?,?,?,?,?,0)",
                    (p["device_id"], kind, event_id, str(pfile), now_s),
                )
            con.commit()
            q_cnt = con.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE payload_path IS NOT NULL AND payload_path != ''"
            ).fetchone()[0]
        finally:
            con.close()
        with self._lock:
            self._outbox_count = q_cnt
        self._outbox_evt.set()

    # ---------- PIN-код и сопряжение ----------

    def create_pin(self):
        pin = f"{secrets.randbelow(1_000_000):06d}"
        now_t = time.time()
        exp = now_t + PIN_TTL_SEC
        with self._lock:
            self._pin_state = {"pin": pin, "expires_at": exp, "fails": 0}
        return {
            "pin": pin,
            "ttl": PIN_TTL_SEC,
            "remaining_sec": PIN_TTL_SEC,
            "expires_at": datetime.fromtimestamp(exp).isoformat(timespec="seconds"),
        }

    def pin_status(self):
        with self._lock:
            st = self._pin_state
            if not st:
                return {"active": False, "remaining_sec": 0}
            rem = int(round(st["expires_at"] - time.time()))
            if rem <= 0:
                self._pin_state = None
                return {"active": False, "remaining_sec": 0}
            return {
                "active": True,
                "pin": st["pin"],
                "remaining_sec": rem,
                "fails": st["fails"],
                "expires_at": datetime.fromtimestamp(st["expires_at"]).isoformat(timespec="seconds"),
            }

    def pair_with(self, ip: str, port: int, pin: str):
        """Сопрягается с устройством по IP:port и одноразовому 6-значному PIN."""
        raw_ip = (ip or "").strip()
        eff_port_in = port
        # Если пользователь скопировал и вставил '192.168.2.15:8766' прямо в поле IP — аккуратно отделяем порт
        if raw_ip.count(":") == 1 and not raw_ip.startswith("["):
            host_part, port_part = raw_ip.rsplit(":", 1)
            if port_part.isdigit():
                raw_ip = host_part.strip()
                eff_port_in = int(port_part)
        clean_ip = _clean_ip_str(raw_ip)
        if not _is_lan_ip(clean_ip):
            raise ValueError("разрешено подключение только к устройствам в локальной сети (LAN)")
        try:
            p = int(eff_port_in)
        except (TypeError, ValueError):
            raise ValueError("некорректный порт устройства")
        if not (1 <= p <= 65535):
            raise ValueError("порт должен быть в диапазоне 1–65535")
        pin_s = re.sub(r"\s+", "", str(pin or ""))
        if not re.fullmatch(r"\d{6}", pin_s):
            raise ValueError("введите 6-значный PIN-код")

        con = db.connect()
        try:
            ident = self._identity(con)
        finally:
            con.close()

        my_cfg = self._lan_cfg()
        if not my_cfg["enabled"]:
            full_cfg = settings.load()
            lan_sec = dict(full_cfg.get("lan") or {})
            lan_sec["enabled"] = True
            lan_sec["auto_discovery"] = True
            full_cfg["lan"] = lan_sec
            settings.save(full_cfg)
            self.reconfigure()
            my_cfg = self._lan_cfg()
        my_port_now = int(self._running_port or my_cfg["port"])
        token_for_peer = secrets.token_urlsafe(32)

        def _send_pair_req(cur_ident: dict):
            req_payload = json.dumps({
                "pin": pin_s,
                "device_id": cur_ident["device_id"],
                "device_name": cur_ident["device_name"],
                "port": my_port_now,
                "token": token_for_peer,
                "platform": _local_platform_name(),
            }, ensure_ascii=False).encode("utf-8")
            return _http_call(
                clean_ip, p, "POST", "/sync/pair", body=req_payload,
                headers={"Content-Type": "application/json"}, timeout=5.0,
            )

        try:
            st, data = _send_pair_req(ident)
        except Exception as e:
            _lan_log(f"Ошибка сопряжения с {clean_ip}:{p}: {e}", logging.WARNING)
            raise ValueError(f"не удалось связаться с {clean_ip}:{p} — {e}")

        # Если второй ПК (например, на 1.8.1) ответил 400 «Нельзя сопрячь устройство с самим собой» из-за клонированного device_id,
        # а адрес/порт принадлежит ДРУГОМУ экземпляру — автоматически меняем свой device_id и сразу повторяем запрос!
        detail_str = str((data or {}).get("detail") or "")
        if (
            st == 400
            and "самим собой" in detail_str.lower()
            and not self._is_self_endpoint(clean_ip, p, my_port_now)
        ):
            ident = self._heal_collided_device_id(clean_ip)
            try:
                st, data = _send_pair_req(ident)
            except Exception as e:
                _lan_log(f"Ошибка повторного сопряжения с {clean_ip}:{p}: {e}", logging.WARNING)
                raise ValueError(f"не удалось связаться с {clean_ip}:{p} — {e}")

        if st != 200 or not data.get("ok"):
            _lan_log(f"Отказ сопряжения от {clean_ip}:{p}: HTTP {st} ({data.get('detail')})", logging.WARNING)
            raise ValueError(data.get("detail") or f"ошибка сопряжения (HTTP {st})")

        peer_id = str(data.get("device_id") or "").strip()
        peer_token = str(data.get("token") or "").strip()
        peer_name = str(data.get("device_name") or clean_ip).strip()
        peer_port = int(data.get("port") or p)
        if not peer_id or not peer_token:
            raise ValueError("удалённое устройство вернуло неполные данные сопряжения")
        if peer_id == ident["device_id"]:
            if self._is_self_endpoint(clean_ip, peer_port, my_port_now):
                raise ValueError("нельзя сопрячь устройство с самим собой")
            ident = self._heal_collided_device_id(clean_ip, peer_name)

        now_s = _now_ts()
        con = db.connect()
        try:
            con.execute(
                """INSERT INTO sync_peers(device_id, name, ip, port, token_hash, peer_token, paired_at, last_seen)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET
                     name=excluded.name, ip=excluded.ip, port=excluded.port,
                     token_hash=excluded.token_hash, peer_token=excluded.peer_token,
                     paired_at=excluded.paired_at, last_seen=excluded.last_seen""",
                (peer_id, peer_name, clean_ip, peer_port, _hash_token(token_for_peer), peer_token, now_s, now_s),
            )
            con.commit()
        finally:
            con.close()

        with self._lock:
            self._discovered.pop(peer_id, None)
            self._bootstrapped_peers.add(peer_id)
            self._peer_runtime[peer_id] = {
                "online": True,
                "last_check": now_s,
                "rtt_ms": None,
                "device_name": peer_name,
                "platform": data.get("platform") or "",
                "error": None,
            }
        _lan_log(f"Успешно сопряжено устройство «{peer_name}» ({clean_ip}:{peer_port}, id={peer_id})")
        runner.manager.log("lan", f"сопряжено устройство «{peer_name}» ({clean_ip}:{peer_port})")
        # Сразу подтягиваем свежий статус пира
        try:
            self.poll_peers_once()
        except Exception:
            pass
        return {"ok": True, "device_id": peer_id, "name": peer_name, "ip": clean_ip, "port": peer_port}

    def delete_peer(self, device_id: str):
        con = db.connect()
        try:
            cur = con.execute("DELETE FROM sync_peers WHERE device_id=?", (device_id,))
            if cur.rowcount == 0:
                raise LanNotFoundError("устройство не найдено")
            con.execute("DELETE FROM sync_outbox WHERE peer_id=?", (device_id,))
            con.commit()
            self._cleanup_orphan_outbox_files(con)
            q_cnt = con.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE payload_path IS NOT NULL AND payload_path != ''"
            ).fetchone()[0]
        finally:
            con.close()
        with self._lock:
            self._peer_runtime.pop(device_id, None)
            self._bootstrapped_peers.discard(device_id)
            if (self._newer_db_peer or {}).get("device_id") == device_id:
                self._newer_db_peer = None
            self._outbox_count = q_cnt
        return {"ok": True, "device_id": device_id}

    def set_peer_live_cfg(self, device_id: str, patch: dict):
        con = db.connect()
        try:
            row = con.execute("SELECT device_id, live_cfg_json FROM sync_peers WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                raise LanNotFoundError("устройство не найдено")
            cur = _normalize_live_cfg(row["live_cfg_json"])
            if isinstance(patch, dict):
                cur = _normalize_live_cfg({**cur, **patch})
            con.execute(
                "UPDATE sync_peers SET live_cfg_json=? WHERE device_id=?",
                (json.dumps(cur, ensure_ascii=False), device_id),
            )
            con.commit()
        finally:
            con.close()
        return {"ok": True, "device_id": device_id, "live_cfg": cur}

    # ---------- статусы для UI и локального API ----------

    def list_peers(self):
        con = db.connect()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT device_id, name, ip, port, paired_at, last_seen, last_db_ts, live_cfg_json "
                "FROM sync_peers ORDER BY paired_at DESC, name"
            )]
        finally:
            con.close()
        with self._lock:
            rt_map = {k: dict(v) for k, v in self._peer_runtime.items()}
        out = []
        for r in rows:
            rt = rt_map.get(r["device_id"]) or {}
            out.append({
                "device_id": r["device_id"],
                "name": rt.get("device_name") or r["name"] or r["device_id"],
                "ip": r["ip"],
                "port": r["port"],
                "paired_at": r["paired_at"],
                "last_seen": r["last_seen"],
                "last_db_ts": rt.get("latest_db_ts") or r["last_db_ts"],
                "online": bool(rt.get("online", False)),
                "rtt_ms": rt.get("rtt_ms"),
                "busy": bool(rt.get("busy", False)),
                "listings_count": rt.get("listings_count"),
                "likes_count": rt.get("likes_count"),
                "marks_counts": rt.get("marks_counts"),
                "has_newer_db": bool(rt.get("has_newer_db", False)),
                "newer_sources": list(rt.get("newer_sources") or []),
                "live_cfg": _normalize_live_cfg(r.get("live_cfg_json")),
                "error": rt.get("error"),
            })
        return out

    def list_discovered(self):
        con = db.connect()
        try:
            paired_ids = {r[0] for r in con.execute("SELECT device_id FROM sync_peers")}
        finally:
            con.close()
        cutoff = time.time() - DISCOVERY_TTL_SEC
        with self._lock:
            items = [dict(v) for v in self._discovered.values() if v.get("seen_ts", 0) >= cutoff]
        return sorted(
            [it for it in items if it["device_id"] not in paired_ids],
            key=lambda x: x.get("device_name") or "",
        )

    def summary(self, lan_after: int = 0):
        """Компактная сводка для /api/status (поллинг каждые 1–2 с)."""
        cfg = self._lan_cfg()
        hunread = self._ensure_history_unread()
        with self._lock:
            self._ensure_inbox_loaded()
            inbox_cnt = len(self._inbox)
            online = sum(1 for v in self._peer_runtime.values() if v.get("online"))
            total_known = len(self._peer_runtime)
            evs = [ev for ev in self._live_events if ev["seq"] > lan_after]
            seq = self._live_seq
            drev = self._data_rev
            ocnt = self._outbox_count
            hrev = self._history_rev
            newer_peer = dict(self._newer_db_peer) if self._newer_db_peer else None
        return {
            "enabled": cfg["enabled"],
            "listening": self.is_listening(),
            "port": self._running_port or cfg["port"],
            "online_peers": online,
            "known_peers": total_known,
            "inbox_count": inbox_cnt,
            "outbox_count": ocnt,
            "history_unread": hunread,
            "history_rev": hrev,
            "seq": seq,
            "data_rev": drev,
            "events": evs,
            "newer_db_peer": newer_peer,
        }

    def refresh_firewall_async(self, lan_port: int | None = None):
        """Фоново проверяет правила брандмауэра и профиль сети без блокировки основного потока."""
        port_i = int(lan_port or self._running_port or self._lan_cfg()["port"])
        with self._lock:
            if self._fw_refreshing:
                return
            self._fw_refreshing = True

        def _worker():
            try:
                res = query_firewall_and_profiles(port_i)
                with self._lock:
                    self._fw_cache = res
                    self._fw_cache_ts = time.monotonic()
                if not res.get("ok"):
                    _lan_log(
                        f"Брандмауэр Windows: требуются правила для {res.get('program')} "
                        f"(TCP {res.get('tcp_port')}={res.get('tcp_allowed')}, UDP {res.get('udp_port')}={res.get('udp_allowed')}, "
                        f"block={res.get('has_block_rules')})",
                        logging.WARNING,
                        rate_key="fw_warn",
                        min_interval=300.0,
                    )
            finally:
                with self._lock:
                    self._fw_refreshing = False

        threading.Thread(target=_worker, daemon=True, name="lan-fw-check").start()

    def get_firewall_status(self, lan_port: int | None = None, *, force: bool = False, sync_if_empty: bool = False) -> dict:
        port_i = int(lan_port or self._running_port or self._lan_cfg()["port"])
        if self._bind_host == "127.0.0.1" and not force:
            primary_prog, _ = _firewall_target_programs()
            return {
                "checked": False,
                "program": primary_prog,
                "tcp_port": port_i,
                "udp_port": port_i + 1,
                "tcp_allowed": True,
                "udp_allowed": True,
                "has_block_rules": False,
                "block_rules": [],
                "ok": True,
                "profiles": [],
                "has_public_profile": False,
                "public_profiles": [],
            }
        now_m = time.monotonic()
        with self._lock:
            cached = self._fw_cache
            age = now_m - self._fw_cache_ts
        if not force and cached is not None and cached.get("tcp_port") == port_i and age < 30.0:
            return dict(cached)
        if force or sync_if_empty:
            fresh = query_firewall_and_profiles(port_i)
            with self._lock:
                self._fw_cache = fresh
                self._fw_cache_ts = time.monotonic()
            return dict(fresh)
        self.refresh_firewall_async(port_i)
        if cached is not None:
            return dict(cached)
        primary_prog, _ = _firewall_target_programs()
        return {
            "checked": False,
            "program": primary_prog,
            "tcp_port": port_i,
            "udp_port": port_i + 1,
            "tcp_allowed": True,
            "udp_allowed": True,
            "has_block_rules": False,
            "block_rules": [],
            "ok": True,
            "profiles": [],
            "has_public_profile": False,
            "public_profiles": [],
        }

    def allow_firewall(self, remove_blocking: bool = False) -> dict:
        """Запрашивает права администратора (UAC) и создаёт входящие правила брандмауэра для pythonw.exe (TCP + UDP)."""
        cfg = self._lan_cfg()
        tcp_p = int(self._running_port or cfg["port"])
        udp_p = tcp_p + 1
        cur_fw = query_firewall_and_profiles(tcp_p)
        if cur_fw.get("supported") is False:
            if cur_fw.get("macos_block_all"):
                raise ValueError(
                    "В настройках macOS включён режим «Блокировать все входящие подключения». "
                    "Откройте «Системные настройки → Сеть → Межсетевой экран» и отключите его."
                )
            return {"ok": True, "firewall": cur_fw}
        primary_prog, all_progs = _firewall_target_programs()
        block_names = [b.get("name") or "" for b in (cur_fw.get("block_rules") or []) if b.get("name")]

        if cur_fw.get("has_block_rules") and not remove_blocking:
            raise ValueError(
                "Для программы найдены блокирующие правила брандмауэра — они перекрывают разрешающие правила. "
                "Отметьте галочку «Удалить блокирующие правила» и нажмите кнопку ещё раз."
            )

        ps_script = build_firewall_netsh_script(
            all_progs,
            tcp_p,
            udp_p,
            remove_blocking=bool(remove_blocking),
            block_rule_names=block_names,
        )
        _lan_log(f"Запуск настройки брандмауэра через UAC: program={primary_prog}, TCP={tcp_p}, UDP={udp_p}, remove_blocking={bool(remove_blocking)}")
        rc = _run_elevated_powershell(ps_script)
        if rc != 0:
            _lan_log(f"Настройка брандмауэра через UAC завершилась с кодом {rc}", logging.ERROR)
            raise RuntimeError(f"Не удалось добавить правила брандмауэра (код netsh: {rc})")

        updated = query_firewall_and_profiles(tcp_p)
        with self._lock:
            self._fw_cache = updated
            self._fw_cache_ts = time.monotonic()
        _lan_log(f"Правила брандмауэра успешно применены: ok={updated.get('ok')}")
        runner.manager.log("lan", f"правила брандмауэра Windows добавлены (TCP {tcp_p}, UDP {udp_p})")
        return {"ok": True, "firewall": updated}

    def open_network_settings(self) -> dict:
        """Открывает системные настройки сети (Системные настройки macOS или Параметры Windows)."""
        if os.name == "nt":
            os.startfile("ms-settings:network-status")
            return {"ok": True}
        if sys.platform == "darwin":
            for target in (
                "x-apple.systempreferences:com.apple.Network-Settings.extension",
                "/System/Library/PreferencePanes/Network.prefPane",
            ):
                try:
                    r = subprocess.run(["open", target], capture_output=True, timeout=5.0)
                    if r.returncode == 0:
                        return {"ok": True}
                except (OSError, subprocess.SubprocessError):
                    continue
            subprocess.Popen(["open", "-a", "System Settings"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        raise ValueError("Открытие системных параметров сети не поддерживается на этой платформе")

    def diagnose_network(
        self,
        peer_ip: str = "",
        peer_port: int | None = None,
        probe_wait_sec: float | None = None,
    ) -> dict:
        """Выполняет полную пошаговую диагностику LAN (адаптер, порты, профиль, брандмауэр, VPN, шлюз, UDP-зонд с порта 8767, TCP пира)."""
        cfg = self._lan_cfg()
        lan_port = int(self._running_port or cfg["port"])
        disc_port = lan_port + 1

        clean_peer = ""
        eff_peer_port = int(peer_port or lan_port)
        raw_peer = (peer_ip or "").strip()
        if raw_peer:
            if raw_peer.count(":") == 1 and not raw_peer.startswith("["):
                hp, pp = raw_peer.rsplit(":", 1)
                if pp.isdigit():
                    raw_peer = hp.strip()
                    eff_peer_port = int(pp)
            clean_peer = _clean_ip_str(raw_peer)

        raw_adapters = _raw_system_adapters()
        ifaces = local_ipv4s(raw_adapters)
        checks: list[dict] = []

        # 1. LAN включена, физический адаптер и локальные порты
        listening_tcp = self.is_listening()
        listening_udp = self.is_udp_listening()
        if not ifaces and self._bind_host != "127.0.0.1":
            checks.append({
                "id": "lan_ports",
                "status": "fail",
                "title": "Не найден подключённый физический адаптер Wi-Fi / Ethernet",
                "detail": "Нет активного IPv4-адреса локальной сети (виртуальные и VPN-адаптеры исключены).",
                "hint": "Проверьте подключение к роутеру по Wi-Fi или кабелю Ethernet.",
                "action": None,
            })
        elif not cfg["enabled"] or not listening_tcp:
            checks.append({
                "id": "lan_ports",
                "status": "fail",
                "title": f"LAN выключена или порт TCP {lan_port} не слушается",
                "detail": "По умолчанию обмен по локальной сети выключен.",
                "hint": "Включите переключатель «Разрешить обмен по локальной сети» на ОБОИХ компьютерах.",
                "action": "enable_lan",
            })
        elif cfg["auto_discovery"] and not listening_udp:
            checks.append({
                "id": "lan_ports",
                "status": "warn",
                "title": f"HTTP порт {lan_port} открыт, но UDP порт обнаружения {disc_port} занят",
                "detail": ", ".join(f"{x['adapter']}: {x['ip']}/{x['prefix']}" for x in ifaces),
                "hint": "Автопоиск может не работать — используйте подключение вручную по IP или смените LAN-порт в настройках.",
                "action": None,
            })
        else:
            iface_txt = ", ".join(f"{x['adapter']} ({x['ip']}/{x['prefix']})" for x in ifaces) or "loopback (127.0.0.1)"
            checks.append({
                "id": "lan_ports",
                "status": "ok",
                "title": f"LAN включена, порты TCP {lan_port} и UDP {disc_port} открыты",
                "detail": f"Физические адаптеры: {iface_txt}",
                "hint": "",
                "action": None,
            })

        # 2 & 3. Профиль сети и межсетевой экран (macOS / Windows)
        fw_info = self.get_firewall_status(lan_port, force=(self._bind_host != "127.0.0.1"))
        if fw_info.get("supported") is False:
            checks.append({
                "id": "profile",
                "status": "ok",
                "title": "Доступ к локальной сети macOS",
                "detail": "Если система запросит разрешение на поиск устройств в локальной сети — нажмите «Разрешить».",
                "hint": "",
                "action": None,
            })
            mac_st = fw_info.get("macos_state") or "unknown"
            if fw_info.get("macos_block_all"):
                checks.append({
                    "id": "firewall",
                    "status": "fail",
                    "title": "Межсетевой экран macOS блокирует все входящие подключения",
                    "detail": "Включён строгий режим блокировки всех входящих соединений.",
                    "hint": "Откройте «Системные настройки → Сеть → Межсетевой экран» и снимите галочку «Блокировать все входящие подключения».",
                    "action": "open_net_settings",
                })
            elif mac_st == "enabled":
                checks.append({
                    "id": "firewall",
                    "status": "ok",
                    "title": "Межсетевой экран macOS работает в штатном режиме",
                    "detail": f"Порты TCP {lan_port} и UDP {disc_port}; при запросе macOS разрешите входящие подключения.",
                    "hint": "",
                    "action": None,
                })
            else:
                checks.append({
                    "id": "firewall",
                    "status": "ok",
                    "title": f"Межсетевой экран macOS не блокирует порты TCP {lan_port} и UDP {disc_port}",
                    "detail": "Ограничений на локальные входящие подключения не обнаружено.",
                    "hint": "",
                    "action": None,
                })
        else:
            if fw_info.get("has_public_profile"):
                pub_names = ", ".join(f"«{p['name']}» ({p['adapter']})" for p in (fw_info.get("public_profiles") or []))
                checks.append({
                    "id": "profile",
                    "status": "warn" if fw_info.get("ok") else "fail",
                    "title": f"Сеть {pub_names} имеет профиль «Общедоступная» (Public)",
                    "detail": "В общедоступной сети брандмауэр Windows по умолчанию блокирует входящие подключения.",
                    "hint": "Переключите профиль сети на «Частная» (Private) в Параметрах Windows (на обоих ПК) и разрешите брандмауэр.",
                    "action": "open_net_settings",
                })
            else:
                prof_names = ", ".join(f"«{p['name']}» ({p['category']})" for p in (fw_info.get("profiles") or [])) or "Частная / Локальная"
                checks.append({
                    "id": "profile",
                    "status": "ok",
                    "title": "Профиль сетевого подключения в порядке",
                    "detail": prof_names,
                    "hint": "",
                    "action": None,
                })

            if fw_info.get("has_block_rules"):
                bnames = ", ".join(b.get("display_name") or b.get("name") or "" for b in (fw_info.get("block_rules") or []))
                checks.append({
                    "id": "firewall",
                    "status": "fail",
                    "title": "Найдены блокирующие входящие правила брандмауэра для RealtyWatch",
                    "detail": f"Блокирующие правила ({bnames}) имеют приоритет над разрешающими.",
                    "hint": "Отметьте удаление блокирующих правил и нажмите «Разрешить в брандмауэре».",
                    "action": "allow_firewall_unblock",
                })
            elif not fw_info.get("ok"):
                missing = []
                if not fw_info.get("tcp_allowed"):
                    missing.append(f"TCP {lan_port}")
                if not fw_info.get("udp_allowed"):
                    missing.append(f"UDP {disc_port}")
                checks.append({
                    "id": "firewall",
                    "status": "fail",
                    "title": f"В брандмауэре нет входящих разрешений ({', '.join(missing)})",
                    "detail": f"Программа: {fw_info.get('program')}",
                    "hint": "Нажмите «Разрешить в брандмауэре (права администратора)», чтобы открыть входящие подключения в профилях Частная и Публичная.",
                    "action": "allow_firewall",
                })
            else:
                checks.append({
                    "id": "firewall",
                    "status": "ok",
                    "title": f"Правила брандмауэра Windows разрешают TCP {lan_port} и UDP {disc_port}",
                    "detail": f"Проверена программа: {Path(fw_info.get('program') or 'pythonw.exe').name}",
                    "hint": "",
                    "action": None,
                })

        # 4. Перехват локальной подсети активным VPN
        extra_t = [clean_peer] if clean_peer else []
        vpn_info = (
            detect_vpn_route_hijack(raw_adapters, extra_targets=extra_t)
            if self._bind_host != "127.0.0.1"
            else {"hijacked": False, "vpn_adapter": "", "intercepted_targets": [], "message": ""}
        )
        if vpn_info.get("hijacked"):
            checks.append({
                "id": "vpn_route",
                "status": "fail",
                "title": f"VPN «{vpn_info['vpn_adapter']}» перехватывает локальную сеть ({vpn_info.get('subnet')})",
                "detail": f"Пакеты на {', '.join(vpn_info.get('intercepted_targets') or [])} уходят в туннель вместо «{vpn_info.get('physical_adapter')}».",
                "hint": "Включите в настройках VPN раздельное туннелирование: добавьте в исключения 192.168.0.0/16 (и 10.0.0.0/8, 172.16.0.0/12) — либо отключите VPN на время обмена.",
                "action": None,
            })
        else:
            phys_lbl = ifaces[0]["adapter"] if ifaces else "локальный интерфейс"
            checks.append({
                "id": "vpn_route",
                "status": "ok",
                "title": f"Маршрутизация в локальную сеть идёт напрямую через «{phys_lbl}»",
                "detail": "Перехвата локальных маршрутов со стороны VPN не обнаружено.",
                "hint": "",
                "action": None,
            })

        # 5. Доступность шлюза по умолчанию (роутера)
        gw_ip = ifaces[0].get("gateway") if ifaces else ""
        if self._bind_host == "127.0.0.1":
            checks.append({
                "id": "gateway",
                "status": "ok",
                "title": "Локальный стенд (127.0.0.1) доступен",
                "detail": "Режим loopback",
                "hint": "",
                "action": None,
            })
        elif not gw_ip:
            checks.append({
                "id": "gateway",
                "status": "warn",
                "title": "У сетевого адаптера не указан основной шлюз",
                "detail": "Прямое соединение без роутера или сеть без выхода в интернет.",
                "hint": "Если оба ПК подключены к одному роутеру — проверьте получение адреса по DHCP.",
                "action": None,
            })
        else:
            gw_ok = _probe_gateway_reachable(gw_ip)
            if gw_ok:
                checks.append({
                    "id": "gateway",
                    "status": "ok",
                    "title": f"Роутер (шлюз {gw_ip}) отвечает",
                    "detail": "Связь с локальной сетью подтверждена.",
                    "hint": "",
                    "action": None,
                })
            else:
                checks.append({
                    "id": "gateway",
                    "status": "fail",
                    "title": f"Роутер (шлюз {gw_ip}) не отвечает",
                    "detail": "Пакеты в локальную сеть блокируются на уровне системы или Wi-Fi.",
                    "hint": "Частая причина — активный VPN Kill-Switch (например, в AmneziaVPN / WireGuard) или изоляция клиентов гостевой Wi-Fi сети на роутере.",
                    "action": None,
                })

        # 6. Активный UDP-зонд RW_PROBE_V1 с основного порта disc_port (8767)
        if cfg["enabled"]:
            con = db.connect()
            try:
                my_ident = self._identity(con)
            finally:
                con.close()
            my_ips = {x["ip"] for x in ifaces} | {"127.0.0.1"}
            rx_sock = self._wait_udp_ready(0.5) if cfg.get("auto_discovery") else None
            diag_id = f"diag-{uuid.uuid4().hex[:8]}"
            diag_name = f"diag@{my_ident['device_name']}"
            probe_b = json.dumps({
                "magic": _PROBE_MAGIC,
                "device_id": diag_id,
                "device_name": diag_name,
                "port": lan_port,
                "disc_port": disc_port,
                "platform": "diag",
                "app_version": current_lan_app_version(),
                "probe_kind": "diag",
            }, ensure_ascii=False).encode("utf-8")

            peer_replies: dict[str, dict] = {}
            self_replied = False
            in_unicast_sweep = False
            wake_evt = threading.Event()

            def _on_diag_pkt(pkt_p: dict, addr_ip: str, addr_port: int):
                nonlocal self_replied, my_ident
                if pkt_p.get("magic") != _DISCOVERY_MAGIC:
                    return
                did = str(pkt_p.get("device_id") or "").strip()
                if not did:
                    return
                dport = int(pkt_p.get("port") or 8766)
                if self._is_self_endpoint(addr_ip, dport, lan_port):
                    self_replied = True
                    return
                if did == my_ident["device_id"]:
                    my_ident = self._heal_collided_device_id(addr_ip, str(pkt_p.get("device_name") or ""))
                if did.startswith("diag-") or pkt_p.get("platform") == "diag":
                    return
                dname = str(pkt_p.get("device_name") or addr_ip)
                dver = str(pkt_p.get("app_version") or "").strip()
                rec = peer_replies.get(did)
                if rec is None:
                    rec = {
                        "device_id": did,
                        "name": dname,
                        "ip": addr_ip,
                        "port": dport,
                        "app_version": dver,
                        "src_ports": set(),
                        "unicast_only": bool(in_unicast_sweep),
                    }
                    peer_replies[did] = rec
                else:
                    if dver and not rec["app_version"]:
                        rec["app_version"] = dver
                if addr_port:
                    rec["src_ports"].add(int(addr_port))
                wake_evt.set()

            with self._lock:
                self._diag_listeners.append(_on_diag_pkt)

            temp_socks: list[tuple[str, list[str], socket.socket]] = []
            try:
                now_p = time.monotonic()
                with self._lock:
                    self._last_bcast_probe_mono = now_p
                    if clean_peer:
                        self._last_unicast_probe_by_ip[clean_peer] = now_p

                dst_ports = {disc_port}
                if self._bind_host != "127.0.0.1":
                    dst_ports.add(8767)
                targets = {(d, p) for d in self._broadcast_targets() for p in dst_ports}
                if clean_peer and _is_lan_ip(clean_peer):
                    targets.add((clean_peer, int(eff_peer_port) + 1))
                    targets.add((clean_peer, disc_port))

                if rx_sock is not None:
                    for dst_pair in sorted(targets):
                        try:
                            rx_sock.sendto(probe_b, dst_pair)
                        except OSError:
                            pass
                    self._send_limited_bcast_per_adapter(lan_port, disc_port, probe_b, dst_ports)
                else:
                    temp_socks = self._open_bound_bcast_sockets(bind_port=disc_port)
                    for idx_s, (_lbl, dst_ips, s) in enumerate(temp_socks):
                        t_set = {(d, p) for d in dst_ips for p in dst_ports}
                        if idx_s == 0 and clean_peer and _is_lan_ip(clean_peer):
                            t_set.add((clean_peer, int(eff_peer_port) + 1))
                            t_set.add((clean_peer, disc_port))
                        for dst_pair in sorted(t_set):
                            try:
                                s.sendto(probe_b, dst_pair)
                            except OSError:
                                pass

                if probe_wait_sec is not None:
                    eff_wait = max(0.15, min(float(probe_wait_sec), 10.0))
                else:
                    eff_wait = 0.45 if self._bind_host == "127.0.0.1" else 4.2

                was_running_diag = self.is_listening()

                def _pump_wait(duration: float):
                    end_t = time.monotonic() + duration
                    settle_t: float | None = None
                    while time.monotonic() < end_t and not (was_running_diag and self._stop_evt.is_set()):
                        now_w = time.monotonic()
                        if settle_t is not None and now_w >= settle_t:
                            break
                        if temp_socks:
                            socks_list = [s for _l, _d, s in temp_socks]
                            try:
                                rdy, _, _ = select.select(socks_list, [], [], 0.12)
                            except (OSError, ValueError):
                                break
                            for rs in rdy:
                                try:
                                    raw_p, addr_p = rs.recvfrom(4096)
                                    self._handle_discovery_packet(raw_p, addr_p[0], addr_p[1], my_lan_port=lan_port, sync_verify=True, reply_sock=rs)
                                except OSError:
                                    pass
                        else:
                            wake_evt.wait(min(0.15, max(0.01, end_t - now_w)))
                            wake_evt.clear()
                        if clean_peer and any(r["ip"] == clean_peer for r in peer_replies.values()) and settle_t is None:
                            settle_t = min(end_t, time.monotonic() + 0.35)
                        elif in_unicast_sweep and peer_replies and settle_t is None:
                            settle_t = min(end_t, time.monotonic() + 0.35)

                _pump_wait(eff_wait)

                # Если на broadcast никто не ответил и конкретный IP не задан — делаем поадресный unicast-обход подсети
                if not peer_replies and not clean_peer and self._bind_host != "127.0.0.1" and ifaces:
                    in_unicast_sweep = True
                    tx_for_sweep = rx_sock if rx_sock is not None else (temp_socks[0][2] if temp_socks else None)
                    sent_u = self._unicast_subnet_sweep(
                        lan_port,
                        tx_sock=tx_for_sweep,
                        interfaces=ifaces,
                        probe_device_id=diag_id,
                        probe_device_name=diag_name,
                        probe_platform="diag",
                    )
                    if sent_u > 0:
                        _pump_wait(min(eff_wait, 3.5))
            finally:
                with self._lock:
                    if _on_diag_pkt in self._diag_listeners:
                        self._diag_listeners.remove(_on_diag_pkt)
                for _l, _d, s in temp_socks:
                    try:
                        s.close()
                    except OSError:
                        pass

            if peer_replies:
                desc_parts = []
                for pr in peer_replies.values():
                    vtag = f"v{pr['app_version']}" if pr["app_version"] else "версия <= 1.8"
                    desc_parts.append(f"{pr['name']} ({pr['ip']}, {vtag})")
                checks.append({
                    "id": "udp_probe",
                    "status": "ok",
                    "title": f"На UDP-зонд (порт {disc_port}) ответили устройства: {len(peer_replies)}",
                    "detail": ", ".join(desc_parts),
                    "hint": "",
                    "action": None,
                })

                now_diag = time.monotonic()
                with self._lock:
                    disc_snap = {k: dict(v) for k, v in self._discovered.items()}
                    udp_uptime = (now_diag - self._udp_started_mono) if self._udp_started_mono > 0 else 0.0

                for pr in peer_replies.values():
                    did = pr["device_id"]
                    exp_disc = int(pr["port"]) + 1
                    disc_entry = disc_snap.get(did) or {}
                    eff_ver = pr["app_version"] or str(disc_entry.get("app_version") or "")
                    seen_fixed = bool(disc_entry.get("seen_fixed_port")) or (exp_disc in pr["src_ports"])
                    is_legacy = bool(not eff_ver and not seen_fixed and pr["src_ports"])
                    if is_legacy:
                        sample_sp = sorted(pr["src_ports"])[0]
                        checks.append({
                            "id": "peer_legacy_protocol",
                            "status": "warn",
                            "title": f"Устройство «{pr['name']}» ({pr['ip']}) отвечает по старому протоколу (версия 1.7 или старше) — обновите его до 1.8.1",
                            "detail": f"Ответ на порт {disc_port} пришёл со случайного порта UDP {sample_sp} вместо {exp_disc}.",
                            "hint": "Обновите RealtyWatch на втором компьютере до версии 1.8.1+, чтобы обнаружение работало мгновенно без задержек.",
                            "action": None,
                        })

                    seen_bcast = bool(disc_entry.get("seen_broadcast"))
                    if not seen_bcast and (pr["unicast_only"] or udp_uptime >= (DISCOVERY_INTERVAL_SEC + 1.5)):
                        checks.append({
                            "id": "peer_asymmetric_bcast",
                            "status": "warn",
                            "title": f"Устройство «{pr['name']}» ({pr['ip']}) найдено только по запросу — проверьте на нём «Проверить сеть» (VPN/брандмауэр)",
                            "detail": "Наши зонды доходят до второго компьютера, но его собственные широковещательные анонсы сюда не поступают.",
                            "hint": "Откройте окно «Локальная сеть → Проверить сеть» на втором компьютере: вероятно, там включён VPN без раздельного туннелирования или активен виртуальный адаптер.",
                            "action": None,
                        })
            else:
                self_txt = f"Свой локальный сервер (UDP {disc_port}) на зонд ответил." if self_replied else f"Даже свой локальный UDP-сервер ({disc_port}) не ответил на зонд."
                checks.append({
                    "id": "udp_probe",
                    "status": "warn",
                    "title": "Другие компьютеры с RealtyWatch на UDP-зонд не ответили (0 ответов)",
                    "detail": self_txt,
                    "hint": "Убедитесь, что RealtyWatch открыт и LAN включена на ОБОИХ ПК, оба компьютера в одной сети, а на втором ПК также разрешён брандмауэр и не перехватывает VPN.",
                    "action": None,
                })
        else:
            checks.append({
                "id": "udp_probe",
                "status": "fail",
                "title": "UDP-зонд пропущен, так как LAN выключена",
                "detail": "",
                "hint": "Сначала включите локальную сеть.",
                "action": "enable_lan",
            })

        # 7. Прямая TCP-проверка до введённого IP второго компьютера (если указан)
        tcp_reachable_peer = None
        paired_ips = {p["ip"] for p in self.list_peers()}
        if clean_peer:
            if not _is_lan_ip(clean_peer):
                checks.append({
                    "id": "peer_tcp",
                    "status": "fail",
                    "title": f"Адрес {clean_peer} не является адресом локальной сети",
                    "detail": "Поддерживаются только частные IPv4-адреса (192.168.x.x, 10.x.x.x, 172.16-31.x.x).",
                    "hint": "Скопируйте физический адрес Wi-Fi/Ethernet со второго компьютера.",
                    "action": None,
                })
            else:
                try:
                    t0 = time.monotonic()
                    with socket.create_connection((clean_peer, eff_peer_port), timeout=2.5) as tsock:
                        tsock.settimeout(2.0)
                        req_b = f"GET /sync/status HTTP/1.1\r\nHost: {clean_peer}\r\nConnection: close\r\n\r\n".encode("ascii", errors="ignore")
                        tsock.sendall(req_b)
                        first_line = tsock.recv(256).decode("utf-8", errors="replace").splitlines()[0] if True else ""
                    rtt = round((time.monotonic() - t0) * 1000)
                    is_self_ip = (clean_peer in ({x["ip"] for x in ifaces} | {"127.0.0.1"}) and eff_peer_port == lan_port)
                    can_pair_by_ip = (not is_self_ip) and (clean_peer not in paired_ips)
                    if can_pair_by_ip:
                        tcp_reachable_peer = {"ip": clean_peer, "port": eff_peer_port}
                    checks.append({
                        "id": "peer_tcp",
                        "status": "ok",
                        "title": f"Прямое TCP-соединение с {clean_peer}:{eff_peer_port} установлено ({rtt} мс)",
                        "detail": f"Ответ сервера партнёра: {first_line or 'OK'}",
                        "hint": "Сопряжение по TCP полностью доступно — введите 6-значный PIN со второго компьютера." if can_pair_by_ip else "",
                        "action": "pair_by_ip" if can_pair_by_ip else None,
                    })
                except ConnectionRefusedError:
                    checks.append({
                        "id": "peer_tcp",
                        "status": "fail",
                        "title": f"Компьютер {clean_peer} отклонил подключение на порт {eff_peer_port}",
                        "detail": "Узел в сети достижим, но порт закрыт.",
                        "hint": "На втором компьютере выключена LAN в настройках RealtyWatch, закрыта программа или указан другой порт.",
                        "action": None,
                    })
                except OSError as e:
                    checks.append({
                        "id": "peer_tcp",
                        "status": "fail",
                        "title": f"Нет TCP-соединения с {clean_peer}:{eff_peer_port}",
                        "detail": f"Ошибка сети: {e}",
                        "hint": "На втором компьютере межсетевой экран блокирует входящие подключения, мешает VPN, не разрешён доступ к локальной сети или введён неверный IP (например, адрес VPN вместо Wi-Fi).",
                        "action": None,
                    })

        has_fail = any(c["status"] == "fail" for c in checks)
        ok_all = not has_fail
        _lan_log(
            f"Диагностика сети завершена (peer={clean_peer or '—'}): ok={ok_all}, "
            + "; ".join(f"{c['id']}={c['status']}" for c in checks)
        )
        return {
            "ok": ok_all,
            "checked_at": _now_ts(),
            "peer_ip": clean_peer,
            "peer_port": eff_peer_port if clean_peer else None,
            "tcp_reachable_peer": tcp_reachable_peer,
            "checks": checks,
            "local_interfaces": ifaces,
            "vpn_hijack": vpn_info,
            "firewall": fw_info,
        }

    def status(self, refresh: bool = False):
        cfg = self._lan_cfg()
        con = db.connect()
        try:
            ident = self._identity(con)
            local_f = exchange._db_freshness(con)
            local_m = _marks_counts(con)
            ocnt = con.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE payload_path IS NOT NULL AND payload_path != ''"
            ).fetchone()[0]
        finally:
            con.close()
        if refresh and cfg["enabled"]:
            try:
                self.poll_peers_once()
            except Exception:
                pass
        peers = self.list_peers()
        inbox = self.list_inbox()
        raw_adapters = _raw_system_adapters() if self._bind_host != "127.0.0.1" else []
        ifaces = local_ipv4s(raw_adapters) if self._bind_host != "127.0.0.1" else []
        vpn_hijack = (
            detect_vpn_route_hijack(raw_adapters)
            if self._bind_host != "127.0.0.1"
            else {"hijacked": False, "vpn_adapter": "", "intercepted_targets": [], "message": ""}
        )
        fw_info = self.get_firewall_status(cfg["port"], sync_if_empty=(refresh and self._bind_host != "127.0.0.1"))
        with self._lock:
            self._outbox_count = ocnt
            newer_peer = dict(self._newer_db_peer) if self._newer_db_peer else None
            scanning_sub = bool(self._scanning_subnet)
        return {
            "enabled": cfg["enabled"],
            "listening": self.is_listening(),
            "udp_listening": self.is_udp_listening(),
            "scanning_subnet": scanning_sub,
            "app_version": current_lan_app_version(),
            "port": cfg["port"],
            "actual_port": self._running_port,
            "auto_discovery": cfg["auto_discovery"],
            "device_id": ident["device_id"],
            "device_name": ident["device_name"],
            "local_interfaces": ifaces,
            "local_ips": [x["ip"] for x in ifaces],
            "vpn_hijack": vpn_hijack,
            "firewall": fw_info,
            "pin": self.pin_status(),
            "discovered": self.list_discovered(),
            "peers": peers,
            "online_peers": sum(1 for p in peers if p["online"]),
            "inbox": inbox,
            "inbox_count": len(inbox),
            "outbox_count": ocnt,
            "newer_db_peer": newer_peer,
            "local_freshness": local_f,
            "local_marks": local_m,
            "history_unread": self._ensure_history_unread(),
            "busy": runner.manager.any_running(),
        }

    # ---------- входящие запросы (очередь + принятие / отклонение) ----------

    def list_inbox(self):
        con = db.connect()
        try:
            local_f = exchange._db_freshness(con)
            local_listings = con.execute("SELECT COUNT(*) FROM listings WHERE COALESCE(imported_only,0)=0").fetchone()[0]
            local_likes = con.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
            local_m = _marks_counts(con)
        finally:
            con.close()
        busy = runner.manager.any_running()
        with self._lock:
            self._ensure_inbox_loaded()
            items = sorted(self._inbox.values(), key=lambda x: x.get("received_at") or "", reverse=True)
        out = []
        for it in items:
            row = dict(it)
            if row.get("kind") == "db":
                cmp_res = _compare_freshness(row.get("sender_freshness") or {}, local_f)
                row["local_freshness"] = local_f
                row["local_counts"] = {"listings": local_listings, "likes": local_likes}
                row["freshness_cmp"] = cmp_res
                row["busy"] = busy
            elif row.get("kind") == "marks":
                row["local_marks"] = local_m
                row["busy"] = busy
            out.append(row)
        return out

    def reject_inbox(self, req_id: str):
        with self._lock:
            self._ensure_inbox_loaded()
            it = self._inbox.pop(req_id, None)
            if it is None:
                raise LanNotFoundError("входящий запрос не найден")
            self._save_inbox_manifest()
        self._cleanup_inbox_files(it)
        try:
            con = db.connect()
            try:
                con.execute("UPDATE sync_history SET seen=1, detail=CASE WHEN COALESCE(detail,'')='' THEN 'Отклонено' ELSE detail || ' · отклонено' END WHERE req_id=?", (req_id,))
                con.commit()
                unread = int(con.execute("SELECT COUNT(*) FROM sync_history WHERE COALESCE(seen, 0)=0").fetchone()[0] or 0)
            finally:
                con.close()
            with self._lock:
                self._history_db_key = str(db.DB_FILE)
                self._history_unread = unread
                self._history_rev += 1
        except Exception:
            pass
        runner.manager.log("lan", f"отклонён входящий запрос от {it.get('from_device_name') or 'устройства'}")
        return {"ok": True, "req_id": req_id}

    def _cleanup_inbox_files(self, item: dict):
        zp = item.get("zip_path")
        if zp:
            try:
                Path(zp).unlink(missing_ok=True)
            except OSError:
                pass
        sdir = item.get("sess_dir")
        if sdir:
            shutil.rmtree(sdir, ignore_errors=True)

    @staticmethod
    def _extract_marks_history_meta(payload: dict, eff_items: list[str]):
        cats_keys: dict[str, list[list[str]]] = {}
        if "likes" in eff_items:
            cats_keys["likes"] = [
                [str(r["source"]), str(r["source_id"])]
                for r in (payload.get("likes") or [])
                if isinstance(r, dict) and r.get("source") and r.get("source_id")
            ]
        if "dislikes" in eff_items:
            cats_keys["dislikes"] = [
                [str(r["source"]), str(r["source_id"])]
                for r in (payload.get("dislikes") or [])
                if isinstance(r, dict) and r.get("source") and r.get("source_id")
            ]
        if "notes" in eff_items:
            n_keys = []
            for r in (payload.get("notes") or []) + (payload.get("imported_notes") or []):
                if isinstance(r, dict) and r.get("source") and r.get("source_id") and str(r.get("text") or "").strip():
                    n_keys.append([str(r["source"]), str(r["source_id"])])
            cats_keys["notes"] = n_keys
        if "merges" in eff_items:
            cats_keys["merges"] = [
                [str(r["source"]), str(r["source_id"])]
                for r in (payload.get("manual_merges") or [])
                if isinstance(r, dict) and r.get("source") and r.get("source_id")
            ]
        all_keys: list[list[str]] = []
        seen = set()
        for ck in MARK_CATEGORIES:
            for pair in cats_keys.get(ck) or []:
                tp = (pair[0], pair[1])
                if tp not in seen:
                    seen.add(tp)
                    all_keys.append(pair)
        fb_rows = {
            (str(r["source"]), str(r["source_id"])): r
            for r in (payload.get("ref_listings") or [])
            if isinstance(r, dict) and r.get("source") and r.get("source_id")
        }
        notes_map = {
            (str(r["source"]), str(r["source_id"])): str(r.get("text") or "").strip()
            for r in (payload.get("notes") or [])
            if isinstance(r, dict) and r.get("source") and r.get("source_id") and str(r.get("text") or "").strip()
        }
        return cats_keys, all_keys, fb_rows, notes_map

    @staticmethod
    def _extract_bundle_history_meta(zip_path: Path):
        all_keys: list[list[str]] = []
        fb_rows: dict[tuple[str, str], dict] = {}
        notes_map: dict[tuple[str, str], str] = {}
        seen = set()
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                _, cards = exchange._load_bundle_zip(zf)
            for c in cards or []:
                cnote = str(c.get("note") or "").strip()
                for li in c.get("listings") or []:
                    if not isinstance(li, dict):
                        continue
                    s, sid = str(li.get("source") or ""), str(li.get("source_id") or "")
                    if not s or not sid:
                        continue
                    if (s, sid) not in seen:
                        seen.add((s, sid))
                        all_keys.append([s, sid])
                    fb_rows[(s, sid)] = li
                    if cnote:
                        notes_map[(s, sid)] = cnote
        except Exception:
            pass
        return all_keys, fb_rows, notes_map

    def accept_inbox(self, req_id: str, opts: dict | None = None):
        opts = opts or {}
        with self._lock:
            self._ensure_inbox_loaded()
            it = self._inbox.get(req_id)
            if it is None:
                raise LanNotFoundError("входящий запрос не найден")
            it = dict(it)

        kind = it.get("kind")
        origin = {
            "device_id": it.get("from_device_id") or "",
            "device_name": it.get("from_device_name") or "Устройство LAN",
        }
        if kind == "cards":
            b_keys, _, _ = self._extract_bundle_history_meta(Path(it["zip_path"]))
            likes_mode = str(opts.get("likes_mode") or "preserve")
            apply_merges = bool(opts.get("apply_manual_merges", True))
            res = exchange.import_cards_zip(
                it["zip_path"],
                likes_mode=likes_mode,
                channel="lan",
                origin_override=origin,
                apply_manual_merges=apply_merges,
            )
            res["kind"] = "cards"
            self._record_history(
                kind="cards_applied",
                peer_id=origin["device_id"],
                peer_name=origin["device_name"],
                req_id=req_id,
                batch_id=res.get("batch_id"),
                title="Принята подборка карточек",
                detail=f"Новых: {res.get('new_cards', 0)}, совпало: {res.get('matched_cards', 0)}",
                keys=b_keys,
                mark_seen=True,
            )
        elif kind == "favorites":
            b_keys, _, _ = self._extract_bundle_history_meta(Path(it["zip_path"]))
            fav_mode = str(opts.get("favorites_mode") or "merge")
            if fav_mode not in ("merge", "replace"):
                raise ValueError(f"неизвестный режим избранного: {fav_mode}")
            res = self._accept_favorites(it, origin, fav_mode)
            self._record_history(
                kind="favorites_applied",
                peer_id=origin["device_id"],
                peer_name=origin["device_name"],
                req_id=req_id,
                batch_id=res.get("batch_id"),
                title="Принято Избранное (♥)",
                detail=f"Режим: {'замена' if fav_mode == 'replace' else 'дополнение'} · карточек: {res.get('liked', 0)}",
                keys=b_keys,
                cats={"likes": b_keys} if b_keys else {},
                mark_seen=True,
            )
        elif kind == "marks":
            marks_mode = str(opts.get("mode") or "merge")
            sel_items = opts.get("items") if opts.get("items") is not None else it.get("items")
            payload = _unpack_marks_zip(Path(it["zip_path"]))
            payload["from_device_id"] = origin["device_id"] or payload.get("from_device_id") or ""
            payload["from_device_name"] = origin["device_name"] or payload.get("from_device_name") or "Устройство LAN"
            res = self._apply_marks_payload(payload, items=sel_items, mode=marks_mode)
            res["kind"] = "marks"
            cats_k, all_k, fb_r, n_map = self._extract_marks_history_meta(payload, res["items"])
            con = db.connect()
            try:
                prev_items = self._build_preview_items(con, all_k, fallback_rows=fb_r, notes_map=n_map)
            finally:
                con.close()
            lbl_map = {"likes": "♥ лайки", "dislikes": "👎 дизлайки", "notes": "✎ заметки", "merges": "🔗 дубли"}
            parts_str = ", ".join(f"{lbl_map[k]} ({res['incoming_counts'].get(k, 0)})" for k in res["items"] if k in lbl_map)
            self._record_history(
                kind="marks_applied",
                peer_id=origin["device_id"],
                peer_name=origin["device_name"],
                req_id=req_id,
                title="Приняты отметки и дубли",
                detail=f"{'Перезаписано' if marks_mode == 'replace' else 'Добавлено к вашим'}: {parts_str}",
                keys=all_k,
                cats=cats_k,
                preview=prev_items,
                mark_seen=True,
            )
        elif kind == "db":
            if runner.manager.any_running():
                raise LanBusyError("Идёт сбор объявлений — дождитесь окончания, затем примите базу")
            res = self._accept_full_db(it, copy_filters_zone=bool(opts.get("copy_filters_zone", False)))
            self._record_history(
                kind="db_applied",
                peer_id=origin["device_id"],
                peer_name=origin["device_name"],
                req_id=req_id,
                title="Принята полная база по LAN",
                detail=f"Объявлений: {res.get('listings', 0)}, объектов: {res.get('objects', 0)}, новых фото: {res.get('images_copied', 0)}",
                mark_seen=True,
            )
        else:
            raise ValueError(f"неподдерживаемый тип входящего запроса: {kind}")

        with self._lock:
            self._ensure_inbox_loaded()
            self._inbox.pop(req_id, None)
            self._save_inbox_manifest()
        self._cleanup_inbox_files(it)
        runner.manager.log("lan", f"принято от «{origin['device_name']}» ({kind})")
        return res

    def _bundle_live_keys(self, zip_path: Path, origin: dict, con) -> list[tuple[str, str]]:
        """Ключи актуальных (не-stale) объявлений из архива .rwcards.zip."""
        z, p = exchange._open_zip(zip_path)
        with z:
            an = exchange._analyze_bundle(z, con, origin_override=origin, source_file=p.name)
        out = []
        seen = set()
        for ac in an["cards"]:
            if ac["status"] == "stale":
                continue
            for ic in ac["items"]:
                if ic["is_stale"] and ic["local"] is None:
                    continue
                k = ic["key"]
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        return out

    def _expand_object_keys(self, con, keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Возвращает существующие в listings ключи и всех их собратьев по object_id."""
        if not keys:
            return []
        expanded = []
        seen = set()
        for src, sid in keys:
            row = con.execute(
                "SELECT object_id FROM listings WHERE source=? AND source_id=?", (src, sid)
            ).fetchone()
            if row is None:
                continue
            oid = row[0]
            if oid:
                siblings = [
                    (r[0], str(r[1]))
                    for r in con.execute("SELECT source, source_id FROM listings WHERE object_id=?", (oid,))
                ]
            else:
                siblings = [(src, sid)]
            for sk in siblings:
                if sk not in seen:
                    seen.add(sk)
                    expanded.append(sk)
        return expanded

    def _accept_favorites(self, item: dict, origin: dict, fav_mode: str):
        zpath = Path(item["zip_path"])
        backup_path = None
        if fav_mode == "replace":
            backup_path = transfer.backup("перед_заменой_избранного", with_images=False)
            res = exchange.import_cards_zip(
                zpath, likes_mode="none", channel="lan", origin_override=origin, apply_manual_merges=True,
            )
            con = db.connect()
            try:
                now_s = _now_ts()
                live_keys = self._bundle_live_keys(zpath, origin, con)
                target_keys = self._expand_object_keys(con, live_keys)
                con.execute("DELETE FROM likes")
                for src, sid in target_keys:
                    con.execute("INSERT OR IGNORE INTO likes(source, source_id, ts) VALUES(?,?,?)", (src, sid, now_s))
                    con.execute("DELETE FROM dislikes WHERE source=? AND source_id=?", (src, sid))
                con.commit()
                res["liked"] = len({
                    con.execute("SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source=? AND source_id=?", k).fetchone()[0]
                    for k in target_keys
                })
                if target_keys:
                    res["skipped_empty"] = False
            finally:
                con.close()
            res["backup"] = str(backup_path)
        else:
            res = exchange.import_cards_zip(
                zpath, likes_mode="all", channel="lan", origin_override=origin, apply_manual_merges=True,
            )
            # Если все карточки уже были в базе (например, imported_only без лайка), проставляем лайк на не-stale
            con = db.connect()
            try:
                now_s = _now_ts()
                live_keys = self._bundle_live_keys(zpath, origin, con)
                target_keys = self._expand_object_keys(con, live_keys)
                added = 0
                for src, sid in target_keys:
                    cur = con.execute("INSERT OR IGNORE INTO likes(source, source_id, ts) VALUES(?,?,?)", (src, sid, now_s))
                    con.execute("DELETE FROM dislikes WHERE source=? AND source_id=?", (src, sid))
                    if cur.rowcount:
                        added += 1
                con.commit()
                if added > 0:
                    res["skipped_empty"] = False
                    res["liked"] = int(res.get("liked") or 0) + added
            finally:
                con.close()
        res["kind"] = "favorites"
        res["favorites_mode"] = fav_mode
        return res

    def _accept_full_db(self, item: dict, copy_filters_zone: bool = False):
        src_db = Path(item["db_path"])
        if not src_db.is_file():
            raise ValueError("файл входящей базы не найден")
        _validate_sqlite_snapshot(src_db)
        backup_path = transfer.backup("перед_синхронизацией_базы", with_images=False)

        with tempfile.TemporaryDirectory(prefix="rw_dbsync_") as td:
            work_db = Path(td) / "incoming.db"
            shutil.copy2(src_db, work_db)
            icon = sqlite3.connect(work_db)
            icon.row_factory = sqlite3.Row
            db.migrate(icon)
            icon.close()
            _validate_sqlite_snapshot(work_db)

            con = db.connect()
            try:
                ident = self._identity(con)
                my_device_id = ident["device_id"]

                # Ключи объявлений, на которые ссылается личный слой получателя
                ref_keys = set()
                for tbl in ("likes", "dislikes", "notes", "imported_notes"):
                    for r in con.execute(f"SELECT source, source_id FROM {tbl}"):
                        ref_keys.add((r[0], str(r[1])))
                for r in con.execute("SELECT source, source_id FROM manual_merges"):
                    ref_keys.add((r[0], str(r[1])))

                imp_listings = []
                imp_history = []
                attached = False
                try:
                    con.execute("ATTACH DATABASE ? AS imp", (str(work_db),))
                    attached = True
                    if ref_keys:
                        for r in con.execute("SELECT * FROM listings WHERE COALESCE(imported_only, 0)=1"):
                            k = (r["source"], str(r["source_id"]))
                            if k not in ref_keys:
                                continue
                            in_inc = con.execute(
                                "SELECT 1 FROM imp.listings WHERE source=? AND source_id=?", k
                            ).fetchone()
                            if in_inc is None:
                                imp_listings.append(dict(r))
                                for hr in con.execute(
                                    "SELECT source, source_id, ts, price FROM price_history WHERE source=? AND source_id=?",
                                    k,
                                ):
                                    imp_history.append(dict(hr))

                    # Заменяем только рыночные таблицы; личный слой получателя не трогаем
                    con.execute("BEGIN IMMEDIATE")
                    for tbl in ("listings", "price_history", "events", "runs", "snapshots"):
                        con.execute(f"DELETE FROM {tbl}")
                        _copy_common_table(con, "imp", tbl)

                    # Возвращаем локальные imported_only=1 объявления, на которые ссылаются лайки/заметки получателя
                    if imp_listings:
                        lcols = [r[1] for r in con.execute("PRAGMA main.table_info(listings)")]
                        placeholders = ",".join("?" * len(lcols))
                        col_sql = ",".join(lcols)
                        for row in imp_listings:
                            vals = [row.get(c) for c in lcols]
                            con.execute(f"INSERT OR IGNORE INTO listings({col_sql}) VALUES({placeholders})", vals)
                        for hr in imp_history:
                            con.execute(
                                "INSERT OR IGNORE INTO price_history(source, source_id, ts, price) VALUES(?,?,?,?)",
                                (hr["source"], hr["source_id"], hr["ts"], hr["price"]),
                            )

                    # Объединяем ручные склейки обеих сторон без потери ничьих связок
                    transfer._merge_manual_merges(con)

                    # Гарантируем сохранение собственного device_id
                    con.execute("INSERT OR REPLACE INTO meta VALUES('device_id', ?)", (my_device_id,))

                    # Обновляем last_db_ts отправителя в sync_peers
                    from_id = item.get("from_device_id")
                    sender_max = _max_freshness_ts(item.get("sender_freshness") or {}) or _now_ts()
                    if from_id:
                        con.execute(
                            "UPDATE sync_peers SET last_db_ts=?, last_seen=? WHERE device_id=?",
                            (sender_max, _now_ts(), from_id),
                        )
                    con.commit()
                except Exception as e:
                    if con.in_transaction:
                        con.rollback()
                    raise ValueError(f"не удалось применить входящую базу: {e}") from e
                finally:
                    if attached:
                        try:
                            con.execute("DETACH DATABASE imp")
                        except sqlite3.Error:
                            pass

                # Переносим полученные дельта-фото, не перезаписывая существующие файлы
                img_copied = 0
                sess_img_dir = Path(item.get("sess_dir") or "") / "images"
                if sess_img_dir.is_dir():
                    for src in _SOURCES:
                        s_sub = sess_img_dir / src
                        if not s_sub.is_dir():
                            continue
                        for f in s_sub.iterdir():
                            if not f.is_file():
                                continue
                            parsed = _safe_img_rel(f"{src}/{f.name}")
                            if not parsed:
                                continue
                            _, stem, ext, fname = parsed
                            if images.local_path(src, stem) is not None:
                                continue
                            dst_dir = images.IMAGES / src
                            dst_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(f, dst_dir / fname)
                            img_copied += 1

                dedupe.clean_manual_merges(con)
                con.commit()
                objects = dedupe.rebuild(con)
                snapshots = history.rebuild_snapshots(con)
                backfilled = history.backfill(con)
                listings_cnt = con.execute("SELECT COUNT(*) FROM listings WHERE COALESCE(imported_only,0)=0").fetchone()[0]
                likes_cnt = con.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
                preserved_imp = len(imp_listings)
            finally:
                con.close()

        filters_copied = False
        fz = item.get("filters_zone")
        if copy_filters_zone and isinstance(fz, dict):
            cur_cfg = settings.load()
            changed = False
            if isinstance(fz.get("filters"), dict):
                cur_cfg["filters"] = fz["filters"]
                changed = True
            if isinstance(fz.get("zone"), dict):
                cur_cfg["zone"] = fz["zone"]
                changed = True
            if changed:
                settings.save(cur_cfg)
                filters_copied = True

        with self._lock:
            self._newer_db_peer = None
            for rt in self._peer_runtime.values():
                rt["has_newer_db"] = False
                rt["newer_sources"] = []

        return {
            "ok": True,
            "kind": "db",
            "backup": str(backup_path),
            "listings": listings_cnt,
            "objects": objects,
            "snapshots": snapshots,
            "backfilled": backfilled,
            "likes": likes_cnt,
            "preserved_imported": preserved_imp,
            "images_copied": img_copied,
            "filters_copied": filters_copied,
        }

    # ---------- отправка на сопряжённые устройства ----------

    def _get_peer(self, con, peer_id: str) -> dict:
        r = con.execute("SELECT * FROM sync_peers WHERE device_id=?", (peer_id,)).fetchone()
        if r is None:
            raise LanNotFoundError(f"устройство {peer_id} не найдено среди сопряжённых")
        return dict(r)

    def _auth_headers(self, ident: dict, peer: dict) -> dict:
        return {
            "X-RW-Device": ident["device_id"],
            "X-RW-Port": str(self._lan_cfg()["port"]),
            "Authorization": f"Bearer {peer['peer_token'] or ''}",
        }

    def send_cards(self, peer_ids: list[str], object_ids=None, liked_only: bool = False):
        """Собирает .rwcards.zip с выбранными карточками и отправляет на выбранные LAN-устройства."""
        if not peer_ids:
            raise ValueError("выберите хотя бы одно устройство")
        con = db.connect()
        try:
            ident = self._identity(con)
            peers = [self._get_peer(con, pid) for pid in peer_ids]
        finally:
            con.close()

        with tempfile.TemporaryDirectory(prefix="rw_send_cards_") as td:
            zpath = Path(td) / exchange.default_name("cards")
            summary = exchange.export_cards_zip(zpath, object_ids=object_ids, liked_only=liked_only, kind="cards")
            raw = zpath.read_bytes()

        results = []
        con = db.connect()
        try:
            for p in peers:
                hdrs = self._auth_headers(ident, p)
                hdrs["Content-Type"] = "application/zip"
                hdrs["X-RW-Kind"] = "cards"
                try:
                    st, data = _http_call(p["ip"], p["port"], "POST", "/sync/push/cards", body=raw, headers=hdrs, timeout=60.0)
                    ok = st == 200 and bool(data.get("ok"))
                    err = None if ok else (data.get("detail") or f"HTTP {st}")
                    if ok:
                        _outbox_log(con, p["device_id"], "cards", str(data.get("req_id") or ""))
                except Exception as e:
                    ok, data, err = False, {}, str(e)
                results.append({"device_id": p["device_id"], "name": p["name"], "ok": ok, "error": err, "req_id": data.get("req_id")})
        finally:
            con.close()
        ok_cnt = sum(1 for r in results if r["ok"])
        if ok_cnt:
            runner.manager.log("lan", f"отправлены карточки ({summary.get('cards', 0)} шт.) на {ok_cnt} устройств(а)")
        return {"ok": ok_cnt > 0, "cards": summary.get("cards", 0), "results": results}

    def send_favorites(self, peer_ids: list[str]):
        """Экспортирует все карточки из Избранного и отправляет на выбранные устройства."""
        if not peer_ids:
            raise ValueError("выберите хотя бы одно устройство")
        con = db.connect()
        try:
            likes_cnt = con.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
            if likes_cnt == 0:
                raise ValueError("в Избранном пока нет ни одной карточки")
            ident = self._identity(con)
            peers = [self._get_peer(con, pid) for pid in peer_ids]
        finally:
            con.close()

        with tempfile.TemporaryDirectory(prefix="rw_send_fav_") as td:
            zpath = Path(td) / exchange.default_name("favorites")
            summary = exchange.export_cards_zip(zpath, liked_only=True, kind="favorites")
            raw = zpath.read_bytes()

        results = []
        con = db.connect()
        try:
            for p in peers:
                hdrs = self._auth_headers(ident, p)
                hdrs["Content-Type"] = "application/zip"
                hdrs["X-RW-Kind"] = "favorites"
                try:
                    st, data = _http_call(p["ip"], p["port"], "POST", "/sync/push/favorites", body=raw, headers=hdrs, timeout=60.0)
                    ok = st == 200 and bool(data.get("ok"))
                    err = None if ok else (data.get("detail") or f"HTTP {st}")
                    if ok:
                        _outbox_log(con, p["device_id"], "favorites", str(data.get("req_id") or ""))
                except Exception as e:
                    ok, data, err = False, {}, str(e)
                results.append({"device_id": p["device_id"], "name": p["name"], "ok": ok, "error": err, "req_id": data.get("req_id")})
        finally:
            con.close()
        ok_cnt = sum(1 for r in results if r["ok"])
        if ok_cnt:
            runner.manager.log("lan", f"отправлено Избранное ({summary.get('cards', 0)} карт.) на {ok_cnt} устройств(а)")
        return {"ok": ok_cnt > 0, "cards": summary.get("cards", 0), "results": results}

    def send_db(self, peer_id: str):
        """Отправляет полный снимок базы + дельту недостающих фото выбранному устройству."""
        con = db.connect()
        try:
            ident = self._identity(con)
            peer = self._get_peer(con, peer_id)
            freshness = exchange._db_freshness(con)
            listings_cnt = con.execute("SELECT COUNT(*) FROM listings WHERE COALESCE(imported_only,0)=0").fetchone()[0]
            likes_cnt = con.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
            runs_cnt = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            con.close()

        cfg = settings.load()
        img_manifest = _scan_images_manifest()
        meta = {
            "format": _DB_SYNC_FORMAT,
            "version": _DB_SYNC_VERSION,
            "created_at": _now_ts(),
            "from_device_id": ident["device_id"],
            "from_device_name": ident["device_name"],
            "freshness": freshness,
            "counts": {
                "listings": listings_cnt,
                "likes": likes_cnt,
                "runs": runs_cnt,
                "images": len(img_manifest),
            },
            "filters_zone": {
                "filters": cfg.get("filters") or {},
                "zone": cfg.get("zone") or {},
            },
            "images_manifest": img_manifest,
        }

        with tempfile.TemporaryDirectory(prefix="rw_send_db_") as td:
            td_p = Path(td)
            snap_path = td_p / "realty.db"
            transfer._snapshot(snap_path)
            init_zip = td_p / "init.zip"
            with zipfile.ZipFile(init_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(snap_path, "realty.db")
                zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False))
            init_bytes = init_zip.read_bytes()

        hdrs = self._auth_headers(ident, peer)
        hdrs["Content-Type"] = "application/zip"
        try:
            st, init_resp = _http_call(
                peer["ip"], peer["port"], "POST", "/sync/push/db/init",
                body=init_bytes, headers=hdrs, timeout=180.0,
            )
        except Exception as e:
            raise ValueError(f"ошибка отправки базы на {peer['name']}: {e}")
        if st != 200 or not init_resp.get("ok"):
            raise ValueError(init_resp.get("detail") or f"ошибка приёма базы (HTTP {st})")

        req_id = init_resp["req_id"]
        missing = list(init_resp.get("missing_images") or [])
        sent_images = 0

        if missing:
            with tempfile.TemporaryDirectory(prefix="rw_send_imgs_") as td:
                delta_zip = Path(td) / "delta.zip"
                with zipfile.ZipFile(delta_zip, "w", compression=zipfile.ZIP_STORED) as zf:
                    for rel in missing:
                        parsed = _safe_img_rel(rel)
                        if not parsed:
                            continue
                        src, stem, _, fname = parsed
                        fp = images.IMAGES / src / fname
                        if not fp.is_file():
                            fp = images.local_path(src, stem)
                        if fp is not None and fp.is_file():
                            zf.write(fp, f"images/{src}/{fp.name}")
                            sent_images += 1
                delta_bytes = delta_zip.read_bytes()

            hdrs2 = self._auth_headers(ident, peer)
            hdrs2["Content-Type"] = "application/zip"
            try:
                st2, img_resp = _http_call(
                    peer["ip"], peer["port"], "POST", f"/sync/push/db/images/{req_id}",
                    body=delta_bytes, headers=hdrs2, timeout=300.0,
                )
            except Exception as e:
                raise ValueError(f"ошибка отправки недостающих фото: {e}")
            if st2 != 200 or not img_resp.get("ok"):
                raise ValueError(img_resp.get("detail") or f"ошибка приёма фото (HTTP {st2})")

        con = db.connect()
        try:
            _outbox_log(con, peer["device_id"], "db", str(req_id))
        finally:
            con.close()

        skipped_images = max(0, len(img_manifest) - sent_images)
        runner.manager.log(
            "lan",
            f"отправлена база на «{peer['name']}» ({listings_cnt} объявл., фото: {sent_images} новых, {skipped_images} уже есть)",
        )
        return {
            "ok": True,
            "req_id": req_id,
            "peer": {"device_id": peer["device_id"], "name": peer["name"]},
            "listings": listings_cnt,
            "total_images": len(img_manifest),
            "sent_images": sent_images,
            "skipped_images": skipped_images,
        }

    # ---------- Этап 4: Spotlight, догрузка карточки, pull DB и Live-синхронизация ----------

    def send_spotlight(self, object_id: str, peer_ids: list[str] | None = None):
        """Отправляет сигнал «Подсветить партнёру» онлайн-пирам (одному или всем в сети)."""
        if not self._lan_cfg()["enabled"]:
            raise ValueError("локальная сеть выключена")
        con = db.connect()
        try:
            ident = self._identity(con)
            rows = [dict(r) for r in con.execute(
                "SELECT source, source_id, url, title, address, rooms, area, price "
                "FROM listings WHERE object_id=? OR (object_id IS NULL AND source||':'||source_id=?)",
                (object_id, object_id),
            )]
            if not rows and ":" in str(object_id):
                src0, sid0 = str(object_id).split(":", 1)
                rows = [dict(r) for r in con.execute(
                    "SELECT source, source_id, url, title, address, rooms, area, price "
                    "FROM listings WHERE source=? AND source_id=?",
                    (src0, sid0),
                )]
            if not rows:
                raise LanNotFoundError("карточка не найдена")
            if peer_ids:
                targets = [self._get_peer(con, pid) for pid in peer_ids]
            else:
                all_peers = [dict(r) for r in con.execute("SELECT * FROM sync_peers")]
                with self._lock:
                    online_ids = {k for k, v in self._peer_runtime.items() if v.get("online")}
                targets = [p for p in all_peers if p["device_id"] in online_ids] or all_peers
        finally:
            con.close()

        if not targets:
            raise ValueError("нет подключённых устройств")

        member_keys = [[r["source"], str(r["source_id"])] for r in rows]
        prices = [r["price"] for r in rows if r.get("price")]
        first = rows[0]
        event_id = uuid.uuid4().hex
        payload = json.dumps({
            "event_id": event_id,
            "origin_device_id": ident["device_id"],
            "origin_name": ident["device_name"],
            "member_keys": member_keys,
            "summary": {
                "url": first.get("url") or "",
                "title": first.get("title") or "",
                "address": first.get("address") or "",
                "rooms": first.get("rooms"),
                "area": first.get("area"),
                "price": min(prices) if prices else first.get("price"),
            },
        }, ensure_ascii=False).encode("utf-8")

        sent_to, errors = [], []
        for p in targets:
            hdrs = self._auth_headers(ident, p)
            hdrs["Content-Type"] = "application/json"
            try:
                st, data = _http_call(p["ip"], p["port"], "POST", "/sync/spotlight", body=payload, headers=hdrs, timeout=3.0)
                if st == 200 and data.get("ok"):
                    sent_to.append({"device_id": p["device_id"], "name": p["name"]})
                elif st == 200 and data.get("ignored"):
                    errors.append({"device_id": p["device_id"], "name": p["name"], "error": "подсветка отключена у получателя"})
                else:
                    errors.append({"device_id": p["device_id"], "name": p["name"], "error": data.get("detail") or f"HTTP {st}"})
            except Exception as e:
                errors.append({"device_id": p["device_id"], "name": p["name"], "error": str(e)})
        if not sent_to and errors:
            raise ValueError(errors[0]["error"])
        return {"ok": len(sent_to) > 0, "event_id": event_id, "sent_to": sent_to, "errors": errors}

    def import_spotlight_card(self, from_device_id: str, source: str, source_id: str):
        """Догружает мини-бандл 1 карточки у партнёра по GET /sync/card и импортирует её."""
        con = db.connect()
        try:
            ident = self._identity(con)
            peer = self._get_peer(con, from_device_id)
        finally:
            con.close()

        q = urllib.parse.urlencode({"source": source, "source_id": str(source_id)})
        hdrs = self._auth_headers(ident, peer)
        try:
            st, _, raw = _http_raw(peer["ip"], peer["port"], "GET", f"/sync/card?{q}", headers=hdrs, timeout=20.0)
        except (ConnectionError, OSError) as e:
            raise ValueError(str(e))
        if st != 200:
            try:
                msg = json.loads(raw.decode("utf-8")).get("detail")
            except Exception:
                msg = None
            raise ValueError(msg or f"не удалось получить карточку с {peer['name']} (HTTP {st})")

        origin = {"device_id": peer["device_id"], "device_name": peer.get("name") or peer["device_id"]}
        with tempfile.TemporaryDirectory(prefix="rw_spot_card_") as td:
            zp = Path(td) / "spotlight.rwcards.zip"
            zp.write_bytes(raw)
            imp_res = exchange.import_cards_zip(
                zp, likes_mode="none", channel="lan", origin_override=origin, apply_manual_merges=True,
            )

        con = db.connect()
        try:
            row = con.execute(
                "SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source=? AND source_id=?",
                (source, str(source_id)),
            ).fetchone()
            oid = row[0] if row else f"{source}:{source_id}"
            mkeys = [
                [r[0], str(r[1])]
                for r in con.execute(
                    "SELECT source, source_id FROM listings WHERE object_id=? OR (object_id IS NULL AND source||':'||source_id=?)",
                    (oid, oid),
                )
            ] or [[source, str(source_id)]]
        finally:
            con.close()
        self._bump_data_rev()
        return {"ok": True, "object_id": oid, "member_keys": mkeys, "import": imp_res}

    def pull_db(self, peer_id: str):
        """Инициирует отправку свежей базы от партнёра себе (POST /sync/request/db)."""
        if runner.manager.any_running():
            raise LanBusyError("Сначала дождитесь окончания сбора данных на этом компьютере")
        if not self._lan_cfg()["enabled"]:
            raise ValueError("включите доступ по локальной сети")
        con = db.connect()
        try:
            ident = self._identity(con)
            peer = self._get_peer(con, peer_id)
        finally:
            con.close()

        hdrs = self._auth_headers(ident, peer)
        hdrs["Content-Type"] = "application/json"
        try:
            st, data = _http_call(peer["ip"], peer["port"], "POST", "/sync/request/db", body=b"{}", headers=hdrs, timeout=240.0)
        except (ConnectionError, OSError) as e:
            raise ValueError(str(e))
        if st != 200 or not data.get("ok"):
            raise ValueError(data.get("detail") or f"устройство {peer['name']} не смогло отправить базу (HTTP {st})")

        req_id = data.get("req_id")
        item = next((it for it in self.list_inbox() if it.get("req_id") == req_id), None)
        return {"ok": True, "req_id": req_id, "item": item, "pull": data}

    # ---------- Обмен отметками, заметками и ручными объединениями (тихий Pull / Push с выбором) ----------

    def _apply_marks_payload(self, payload: dict, items=None, mode: str = "merge") -> dict:
        """Применяет выбранные категории пользовательского слоя (лайки, дизлайки, заметки, ручные склейки)
        в режиме 'merge' (добавить к своим) или 'replace' (перезаписать поверх своих с автобэкапом)."""
        eff_mode = str(mode or "merge").strip().lower()
        if eff_mode not in ("merge", "replace"):
            raise ValueError(f"неизвестный режим применения отметок: {eff_mode}")

        avail_items = _normalize_mark_items(payload.get("items") or [])
        req_items = _normalize_mark_items(items if items is not None else avail_items)
        eff_items = [x for x in req_items if x in avail_items]
        if not eff_items:
            raise ValueError("выберите хотя бы один параметр из доступных в пакете")

        if "merges" in eff_items and runner.manager.any_running():
            raise LanBusyError("Сначала дождитесь окончания сбора объявлений, чтобы применить ручные объединения карточек")

        backup_path = None
        if eff_mode == "replace":
            backup_path = str(transfer.backup("перед_заменой_отметок", with_images=False))

        con = db.connect()
        try:
            my_id = self._identity(con)["device_id"]
            sender_id = str(payload.get("from_device_id") or "lan-peer")[:80]
            sender_name = str(payload.get("from_device_name") or "Партнёр")[:80]
            now_s = _now_ts()

            # Собираем ключи объявлений, на которые ссылаются выбранные категории
            needed_keys: set[tuple[str, str]] = set()
            if "likes" in eff_items:
                for r in payload.get("likes") or []:
                    if isinstance(r, dict) and r.get("source") and r.get("source_id"):
                        needed_keys.add((str(r["source"]), str(r["source_id"])))
            if "dislikes" in eff_items:
                for r in payload.get("dislikes") or []:
                    if isinstance(r, dict) and r.get("source") and r.get("source_id"):
                        needed_keys.add((str(r["source"]), str(r["source_id"])))
            if "notes" in eff_items:
                for r in payload.get("notes") or []:
                    if isinstance(r, dict) and r.get("source") and r.get("source_id") and str(r.get("text") or "").strip():
                        needed_keys.add((str(r["source"]), str(r["source_id"])))
                for inr in payload.get("imported_notes") or []:
                    if isinstance(inr, dict) and inr.get("source") and inr.get("source_id") and str(inr.get("text") or "").strip():
                        needed_keys.add((str(inr["source"]), str(inr["source_id"])))
            if "merges" in eff_items:
                for r in payload.get("manual_merges") or []:
                    if isinstance(r, dict) and r.get("source") and r.get("source_id") and r.get("group_id"):
                        needed_keys.add((str(r["source"]), str(r["source_id"])))

            # Докладываем отсутствующие в локальной базе объявления как imported_only=1 (без влияния на статистику рынка)
            added_listings = 0
            if needed_keys:
                existing_keys = {(r[0], str(r[1])) for r in con.execute("SELECT source, source_id FROM listings")}
                missing_keys = needed_keys - existing_keys
                if missing_keys and payload.get("ref_listings"):
                    lcols = [r[1] for r in con.execute("PRAGMA main.table_info(listings)") if r[1] != "object_id"]
                    col_sql = ",".join(lcols)
                    placeholders = ",".join("?" * len(lcols))
                    for row in payload.get("ref_listings") or []:
                        if not isinstance(row, dict):
                            continue
                        rk = (str(row.get("source") or ""), str(row.get("source_id") or ""))
                        if rk not in missing_keys:
                            continue
                        rec = dict(row)
                        rec["imported_only"] = 1
                        vals = [rec.get(c) for c in lcols]
                        cur = con.execute(f"INSERT OR IGNORE INTO listings({col_sql}) VALUES({placeholders})", vals)
                        if cur.rowcount:
                            added_listings += 1
                    for hr in payload.get("ref_price_history") or []:
                        if not isinstance(hr, dict):
                            continue
                        hk = (str(hr.get("source") or ""), str(hr.get("source_id") or ""))
                        if hk in missing_keys and hr.get("ts") and hr.get("price") is not None:
                            con.execute(
                                "INSERT OR IGNORE INTO price_history(source, source_id, ts, price) VALUES(?,?,?,?)",
                                (hk[0], hk[1], str(hr["ts"]), hr["price"]),
                            )

            # 1. Ручные объединения карточек (выполняем первыми, чтобы пересчитать object_id до раздачи отметок)
            if "merges" in eff_items:
                inc_mm = [
                    {
                        "source": str(r["source"]),
                        "source_id": str(r["source_id"]),
                        "group_id": str(r["group_id"]),
                        "is_primary": 1 if r.get("is_primary") else 0,
                        "ts": str(r.get("ts") or now_s),
                    }
                    for r in (payload.get("manual_merges") or [])
                    if isinstance(r, dict) and r.get("source") and r.get("source_id") and r.get("group_id")
                ]
                if eff_mode == "replace":
                    con.execute("DELETE FROM manual_merges")
                    if inc_mm:
                        con.executemany(
                            "INSERT OR REPLACE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)",
                            [(r["source"], r["source_id"], r["group_id"], r["is_primary"], r["ts"]) for r in inc_mm],
                        )
                else:
                    main_mm = [dict(r) for r in con.execute("SELECT source, source_id, group_id, is_primary, ts FROM manual_merges")]
                    merged_tuples = _union_manual_merges_rows(main_mm, inc_mm)
                    con.execute("DELETE FROM manual_merges")
                    if merged_tuples:
                        con.executemany(
                            "INSERT OR REPLACE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)",
                            merged_tuples,
                        )
                dedupe.clean_manual_merges(con)
                con.commit()
                dedupe.rebuild(con)
                history.mark_snapshots_dirty(con)
            elif added_listings > 0:
                con.commit()
                if not runner.manager.any_running():
                    dedupe.rebuild(con)

            def _expand_with_fallback(src_s: str, sid_s: str) -> list[tuple[str, str]]:
                exp = self._expand_object_keys(con, [(src_s, sid_s)])
                return exp if exp else [(src_s, sid_s)]

            orig_likes = {(r[0], str(r[1])) for r in con.execute("SELECT source, source_id FROM likes")}
            orig_dislikes = {(r[0], str(r[1])) for r in con.execute("SELECT source, source_id FROM dislikes")}

            # 2. Избранное (♥ лайки) и Скрытые (👎 дизлайки)
            inc_likes_map: dict[tuple[str, str], str] = {}
            if "likes" in eff_items:
                for r in payload.get("likes") or []:
                    if not isinstance(r, dict):
                        continue
                    s, sid = str(r.get("source") or ""), str(r.get("source_id") or "")
                    ts = str(r.get("ts") or now_s) or now_s
                    if not s or not sid:
                        continue
                    for ek in _expand_with_fallback(s, sid):
                        if ek not in inc_likes_map or ts > inc_likes_map[ek]:
                            inc_likes_map[ek] = ts
                if eff_mode == "replace":
                    con.execute("DELETE FROM likes")
                    for (s, sid), ts in inc_likes_map.items():
                        con.execute("INSERT OR REPLACE INTO likes(source, source_id, ts) VALUES(?,?,?)", (s, sid, ts))
                        con.execute("DELETE FROM dislikes WHERE source=? AND source_id=?", (s, sid))
                else:
                    for (s, sid), ts in inc_likes_map.items():
                        if (s, sid) in orig_likes or (s, sid) in orig_dislikes:
                            continue
                        con.execute("INSERT OR IGNORE INTO likes(source, source_id, ts) VALUES(?,?,?)", (s, sid, ts))

            if "dislikes" in eff_items:
                inc_dislikes_map: dict[tuple[str, str], str] = {}
                for r in payload.get("dislikes") or []:
                    if not isinstance(r, dict):
                        continue
                    s, sid = str(r.get("source") or ""), str(r.get("source_id") or "")
                    ts = str(r.get("ts") or now_s) or now_s
                    if not s or not sid:
                        continue
                    for ek in _expand_with_fallback(s, sid):
                        if ek not in inc_dislikes_map or ts > inc_dislikes_map[ek]:
                            inc_dislikes_map[ek] = ts
                cur_likes = {(r[0], str(r[1])) for r in con.execute("SELECT source, source_id FROM likes")}
                if eff_mode == "replace":
                    con.execute("DELETE FROM dislikes")
                    for (s, sid), ts in inc_dislikes_map.items():
                        if "likes" in eff_items and (s, sid) in inc_likes_map:
                            continue
                        con.execute("INSERT OR REPLACE INTO dislikes(source, source_id, ts) VALUES(?,?,?)", (s, sid, ts))
                        con.execute("DELETE FROM likes WHERE source=? AND source_id=?", (s, sid))
                else:
                    for (s, sid), ts in inc_dislikes_map.items():
                        if (s, sid) in cur_likes or (s, sid) in orig_dislikes:
                            continue
                        con.execute("INSERT OR IGNORE INTO dislikes(source, source_id, ts) VALUES(?,?,?)", (s, sid, ts))

            # 3. Заметки (личные и атрибутированные)
            if "notes" in eff_items:
                grouped_notes: dict[tuple[tuple[str, str], ...], tuple[str, str]] = {}
                for r in payload.get("notes") or []:
                    if not isinstance(r, dict):
                        continue
                    s, sid = str(r.get("source") or ""), str(r.get("source_id") or "")
                    txt = str(r.get("text") or "").strip()[:2000]
                    ts = str(r.get("ts") or now_s) or now_s
                    if not s or not sid or not txt:
                        continue
                    sibs = tuple(sorted(_expand_with_fallback(s, sid)))
                    prev = grouped_notes.get(sibs)
                    if prev is None or ts >= prev[1]:
                        grouped_notes[sibs] = (txt, ts)

                if eff_mode == "replace":
                    con.execute("DELETE FROM notes")
                    con.execute("DELETE FROM imported_notes")
                    for sibs, (txt, ts) in grouped_notes.items():
                        for s, sid in sibs:
                            con.execute(
                                "INSERT OR REPLACE INTO notes(source, source_id, text, ts) VALUES(?,?,?,?)",
                                (s, sid, txt, ts),
                            )
                    for inr in payload.get("imported_notes") or []:
                        if not isinstance(inr, dict):
                            continue
                        s, sid = str(inr.get("source") or ""), str(inr.get("source_id") or "")
                        oid_orig = str(inr.get("origin_id") or "")
                        oname_orig = str(inr.get("origin_name") or "Импорт")[:80]
                        txt = str(inr.get("text") or "").strip()[:2000]
                        ts = str(inr.get("ts") or now_s)
                        uat = str(inr.get("updated_at") or ts or now_s)
                        if not s or not sid or not oid_orig or not txt or oid_orig == my_id:
                            continue
                        for es, esid in _expand_with_fallback(s, sid):
                            con.execute(
                                "INSERT OR REPLACE INTO imported_notes"
                                "(source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id) "
                                "VALUES(?,?,?,?,?,?,?,NULL)",
                                (es, esid, oid_orig, oname_orig, txt, ts, uat),
                            )
                else:
                    orig_notes = {
                        (str(r["source"]), str(r["source_id"])): str(r["text"] or "").strip()
                        for r in con.execute("SELECT source, source_id, text FROM notes WHERE TRIM(COALESCE(text, '')) != ''")
                    }
                    for sibs, (txt, ts) in grouped_notes.items():
                        existing_txts = [orig_notes[k] for k in sibs if k in orig_notes and orig_notes[k]]
                        existing_txt = existing_txts[0] if existing_txts else ""
                        if not existing_txt:
                            for s, sid in sibs:
                                con.execute(
                                    "INSERT OR REPLACE INTO notes(source, source_id, text, ts) VALUES(?,?,?,?)",
                                    (s, sid, txt, ts),
                                )
                        elif existing_txt != txt:
                            for s, sid in sibs:
                                con.execute(
                                    "INSERT OR REPLACE INTO imported_notes"
                                    "(source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id) "
                                    "VALUES(?,?,?,?,?,?,?,NULL)",
                                    (s, sid, sender_id, sender_name, txt, ts, now_s),
                                )
                    for inr in payload.get("imported_notes") or []:
                        if not isinstance(inr, dict):
                            continue
                        s, sid = str(inr.get("source") or ""), str(inr.get("source_id") or "")
                        oid_orig = str(inr.get("origin_id") or "")
                        oname_orig = str(inr.get("origin_name") or "Импорт")[:80]
                        txt = str(inr.get("text") or "").strip()[:2000]
                        ts = str(inr.get("ts") or now_s)
                        uat = str(inr.get("updated_at") or ts or now_s)
                        if not s or not sid or not oid_orig or not txt or oid_orig == my_id:
                            continue
                        for es, esid in _expand_with_fallback(s, sid):
                            con.execute(
                                """INSERT INTO imported_notes
                                   (source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id)
                                   VALUES(?,?,?,?,?,?,?,NULL)
                                   ON CONFLICT(source, source_id, origin_id) DO UPDATE SET
                                     origin_name=excluded.origin_name, text=excluded.text,
                                     ts=excluded.ts, updated_at=excluded.updated_at
                                   WHERE COALESCE(excluded.updated_at, excluded.ts, '') >=
                                         COALESCE(imported_notes.updated_at, imported_notes.ts, '')""",
                                (es, esid, oid_orig, oname_orig, txt, ts, uat),
                            )

            con.commit()
            counts_after = _marks_counts(con)
        finally:
            con.close()

        self._bump_data_rev()
        return {
            "ok": True,
            "mode": eff_mode,
            "items": eff_items,
            "backup": backup_path,
            "added_listings": added_listings,
            "incoming_counts": {k: int((payload.get("counts") or {}).get(k, 0)) for k in eff_items},
            "counts": counts_after,
            "from_device_name": sender_name,
        }

    def get_marks_info(self, peer_id: str | None = None) -> dict:
        """Возвращает локальные счётчики отметок и (если указан peer_id) тихо запрашивает счётчики партнёра."""
        con = db.connect()
        try:
            ident = self._identity(con)
            local_m = _marks_counts(con)
            peer = self._get_peer(con, peer_id) if peer_id else None
        finally:
            con.close()

        remote_m = None
        remote_err = None
        if peer is not None:
            hdrs = self._auth_headers(ident, peer)
            try:
                st, data = _http_call(peer["ip"], peer["port"], "GET", "/sync/marks/summary", headers=hdrs, timeout=4.0)
                if st == 200 and isinstance(data, dict) and isinstance(data.get("counts"), dict):
                    remote_m = {cat: int(data["counts"].get(cat, 0)) for cat in MARK_CATEGORIES}
                    with self._lock:
                        if peer["device_id"] in self._peer_runtime:
                            self._peer_runtime[peer["device_id"]]["marks_counts"] = remote_m
                else:
                    remote_err = (data or {}).get("detail") or f"HTTP {st}"
            except Exception as e:
                remote_err = str(e)
            if remote_m is None:
                with self._lock:
                    cached = (self._peer_runtime.get(peer["device_id"]) or {}).get("marks_counts")
                    if isinstance(cached, dict):
                        remote_m = dict(cached)

        return {
            "ok": True,
            "local": local_m,
            "remote": remote_m,
            "remote_error": remote_err,
            "busy": runner.manager.any_running(),
            "peer": {"device_id": peer["device_id"], "name": peer["name"]} if peer else None,
        }

    def pull_marks(self, peer_id: str, items, mode: str = "merge") -> dict:
        """Тихо забирает выбранные категории (лайки, дизлайки, заметки, ручные объединения) у сопряжённого
        партнёра без уведомлений на его стороне и применяет локально в режиме merge или replace."""
        sel_items = _normalize_mark_items(items)
        eff_mode = str(mode or "merge").strip().lower()
        if eff_mode not in ("merge", "replace"):
            raise ValueError("режим должен быть 'merge' (добавить) или 'replace' (перезаписать)")
        if "merges" in sel_items and runner.manager.any_running():
            raise LanBusyError("Сначала дождитесь окончания сбора данных, чтобы запросить ручные объединения")

        con = db.connect()
        try:
            ident = self._identity(con)
            peer = self._get_peer(con, peer_id)
        finally:
            con.close()

        q = urllib.parse.urlencode({"items": ",".join(sel_items)})
        hdrs = self._auth_headers(ident, peer)
        try:
            st, _, raw = _http_raw(peer["ip"], peer["port"], "GET", f"/sync/marks?{q}", headers=hdrs, timeout=45.0)
        except (ConnectionError, OSError) as e:
            raise ValueError(str(e))
        if st != 200:
            try:
                detail = json.loads(raw.decode("utf-8", errors="replace")).get("detail")
            except Exception:
                detail = None
            raise ValueError(detail or f"не удалось получить данные с «{peer['name']}» (HTTP {st})")

        payload = _unpack_marks_zip(raw)
        payload["from_device_id"] = peer["device_id"]
        payload["from_device_name"] = peer.get("name") or payload.get("from_device_name") or peer["device_id"]
        res = self._apply_marks_payload(payload, items=sel_items, mode=eff_mode)
        cats_k, all_k, fb_r, n_map = self._extract_marks_history_meta(payload, res["items"])
        con = db.connect()
        try:
            prev_items = self._build_preview_items(con, all_k, fallback_rows=fb_r, notes_map=n_map)
        finally:
            con.close()
        lbl_map = {"likes": "♥ лайки", "dislikes": "👎 дизлайки", "notes": "✎ заметки", "merges": "🔗 дубли"}
        parts_str = ", ".join(f"{lbl_map[k]} ({res['incoming_counts'].get(k, 0)})" for k in res["items"] if k in lbl_map)
        self._record_history(
            kind="marks_pulled",
            peer_id=peer["device_id"],
            peer_name=payload["from_device_name"],
            title="Загружены отметки и дубли",
            detail=f"{'Перезаписано' if eff_mode == 'replace' else 'Добавлено к вашим'}: {parts_str}",
            keys=all_k,
            cats=cats_k,
            preview=prev_items,
            mark_seen=True,
        )
        runner.manager.log(
            "lan",
            f"загружены отметки от «{peer['name']}» ({', '.join(sel_items)}; режим: {'перезапись' if eff_mode == 'replace' else 'добавление'})",
        )
        return res

    def send_marks(self, peer_ids: list[str], items) -> dict:
        """Отправляет выбранные категории (лайки, дизлайки, заметки, ручные объединения) сопряжённому партнёру:
        у получателя всплывает уведомление с перечнем предложенного и выбором добавить / перезаписать."""
        if not peer_ids:
            raise ValueError("выберите хотя бы одно устройство")
        sel_items = _normalize_mark_items(items)

        con = db.connect()
        try:
            ident = self._identity(con)
            peers = [self._get_peer(con, pid) for pid in peer_ids]
            payload = _export_marks_payload(con, ident, sel_items)
        finally:
            con.close()

        raw_zip = _pack_marks_zip(payload)
        results = []
        con = db.connect()
        try:
            for p in peers:
                hdrs = self._auth_headers(ident, p)
                hdrs["Content-Type"] = "application/zip"
                try:
                    st, data = _http_call(p["ip"], p["port"], "POST", "/sync/push/marks", body=raw_zip, headers=hdrs, timeout=45.0)
                    ok = st == 200 and bool(data.get("ok"))
                    err = None if ok else (data.get("detail") or f"HTTP {st}")
                    if ok:
                        _outbox_log(con, p["device_id"], "marks", str(data.get("req_id") or ""))
                except Exception as e:
                    ok, data, err = False, {}, str(e)
                results.append({
                    "device_id": p["device_id"],
                    "name": p["name"],
                    "ok": ok,
                    "error": err,
                    "req_id": data.get("req_id"),
                })
        finally:
            con.close()

        ok_cnt = sum(1 for r in results if r["ok"])
        if not ok_cnt and results:
            raise ValueError(results[0]["error"] or "не удалось доставить данные на устройство")
        runner.manager.log("lan", f"отправлены отметки ({', '.join(sel_items)}) на {ok_cnt} устройств(а)")
        return {
            "ok": ok_cnt > 0,
            "items": sel_items,
            "counts": payload["counts"],
            "results": results,
        }

    def _enqueue_marks_bytes(self, peer: dict, raw: bytes) -> dict:
        if not raw:
            raise HTTPException(400, "Пустой пакет отметок")
        if len(raw) > MAX_MARKS_BYTES:
            raise HTTPException(413, "Пакет отметок превышает допустимый размер")
        try:
            payload = _unpack_marks_zip(raw)
        except ValueError as e:
            raise HTTPException(400, f"Некорректный пакет отметок: {e}")

        req_id = uuid.uuid4().hex[:12]
        origin_name = peer.get("name") or payload.get("from_device_name") or peer["device_id"]
        avail_items = _normalize_mark_items(payload.get("items") or [])
        raw_counts = payload.get("counts") or {}
        counts = {cat: int(raw_counts.get(cat, 0)) if cat in avail_items else 0 for cat in MARK_CATEGORIES}

        with self._lock:
            self._ensure_inbox_loaded()
            idir = self._inbox_dir()
            idir.mkdir(parents=True, exist_ok=True)
            zpath = idir / f"marks_{req_id}.zip"
        part_path = zpath.with_name(zpath.name + ".part")
        part_path.write_bytes(raw)
        try:
            part_path.replace(zpath)
        except Exception:
            part_path.unlink(missing_ok=True)
            raise HTTPException(500, "Не удалось сохранить входящий пакет отметок")

        received_at = _now_ts()
        item = {
            "req_id": req_id,
            "kind": "marks",
            "status": "pending",
            "received_at": received_at,
            "from_device_id": peer["device_id"],
            "from_device_name": origin_name,
            "from_ip": peer.get("ip") or "",
            "from_port": peer.get("port") or 8766,
            "zip_path": str(zpath),
            "items": avail_items,
            "counts": counts,
        }
        with self._lock:
            self._ensure_inbox_loaded()
            self._inbox[req_id] = item
            self._save_inbox_manifest()
        self._push_live_event("marks_offer", {
            "event_id": f"marks_offer_{req_id}",
            "req_id": req_id,
            "from_device_id": peer["device_id"],
            "from_device_name": origin_name,
            "items": avail_items,
            "counts": counts,
            "received_at": received_at,
        })
        cats_k, all_k, fb_r, n_map = self._extract_marks_history_meta(payload, avail_items)
        con = db.connect()
        try:
            prev_items = self._build_preview_items(con, all_k, fallback_rows=fb_r, notes_map=n_map)
        finally:
            con.close()
        lbl_map = {"likes": "♥ лайки", "dislikes": "👎 дизлайки", "notes": "✎ заметки", "merges": "🔗 дубли"}
        parts_str = ", ".join(f"{lbl_map[k]} ({counts.get(k, 0)})" for k in avail_items if k in lbl_map)
        self._record_history(
            kind="marks_offer",
            peer_id=peer["device_id"],
            peer_name=origin_name,
            req_id=req_id,
            title="Предложены отметки и дубли",
            detail=parts_str,
            keys=all_k,
            cats=cats_k,
            preview=prev_items,
        )
        runner.manager.log("lan", f"входящее предложение отметок от «{origin_name}» ({', '.join(avail_items)})")
        return {"ok": True, "req_id": req_id, "kind": "marks", "queued": True}

    def apply_live_favorite(self, keys: list, on: bool, from_device_id: str = "", import_missing: bool = True):
        """Применяет входящее Live-Избранное (при auto_add/auto_sync или по кнопке «Принять»)."""
        clean = [(str(k[0]), str(k[1])) for k in (keys or []) if isinstance(k, (list, tuple)) and len(k) == 2]
        if not clean:
            return {"ok": True, "changed": 0}
        con = db.connect()
        try:
            targets = self._expand_object_keys(con, clean)
        finally:
            con.close()

        if not targets and on and import_missing and from_device_id:
            try:
                self.import_spotlight_card(from_device_id, clean[0][0], clean[0][1])
                con = db.connect()
                try:
                    targets = self._expand_object_keys(con, clean)
                finally:
                    con.close()
            except Exception:
                targets = []

        if not targets:
            return {"ok": True, "changed": 0, "missing": True}

        con = db.connect()
        try:
            now_s = _now_ts()
            for src, sid in targets:
                if on:
                    con.execute("INSERT OR IGNORE INTO likes(source, source_id, ts) VALUES(?,?,?)", (src, sid, now_s))
                    con.execute("DELETE FROM dislikes WHERE source=? AND source_id=?", (src, sid))
                else:
                    con.execute("DELETE FROM likes WHERE source=? AND source_id=?", (src, sid))
            con.commit()
        finally:
            con.close()
        self._bump_data_rev()
        return {"ok": True, "changed": len(targets)}

    def _peers_for_live(self, pred):
        if not self._lan_cfg()["enabled"]:
            return None, []
        con = db.connect()
        try:
            ident = self._identity(con)
            rows = [dict(r) for r in con.execute("SELECT * FROM sync_peers")]
        finally:
            con.close()
        matched = [r for r in rows if pred(_normalize_live_cfg(r.get("live_cfg_json")))]
        return ident, matched

    def on_local_favorite(self, keys: list[tuple[str, str]], on: bool):
        """Хук при локальном изменении ♥: ставит Live-событие целевым пирам."""
        if not keys:
            return
        ident, peers = self._peers_for_live(
            lambda c: c["fav_send"] == "add_remove" or (bool(on) and c["fav_send"] == "add_only")
        )
        if not peers or not ident:
            return
        summary_obj = {}
        con = db.connect()
        try:
            src0, sid0 = keys[0]
            r = con.execute("SELECT url, title, address, rooms, area, price FROM listings WHERE source=? AND source_id=?", (src0, str(sid0))).fetchone()
            if r:
                summary_obj = dict(r)
        finally:
            con.close()
        self._enqueue_live(peers, "live_fav", {
            "origin_device_id": ident["device_id"],
            "origin_name": ident["device_name"],
            "keys": [[str(s), str(sid)] for s, sid in keys],
            "on": bool(on),
            "summary": summary_obj,
        })

    def on_local_note(self, keys: list[tuple[str, str]], text: str, ts: str):
        """Хук при сохранении/очистке личной заметки: пушит пирам с live_notes=True."""
        if not keys:
            return
        ident, peers = self._peers_for_live(lambda c: bool(c["live_notes"]))
        if not peers or not ident:
            return
        self._enqueue_live(peers, "live_note", {
            "origin_device_id": ident["device_id"],
            "origin_name": ident["device_name"],
            "keys": [[str(s), str(sid)] for s, sid in keys],
            "text": str(text or ""),
            "ts": str(ts or _now_ts()),
        })

    def on_local_merge(self, action: str, primary_keys: list[tuple[str, str]] | None = None,
                       secondary_keys: list[tuple[str, str]] | None = None,
                       keys: list[tuple[str, str]] | None = None):
        """Хук при ручном объединении / разделении: пушит пирам с live_merges=True."""
        ident, peers = self._peers_for_live(lambda c: bool(c["live_merges"]))
        if not peers or not ident:
            return
        self._enqueue_live(peers, "live_merge", {
            "origin_device_id": ident["device_id"],
            "origin_name": ident["device_name"],
            "action": action,
            "primary_keys": [[str(s), str(sid)] for s, sid in (primary_keys or [])],
            "secondary_keys": [[str(s), str(sid)] for s, sid in (secondary_keys or [])],
            "keys": [[str(s), str(sid)] for s, sid in (keys or [])],
            "ts": _now_ts(),
        })

    # ---------- приёмники FastAPI /sync/* ----------

    def _authenticate_request(self, request: Request) -> dict:
        dev_id = (request.headers.get("X-RW-Device") or "").strip()
        auth = (request.headers.get("Authorization") or "").strip()
        if not dev_id or not auth.lower().startswith("bearer "):
            raise LanAuthError("Требуются заголовки X-RW-Device и Authorization: Bearer")
        token = auth[7:].strip()
        if not token:
            raise LanAuthError("Пустой токен авторизации")

        con = db.connect()
        try:
            row = con.execute("SELECT * FROM sync_peers WHERE device_id=?", (dev_id,)).fetchone()
            if row is None:
                raise LanAuthError("Устройство не сопряжено")
            expected_hash = str(row["token_hash"] or "")
            if not expected_hash or not hmac.compare_digest(_hash_token(token), expected_hash):
                raise LanAuthError("Неверный токен устройства")
            client_ip = _clean_ip_str(request.client.host if request.client else "")
            peer = dict(row)
            # Мягко обновляем last_seen и фактический LAN IP / порт при DHCP-переадресации
            new_ip = peer["ip"]
            if client_ip and _is_lan_ip(client_ip) and (
                not peer["ip"] or (not ipaddress.ip_address(client_ip).is_loopback) or ipaddress.ip_address(peer["ip"]).is_loopback
            ):
                new_ip = client_ip
            new_port = int(peer.get("port") or 8766)
            x_port_raw = (request.headers.get("X-RW-Port") or "").strip()
            if x_port_raw:
                try:
                    xp = int(x_port_raw)
                    if 1024 <= xp <= 65534:
                        new_port = xp
                except (TypeError, ValueError):
                    pass
            con.execute(
                "UPDATE sync_peers SET last_seen=?, ip=?, port=? WHERE device_id=?",
                (_now_ts(), new_ip, new_port, dev_id),
            )
            con.commit()
            peer["ip"] = new_ip
            peer["port"] = new_port
            with self._lock:
                was_online = bool((self._peer_runtime.get(dev_id) or {}).get("online"))
            if not was_online and self.is_listening():
                self._hb_evt.set()
            return peer
        finally:
            con.close()

    def _enqueue_bundle_bytes(self, peer: dict, raw: bytes, expected_kind: str):
        if not raw:
            raise HTTPException(400, "Пустой архив")
        if len(raw) > MAX_CARDS_BYTES:
            raise HTTPException(413, "Архив слишком большой (больше 250 МБ)")
        req_id = uuid.uuid4().hex[:12]
        origin = {"device_id": peer["device_id"], "device_name": peer.get("name") or peer["device_id"]}

        with self._lock:
            self._ensure_inbox_loaded()
            idir = self._inbox_dir()
            idir.mkdir(parents=True, exist_ok=True)
            zpath = idir / f"{expected_kind}_{req_id}.rwcards.zip"
        part_path = zpath.with_name(zpath.name + ".part")
        part_path.write_bytes(raw)
        try:
            with zipfile.ZipFile(part_path, "r") as zf:
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"повреждён файл в архиве: {bad}")
            preview = exchange.preview_cards_zip(part_path, origin_override=origin)
            part_path.replace(zpath)
        except Exception as e:
            part_path.unlink(missing_ok=True)
            zpath.unlink(missing_ok=True)
            raise HTTPException(400, f"Некорректный пакет карточек: {e}")

        kind = expected_kind if expected_kind in ("cards", "favorites") else (
            "favorites" if preview.get("kind") == "favorites" else "cards"
        )
        item = {
            "req_id": req_id,
            "kind": kind,
            "status": "pending",
            "received_at": _now_ts(),
            "from_device_id": peer["device_id"],
            "from_device_name": origin["device_name"],
            "from_ip": peer.get("ip") or "",
            "from_port": peer.get("port") or 8766,
            "zip_path": str(zpath),
            "preview": preview,
            "summary": {
                "total": preview.get("total_cards", 0),
                "new": preview.get("new_cards", 0),
                "matched": preview.get("matched_cards", 0),
                "duplicate": preview.get("duplicate_cards", 0),
                "stale": preview.get("stale_cards", 0),
                "updated_notes": preview.get("updated_notes", 0),
            },
        }
        with self._lock:
            self._ensure_inbox_loaded()
            self._inbox[req_id] = item
            self._save_inbox_manifest()
        b_keys, fb_r, n_map = self._extract_bundle_history_meta(zpath)
        con = db.connect()
        try:
            prev_items = self._build_preview_items(con, b_keys, fallback_rows=fb_r, notes_map=n_map)
        finally:
            con.close()
        self._record_history(
            kind="favorites_offer" if kind == "favorites" else "cards_offer",
            peer_id=peer["device_id"],
            peer_name=origin["device_name"],
            req_id=req_id,
            title="Входящее Избранное (♥)" if kind == "favorites" else "Входящий пакет карточек",
            detail=f"Карточек: {item['summary']['total']} (новых: {item['summary']['new']}, в базе: {item['summary']['matched']})",
            keys=b_keys,
            cats={"likes": b_keys} if kind == "favorites" and b_keys else {},
            preview=prev_items,
        )
        runner.manager.log(
            "lan",
            f"входящий запрос от «{origin['device_name']}»: "
            + ("Избранное" if kind == "favorites" else "карточки")
            + f" ({item['summary']['total']} шт.)",
        )
        return {"ok": True, "req_id": req_id, "kind": kind, "queued": True}

    def _build_app(self) -> FastAPI:
        svc = self
        app = FastAPI(title="RealtyWatch LAN Sync", docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def lan_only_guard(request: Request, call_next):
            host = request.client.host if request.client else ""
            if not _is_lan_ip(host):
                return JSONResponse({"detail": "Доступ разрешён только из локальной сети"}, status_code=403)
            return await call_next(request)

        @app.get("/sync/status")
        def sync_status(request: Request):
            try:
                svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            con = db.connect()
            try:
                ident = svc._identity(con)
                freshness = exchange._db_freshness(con)
                listings_cnt = con.execute("SELECT COUNT(*) FROM listings WHERE COALESCE(imported_only,0)=0").fetchone()[0]
                likes_cnt = con.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
                m_counts = _marks_counts(con)
            finally:
                con.close()
            cfg = svc._lan_cfg()
            return {
                "ok": True,
                "device_id": ident["device_id"],
                "device_name": ident["device_name"],
                "port": svc._running_port or cfg["port"],
                "platform": _local_platform_name(),
                "app_version": current_lan_app_version(),
                "freshness": freshness,
                "latest_db_ts": _max_freshness_ts(freshness),
                "listings_count": listings_cnt,
                "likes_count": likes_cnt,
                "marks_counts": m_counts,
                "busy": runner.manager.any_running(),
            }

        @app.post("/sync/pair")
        async def sync_pair(request: Request):
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(400, "Некорректный JSON")
            if not isinstance(body, dict):
                raise HTTPException(400, "Ожидался JSON-объект")

            pin_in = re.sub(r"\s+", "", str(body.get("pin") or ""))
            peer_id = str(body.get("device_id") or "").strip()
            peer_name = str(body.get("device_name") or "").strip() or "Устройство LAN"
            peer_token = str(body.get("token") or "").strip()
            try:
                peer_port = int(body.get("port") or 8766)
            except (TypeError, ValueError):
                raise HTTPException(400, "Некорректный порт")
            if not peer_id or not peer_token or not (1 <= peer_port <= 65535):
                raise HTTPException(400, "Отсутствуют обязательные параметры устройства")

            client_ip = _clean_ip_str(request.client.host if request.client else "") or "127.0.0.1"
            cfg = svc._lan_cfg()
            eff_srv_port = int(svc._running_port or cfg["port"])

            con = db.connect()
            try:
                ident = svc._identity(con)
            finally:
                con.close()
            if peer_id == ident["device_id"] and svc._is_self_endpoint(client_ip, peer_port, eff_srv_port):
                raise HTTPException(400, "Нельзя сопрячь устройство с самим собой")

            with svc._lock:
                st = svc._pin_state
                if not st or time.time() > st["expires_at"]:
                    svc._pin_state = None
                    raise HTTPException(403, "PIN-код не создан или истёк")
                if not hmac.compare_digest(pin_in, st["pin"]):
                    st["fails"] += 1
                    if st["fails"] >= PIN_MAX_TRIES:
                        svc._pin_state = None
                        raise HTTPException(429, "Превышено число попыток — PIN сброшен, сгенерируйте новый")
                    left = PIN_MAX_TRIES - st["fails"]
                    raise HTTPException(403, f"Неверный PIN (осталось попыток: {left})")
                # PIN одноразовый — после успешной проверки сразу гасим
                svc._pin_state = None

            if peer_id == ident["device_id"]:
                ident = svc._heal_collided_device_id(client_ip, peer_name)

            token_from_me = secrets.token_urlsafe(32)
            now_s = _now_ts()

            con = db.connect()
            try:
                con.execute(
                    """INSERT INTO sync_peers(device_id, name, ip, port, token_hash, peer_token, paired_at, last_seen)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(device_id) DO UPDATE SET
                         name=excluded.name, ip=excluded.ip, port=excluded.port,
                         token_hash=excluded.token_hash, peer_token=excluded.peer_token,
                         paired_at=excluded.paired_at, last_seen=excluded.last_seen""",
                    (peer_id, peer_name[:80], client_ip, peer_port, _hash_token(token_from_me), peer_token, now_s, now_s),
                )
                con.commit()
            finally:
                con.close()

            with svc._lock:
                svc._discovered.pop(peer_id, None)
                svc._bootstrapped_peers.add(peer_id)
                svc._peer_runtime[peer_id] = {
                    "online": True,
                    "last_check": now_s,
                    "device_name": peer_name[:80],
                    "platform": str(body.get("platform") or ""),
                    "error": None,
                }
            runner.manager.log("lan", f"устройство «{peer_name[:80]}» подключилось по PIN ({client_ip}:{peer_port})")
            cfg = svc._lan_cfg()
            return {
                "ok": True,
                "device_id": ident["device_id"],
                "device_name": ident["device_name"],
                "port": svc._running_port or cfg["port"],
                "platform": _local_platform_name(),
                "token": token_from_me,
            }

        @app.post("/sync/push/cards")
        async def push_cards(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            raw = await _read_checked_body(request, MAX_CARDS_BYTES, empty_msg="Пустой архив")
            return svc._enqueue_bundle_bytes(peer, raw, "cards")

        @app.post("/sync/push/favorites")
        async def push_favorites(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            raw = await _read_checked_body(request, MAX_CARDS_BYTES, empty_msg="Пустой архив")
            return svc._enqueue_bundle_bytes(peer, raw, "favorites")

        @app.post("/sync/push/db/init")
        async def push_db_init(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            if runner.manager.any_running():
                raise HTTPException(409, "Идёт сбор объявлений — приём базы временно недоступен")

            raw = await _read_checked_body(request, MAX_DB_BYTES, empty_msg="Пустой пакет базы")

            req_id = uuid.uuid4().hex[:12]
            with svc._lock:
                svc._ensure_inbox_loaded()
                sess_dir = svc._inbox_dir() / f"db_{req_id}"
            sess_dir.mkdir(parents=True, exist_ok=True)
            init_part = sess_dir / "init.zip.part"
            init_part.write_bytes(raw)
            try:
                with zipfile.ZipFile(init_part, "r") as zf:
                    bad = zf.testzip()
                    if bad:
                        raise ValueError(f"архив снимка базы повреждён: {bad}")
                    names = set(zf.namelist())
                    if "realty.db" not in names or "meta.json" not in names:
                        raise ValueError("в архиве отсутствуют realty.db или meta.json")
                    meta = json.loads(zf.read("meta.json").decode("utf-8"))
                    if meta.get("format") != _DB_SYNC_FORMAT:
                        raise ValueError("неподдерживаемый формат синхронизации базы")
                    db_part = sess_dir / "realty.db.part"
                    db_out = sess_dir / "realty.db"
                    with zf.open("realty.db") as src_f, open(db_part, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
                    db_part.replace(db_out)
                _validate_sqlite_snapshot(db_out)
            except Exception as e:
                shutil.rmtree(sess_dir, ignore_errors=True)
                raise HTTPException(400, f"Некорректный снимок базы: {e}")
            finally:
                init_part.unlink(missing_ok=True)

            # Расчёт дельты фото: проверяем по (source, source_id) и непустому размеру
            missing = []
            manifest_imgs = meta.get("images_manifest") or []
            for img in manifest_imgs:
                if not isinstance(img, dict):
                    continue
                src = str(img.get("source") or "")
                sid = str(img.get("source_id") or "")
                ext = str(img.get("ext") or "jpg").lower()
                rel = f"{src}/{sid}.{ext}"
                if not _safe_img_rel(rel):
                    continue
                lp = images.local_path(src, sid)
                try:
                    has_file = lp is not None and lp.is_file() and lp.stat().st_size > 0
                except OSError:
                    has_file = False
                if not has_file:
                    missing.append(rel)

            item = {
                "req_id": req_id,
                "kind": "db",
                "status": "pending",
                "received_at": _now_ts(),
                "from_device_id": peer["device_id"],
                "from_device_name": peer.get("name") or peer["device_id"],
                "from_ip": peer.get("ip") or "",
                "from_port": peer.get("port") or 8766,
                "sess_dir": str(sess_dir),
                "db_path": str(sess_dir / "realty.db"),
                "sender_freshness": meta.get("freshness") or {},
                "sender_counts": meta.get("counts") or {},
                "filters_zone": meta.get("filters_zone") or {},
                "images_total": len(manifest_imgs),
                "images_missing_requested": len(missing),
                "images_received": 0,
            }

            if not missing:
                with svc._lock:
                    svc._ensure_inbox_loaded()
                    svc._inbox[req_id] = item
                    svc._save_inbox_manifest()
                svc._record_history(
                    kind="db_offer",
                    peer_id=peer["device_id"],
                    peer_name=item["from_device_name"],
                    req_id=req_id,
                    title="Входящая база данных",
                    detail=f"Объявлений: {(meta.get('counts') or {}).get('listings', 0)} · все фото уже есть",
                )
                runner.manager.log("lan", f"входящая база от «{item['from_device_name']}» (все фото уже есть)")
                return {"ok": True, "req_id": req_id, "missing_images": [], "queued": True}

            item["created_mono"] = time.monotonic()
            with svc._lock:
                svc._db_sessions[req_id] = item
            return {"ok": True, "req_id": req_id, "missing_images": missing, "queued": False}

        @app.post("/sync/push/db/images/{req_id}")
        async def push_db_images(req_id: str, request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            with svc._lock:
                sess = svc._db_sessions.get(req_id)
                if sess is None:
                    raise HTTPException(404, "Сессия передачи базы не найдена или уже завершена")
                if sess.get("from_device_id") != peer["device_id"]:
                    raise HTTPException(403, "Сессия принадлежит другому устройству")

            raw = await _read_checked_body(request, MAX_DB_BYTES)

            sess_dir = Path(sess["sess_dir"])
            delta_part = sess_dir / "delta.zip.part"
            delta_part.write_bytes(raw)
            recv_cnt = 0
            img_root = sess_dir / "images"
            img_root.mkdir(parents=True, exist_ok=True)
            created_parts: list[Path] = []
            try:
                if raw:
                    with zipfile.ZipFile(delta_part, "r") as zf:
                        bad = zf.testzip()
                        if bad:
                            raise ValueError(f"повреждён файл фото в пакете: {bad}")
                        for info in zf.infolist():
                            if info.is_dir():
                                continue
                            name = info.filename.replace("\\", "/")
                            if name.startswith("images/"):
                                name = name[7:]
                            parsed = _safe_img_rel(name)
                            if not parsed:
                                continue
                            src, _, _, fname = parsed
                            out_dir = img_root / src
                            out_dir.mkdir(parents=True, exist_ok=True)
                            target = out_dir / fname
                            if not target.resolve().is_relative_to(img_root.resolve()):
                                continue
                            t_part = out_dir / f"{fname}.part"
                            created_parts.append(t_part)
                            with zf.open(info) as s_f, open(t_part, "wb") as d_f:
                                shutil.copyfileobj(s_f, d_f)
                            t_part.replace(target)
                            recv_cnt += 1
            except Exception as e:
                for p in created_parts:
                    p.unlink(missing_ok=True)
                raise HTTPException(400, f"Некорректный пакет фотографий: {e}")
            finally:
                delta_part.unlink(missing_ok=True)

            with svc._lock:
                svc._db_sessions.pop(req_id, None)
                sess_clean = dict(sess)
                sess_clean.pop("created_mono", None)
                sess_clean["images_received"] = recv_cnt
                svc._ensure_inbox_loaded()
                svc._inbox[req_id] = sess_clean
                svc._save_inbox_manifest()
            svc._record_history(
                kind="db_offer",
                peer_id=peer["device_id"],
                peer_name=sess_clean["from_device_name"],
                req_id=req_id,
                title="Входящая база данных",
                detail=f"Объявлений: {(sess_clean.get('sender_counts') or {}).get('listings', 0)} · получено фото: {recv_cnt}",
            )
            runner.manager.log(
                "lan",
                f"входящая база от «{sess_clean['from_device_name']}» готова (получено фото: {recv_cnt})",
            )
            return {"ok": True, "req_id": req_id, "received_images": recv_cnt, "queued": True}

        @app.post("/sync/spotlight")
        async def sync_spotlight(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            lcfg = _normalize_live_cfg(peer.get("live_cfg_json"))
            if not lcfg["accept_spotlight"]:
                return {"ok": False, "ignored": True}
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "Ожидался JSON-объект")
            eid = str(body.get("event_id") or "").strip()
            if eid and svc._mark_seen_event(eid):
                return {"ok": True, "duplicate": True}
            origin_name = str(body.get("origin_name") or peer.get("name") or "Партнёр")
            keys = body.get("member_keys") or []
            sum_obj = body.get("summary") or {}
            ev = svc._push_live_event("spotlight", {
                "event_id": eid or uuid.uuid4().hex,
                "from_device_id": peer["device_id"],
                "from_device_name": origin_name,
                "member_keys": keys,
                "summary": sum_obj,
            })
            con = db.connect()
            try:
                prev_items = svc._build_preview_items(con, keys, summary_hint=sum_obj)
            finally:
                con.close()
            det_parts = [
                f"{sum_obj['rooms']}-к" if sum_obj.get("rooms") else ("студия" if sum_obj.get("rooms") == 0 else ""),
                f"{sum_obj['area']} м²" if sum_obj.get("area") else "",
                sum_obj.get("address") or sum_obj.get("title") or "",
            ]
            svc._record_history(
                kind="spotlight",
                peer_id=peer["device_id"],
                peer_name=origin_name,
                title="Показал карточку (👁 пинг)",
                detail=", ".join(p for p in det_parts if p),
                keys=keys,
                preview=prev_items,
            )
            runner.manager.log("lan", f"подсветка от «{origin_name}»")
            return {"ok": True, "queued": True, "seq": ev["seq"]}

        @app.get("/sync/card")
        def sync_card(request: Request, source: str = "", source_id: str = ""):
            try:
                svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            src = str(source or "").strip()
            sid = str(source_id or "").strip()
            if not src or not sid:
                raise HTTPException(400, "Укажите source и source_id")
            con = db.connect()
            try:
                row = con.execute(
                    "SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source=? AND source_id=?",
                    (src, sid),
                ).fetchone()
                if row is None:
                    raise HTTPException(404, "Карточка не найдена на этом устройстве")
                oid = row[0]
            finally:
                con.close()
            with tempfile.TemporaryDirectory(prefix="rw_sync_card_") as td:
                zp = Path(td) / "card.rwcards.zip"
                exchange.export_cards_zip(zp, object_ids=[oid], liked_only=False, kind="cards")
                data = zp.read_bytes()
            return Response(content=data, media_type="application/zip")

        @app.post("/sync/request/db")
        def sync_request_db(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            if runner.manager.any_running():
                raise HTTPException(409, "На этом устройстве идёт сбор — попробуйте чуть позже")
            try:
                return svc.send_db(peer["device_id"])
            except ValueError as e:
                raise HTTPException(400, str(e))

        @app.post("/sync/live/favorite")
        async def sync_live_favorite(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "Ожидался JSON-объект")
            eid = str(body.get("event_id") or "").strip()
            if eid and svc._mark_seen_event(eid):
                return {"ok": True, "duplicate": True}
            con = db.connect()
            try:
                my_id = svc._identity(con)["device_id"]
            finally:
                con.close()
            if str(body.get("origin_device_id") or "") == my_id:
                return {"ok": True, "self_ignored": True}

            lcfg = _normalize_live_cfg(peer.get("live_cfg_json"))
            mode = lcfg["fav_recv"]
            on = bool(body.get("on", True))
            keys = body.get("keys") or []
            origin_name = str(body.get("origin_name") or peer.get("name") or "Партнёр")

            sum_obj = body.get("summary") or {}
            if mode == "ask":
                ev = svc._push_live_event("live_fav", {
                    "event_id": eid or uuid.uuid4().hex,
                    "from_device_id": peer["device_id"],
                    "from_device_name": origin_name,
                    "keys": keys,
                    "on": on,
                    "summary": sum_obj,
                })
                con = db.connect()
                try:
                    prev_items = svc._build_preview_items(con, keys, summary_hint=sum_obj)
                finally:
                    con.close()
                svc._record_history(
                    kind="live_fav",
                    peer_id=peer["device_id"],
                    peer_name=origin_name,
                    title="Live-Избранное: ♥ отмечено" if on else "Live-Избранное: ♥ снято",
                    detail=str(sum_obj.get("address") or sum_obj.get("title") or ""),
                    keys=keys,
                    cats={"likes": keys} if on and keys else {},
                    preview=prev_items,
                )
                return {"ok": True, "mode": "ask", "seq": ev["seq"]}
            if mode == "auto_add" and not on:
                return {"ok": False, "ignored": True, "reason": "auto_add"}
            res = svc.apply_live_favorite(keys, on, from_device_id=peer["device_id"], import_missing=on)
            con = db.connect()
            try:
                prev_items = svc._build_preview_items(con, keys, summary_hint=sum_obj)
            finally:
                con.close()
            svc._record_history(
                kind="live_fav",
                peer_id=peer["device_id"],
                peer_name=origin_name,
                title="Live-Избранное: ♥ добавлено" if on else "Live-Избранное: ♥ снято",
                detail=str(sum_obj.get("address") or sum_obj.get("title") or ""),
                keys=keys,
                cats={"likes": keys} if on and keys else {},
                preview=prev_items,
            )
            return {"ok": True, "mode": mode, **res}

        @app.post("/sync/live/note")
        async def sync_live_note(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "Ожидался JSON-объект")
            eid = str(body.get("event_id") or "").strip()
            if eid and svc._mark_seen_event(eid):
                return {"ok": True, "duplicate": True}
            lcfg = _normalize_live_cfg(peer.get("live_cfg_json"))
            if not lcfg["live_notes"]:
                return {"ok": False, "ignored": True}
            origin_id = str(body.get("origin_device_id") or peer["device_id"])
            origin_name = str(body.get("origin_name") or peer.get("name") or origin_id)
            text = str(body.get("text") or "").strip()[:2000]
            ts = str(body.get("ts") or _now_ts())
            raw_keys = [(str(k[0]), str(k[1])) for k in (body.get("keys") or []) if isinstance(k, (list, tuple)) and len(k) == 2]
            if not raw_keys:
                return {"ok": True, "updated": 0}

            con = db.connect()
            try:
                if origin_id == svc._identity(con)["device_id"]:
                    return {"ok": True, "self_ignored": True}
                targets = svc._expand_object_keys(con, raw_keys)
                updated = 0
                for src, sid in targets:
                    if text:
                        prev = con.execute(
                            "SELECT batch_id FROM imported_notes WHERE source=? AND source_id=? AND origin_id=?",
                            (src, sid, origin_id),
                        ).fetchone()
                        bid = prev[0] if prev else None
                        con.execute(
                            """INSERT OR REPLACE INTO imported_notes
                               (source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id)
                               VALUES(?,?,?,?,?,?,?,?)""",
                            (src, sid, origin_id, origin_name, text, ts, _now_ts(), bid),
                        )
                    else:
                        con.execute(
                            "DELETE FROM imported_notes WHERE source=? AND source_id=? AND origin_id=?",
                            (src, sid, origin_id),
                        )
                    updated += 1
                con.commit()
                prev_items = svc._build_preview_items(con, targets or raw_keys) if updated else []
            finally:
                con.close()
            if updated:
                svc._bump_data_rev()
                svc._push_live_event("live_note", {
                    "event_id": eid or uuid.uuid4().hex,
                    "from_device_id": peer["device_id"],
                    "from_device_name": origin_name,
                    "keys": raw_keys,
                    "text": text,
                })
                svc._record_history(
                    kind="live_note",
                    peer_id=peer["device_id"],
                    peer_name=origin_name,
                    title="Заметка от партнёра (✎)" if text else "Заметка удалена партнёром",
                    detail=text[:300] if text else "Текст заметки очищен",
                    keys=targets or raw_keys,
                    cats={"notes": targets or raw_keys},
                    preview=prev_items,
                )
            return {"ok": True, "updated": updated}

        @app.post("/sync/live/merge")
        async def sync_live_merge(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "Ожидался JSON-объект")
            eid = str(body.get("event_id") or "").strip()
            if eid and svc._mark_seen_event(eid):
                return {"ok": True, "duplicate": True}
            lcfg = _normalize_live_cfg(peer.get("live_cfg_json"))
            if not lcfg["live_merges"]:
                return {"ok": False, "ignored": True}
            if runner.manager.any_running():
                raise HTTPException(409, "Идёт сбор объявлений")

            action = str(body.get("action") or "")
            origin_name = str(body.get("origin_name") or peer.get("name") or "Партнёр")
            con = db.connect()
            try:
                if str(body.get("origin_device_id") or "") == svc._identity(con)["device_id"]:
                    return {"ok": True, "self_ignored": True}

                def _resolve_oid(key_list):
                    for k in key_list or []:
                        if isinstance(k, (list, tuple)) and len(k) == 2:
                            r = con.execute(
                                "SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source=? AND source_id=?",
                                (str(k[0]), str(k[1])),
                            ).fetchone()
                            if r:
                                return r[0]
                    return None

                applied = False
                if action == "merge":
                    p_oid = _resolve_oid(body.get("primary_keys"))
                    s_oid = _resolve_oid(body.get("secondary_keys"))
                    if p_oid and s_oid and p_oid != s_oid:
                        dedupe.merge_objects(con, p_oid, s_oid)
                        history.mark_snapshots_dirty(con)
                        applied = True
                elif action == "unmerge":
                    oid = _resolve_oid(body.get("keys"))
                    if oid:
                        try:
                            dedupe.unmerge_object(con, oid)
                            history.mark_snapshots_dirty(con)
                            applied = True
                        except ValueError:
                            pass
                elif action == "exclude":
                    pk = (body.get("primary_keys") or [None])[0]
                    if isinstance(pk, (list, tuple)) and len(pk) == 2:
                        oid = _resolve_oid([pk])
                        if oid:
                            try:
                                dedupe.exclude_from_merge(con, oid, str(pk[0]), str(pk[1]))
                                history.mark_snapshots_dirty(con)
                                applied = True
                            except ValueError:
                                pass
                elif action == "unmerge_all":
                    dedupe.unmerge_all(con)
                    history.mark_snapshots_dirty(con)
                    applied = True
                m_keys = (body.get("primary_keys") or []) + (body.get("secondary_keys") or []) + (body.get("keys") or [])
                prev_items = svc._build_preview_items(con, m_keys) if applied and m_keys else []
            finally:
                con.close()
            if applied:
                svc._bump_data_rev()
                act_title = {
                    "merge": "Объединены карточки в дубль (🔗)",
                    "unmerge": "Разделена объединённая карточка",
                    "exclude": "Исключено объявление из склейки",
                    "unmerge_all": "Сброшены ручные объединения",
                }.get(action, "Изменение ручных объединений")
                svc._record_history(
                    kind="live_merge",
                    peer_id=peer["device_id"],
                    peer_name=origin_name,
                    title=act_title,
                    keys=m_keys,
                    cats={"merges": m_keys} if m_keys else {},
                    preview=prev_items,
                )
            return {"ok": True, "applied": applied}

        @app.get("/sync/marks/summary")
        def sync_marks_summary(request: Request):
            try:
                svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            con = db.connect()
            try:
                counts = _marks_counts(con)
            finally:
                con.close()
            return {"ok": True, "counts": counts}

        @app.get("/sync/marks")
        def sync_marks_pull(request: Request, items: str = "likes,dislikes,notes,merges"):
            try:
                svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            try:
                sel_items = _normalize_mark_items(items)
            except ValueError as e:
                raise HTTPException(400, str(e))
            con = db.connect()
            try:
                ident = svc._identity(con)
                payload = _export_marks_payload(con, ident, sel_items)
            finally:
                con.close()
            raw_zip = _pack_marks_zip(payload)
            return Response(content=raw_zip, media_type="application/zip")

        @app.post("/sync/push/marks")
        async def push_marks(request: Request):
            try:
                peer = svc._authenticate_request(request)
            except LanAuthError as e:
                raise HTTPException(401, str(e))
            raw = await _read_checked_body(request, MAX_MARKS_BYTES, empty_msg="Пустой пакет отметок")
            return svc._enqueue_marks_bytes(peer, raw)

        return app


# Основной экземпляр сервиса для приложения
service = LanService()


def start_if_enabled():
    return service.start_if_enabled()


def stop():
    service.stop()


def reconfigure():
    return service.reconfigure()


def status(refresh: bool = False):
    return service.status(refresh=refresh)


def discover_now(
    wait_sec: float = 4.5,
    auto_enable: bool = True,
    target_ports=(),
    mode: str = "auto",
    interfaces: list[dict] | None = None,
):
    return service.discover_now(
        wait_sec=wait_sec,
        auto_enable=auto_enable,
        target_ports=target_ports,
        mode=mode,
        interfaces=interfaces,
    )


def summary(lan_after: int = 0):
    return service.summary(lan_after=lan_after)


def create_pin():
    return service.create_pin()


def pair_with(ip: str, port: int, pin: str):
    return service.pair_with(ip, port, pin)


def delete_peer(device_id: str):
    return service.delete_peer(device_id)


def set_peer_live_cfg(device_id: str, patch: dict):
    return service.set_peer_live_cfg(device_id, patch)


def send_cards(peer_ids: list[str], object_ids=None, liked_only: bool = False):
    return service.send_cards(peer_ids, object_ids=object_ids, liked_only=liked_only)


def send_favorites(peer_ids: list[str]):
    return service.send_favorites(peer_ids)


def send_db(peer_id: str):
    return service.send_db(peer_id)


def send_spotlight(object_id: str, peer_ids: list[str] | None = None):
    return service.send_spotlight(object_id, peer_ids=peer_ids)


def import_spotlight_card(from_device_id: str, source: str, source_id: str):
    return service.import_spotlight_card(from_device_id, source, source_id)


def pull_db(peer_id: str):
    return service.pull_db(peer_id)


def get_marks_info(peer_id: str | None = None):
    return service.get_marks_info(peer_id=peer_id)


def pull_marks(peer_id: str, items, mode: str = "merge"):
    return service.pull_marks(peer_id, items=items, mode=mode)


def send_marks(peer_ids: list[str], items):
    return service.send_marks(peer_ids, items=items)


def apply_live_favorite(keys: list, on: bool, from_device_id: str = "", import_missing: bool = True):
    return service.apply_live_favorite(keys, on, from_device_id=from_device_id, import_missing=import_missing)


def on_local_favorite(keys: list[tuple[str, str]], on: bool):
    service.on_local_favorite(keys, on)


def on_local_note(keys: list[tuple[str, str]], text: str, ts: str):
    service.on_local_note(keys, text, ts)


def on_local_merge(action: str, primary_keys=None, secondary_keys=None, keys=None):
    service.on_local_merge(action, primary_keys=primary_keys, secondary_keys=secondary_keys, keys=keys)


def flush_outbox():
    return service.flush_outbox()


def accept_inbox(req_id: str, opts: dict | None = None):
    return service.accept_inbox(req_id, opts)


def reject_inbox(req_id: str):
    return service.reject_inbox(req_id)


def list_history(limit: int = 100):
    return service.list_history(limit=limit)


def mark_history_seen(ids=None):
    return service.mark_history_seen(ids=ids)


def clear_history():
    return service.clear_history()


def diagnose_network(peer_ip: str = "", peer_port: int | None = None, probe_wait_sec: float | None = None):
    return service.diagnose_network(peer_ip=peer_ip, peer_port=peer_port, probe_wait_sec=probe_wait_sec)


def allow_firewall(remove_blocking: bool = False):
    return service.allow_firewall(remove_blocking=remove_blocking)


def open_network_settings():
    return service.open_network_settings()


def reset_runtime_cache():
    service.reset_runtime_cache()
