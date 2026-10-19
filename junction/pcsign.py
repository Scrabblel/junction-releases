"""The "pc_sign" EA asks for when the EA app signs in (see ea_account.py).

It's a small signed note about this PC: the BIOS, motherboard and disk serial numbers, the graphics card's
device id, the network card's address and a hash of a few more details, with the time. The EA app sends
the same. The signing keys are the EA app's, as the GOG Galaxy EA plugin uses them (galaxy-integration-ead,
pcsign_hash.py, which this follows). It's only sent to EA, only when you sign in to EA, and isn't kept.
"""
import base64
import datetime
import hashlib
import hmac
import json
import random
import re
import subprocess
import uuid

KEYS = {"v1": b"ISa3dpGOc8wW7Adn4auACSQmaccrOyR2", "v2": b"nt5FfJbdPzNcl2pkC3zgjO43Knvscxft"}

# One PowerShell run that reads what's needed from Windows' hardware inventory (WMI), as JSON.
_PS = r"""
$b = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$bi = Get-CimInstance Win32_BIOS | Select-Object -First 1
$os = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1
$d = Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 } | Select-Object -First 1
$g = Get-CimInstance Win32_VideoController | Where-Object { $_.PNPDeviceID -like 'PCI\*' } | Select-Object -First 1
[pscustomobject]@{
  mbm = "$($b.Manufacturer)"; msn = "$($b.SerialNumber)"; bbm = "$($bi.Manufacturer)"; bsn = "$($bi.SerialNumber)"
  osn = "$($os.SerialNumber)"; osi = $os.InstallDate.ToUniversalTime().ToString('yyyy-MM-ddHH:mm:ss') + '.000000000+0000'
  hsn = "$($d.SerialNumber)".Trim(); gpu = "$($g.PNPDeviceID)"
} | ConvertTo-Json -Compress
"""


def _fnv1a(data):
    h = 0xcbf29ce484222325
    for byte in data:
        h = ((h ^ byte) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def _mac():
    """The network card's address as '$<hex>', or None when Windows only gave Python a made-up one."""
    raw = uuid.getnode()
    return None if (raw >> 40) & 1 else f"${raw:012x}"


def hardware():
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], capture_output=True, text=True,
                         encoding="utf-8", errors="ignore", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        hw = json.loads(out.stdout.strip() or "{}")
    except ValueError:
        hw = {}
    return {k: (str(v).strip() if v is not None else "") for k, v in hw.items()}


def make(hw=None, now=None, version=None):
    """A fresh pc_sign: base64url(JSON) + "." + base64url(HMAC-SHA256 of that, with the EA app's key)."""
    hw = hw if hw is not None else hardware()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    version = version or random.choice(list(KEYS))
    mac = _mac()
    dev = re.search(r"DEV_([0-9A-Fa-f]+)", hw.get("gpu") or "")
    parts = [hw.get("mbm") or "Microsoft Corporation", hw.get("msn") or "None", hw.get("bbm") or "Microsoft Corporation",
             hw.get("bsn") or "None", hw.get("osi") or "1970-01-0100:00:00.000000000+0000", hw.get("osn") or "None"]
    note = {
        "av": "v1", "bsn": hw.get("bsn") or "None", "gid": int(dev.group(1), 16) if dev else 0,
        "hsn": hw.get("hsn") or "None", "mid": str(_fnv1a("".join(parts + ([mac] if mac else [])).encode("utf-8"))),
        "msn": hw.get("msn") or "None", "sv": version,
        "ts": f"{now:%Y-%m-%d %H:%M:%S}:{now.microsecond // 1000:03d}",
    }
    if mac:
        note["mac"] = mac
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")  # noqa: E731
    payload = b64(json.dumps(note, separators=(",", ":")).encode())
    return f"{payload}.{b64(hmac.new(KEYS[version], payload.encode('ascii'), hashlib.sha256).digest())}"
