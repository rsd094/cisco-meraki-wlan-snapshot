#!/usr/bin/env python3
"""Cisco Meraki WLAN Snapshot tool.

Take a snapshot whenever you want a record of how the wireless network is
doing (for example before an install). Each snapshot saves a data file and a
report. Open any report and load another snapshot file to compare the two.

    export MERAKI_API_KEY=...            # read-only key is enough
    python3 wlan_snapshot.py list
    python3 wlan_snapshot.py snapshot --network "Ontario" --label pre-install
    ... later ...
    python3 wlan_snapshot.py snapshot --network "Ontario" --label post-install
    python3 wlan_snapshot.py report snapshots/Ontario-*.json   # one report holding all of them

Only GET calls are made. Python 3.8+, standard library only.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.meraki.com/api/v1"
VERSION = "1.0"
MAX_RADIO_DAYS = 14          # networkHealth/channelUtilization keeps 10-min data for 14 days
BANDS = ["2.4", "5", "6"]
EVENT_TYPES = {
    "disassociation": "Disassociation",
    "wpa_deauth": "WPA deauthentication",
    "8021x_deauth": "802.1X deauthentication",
    "8021x_eap_failure": "802.1X EAP failure",
    "8021x_radius_timeout": "802.1X RADIUS timeout",
    "8021x_client_timeout": "802.1X client timeout",
    "aps_association_reject": "Association rejected (client balancing)",
    "11r_fast_roam": "802.11r fast roam",
    "8021x_eap_success": "802.1X success",
}
DOT1X_FAIL = ["8021x_eap_failure", "8021x_radius_timeout", "8021x_client_timeout"]
DISCONNECT = ["disassociation", "wpa_deauth", "8021x_deauth"]

# Lookbacks shown in the report's source notes. "tested" values were measured
# on a live network in Nov 2026; Meraki may change them.
SOURCES = {
    "inventory": ("GET /organizations/{orgId}/devices", "Current state"),
    "status": ("GET /devices/{serial}/wireless/status", "Current state at capture time"),
    "radioSettings": ("GET /devices/{serial}/wireless/radio/settings", "Current state at capture time"),
    "rfProfiles": ("GET /networks/{networkId}/wireless/rfProfiles", "Current state at capture time"),
    "rrm": ("GET /organizations/{orgId}/wireless/radio/rrm/byNetwork", "Current state at capture time"),
    "radioUtil": ("GET /networks/{networkId}/networkHealth/channelUtilization", "14 days (10-minute detail, per radio)"),
    "signal": ("GET /networks/{networkId}/wireless/signalQualityHistory", "31 days"),
    "clients": ("GET /networks/{networkId}/wireless/clientCountHistory", "31 days"),
    "usage": ("GET /networks/{networkId}/wireless/usageHistory", "31 days"),
    "connections": ("GET /networks/{networkId}/wireless/connectionStats", "180 days documented, ~65 days tested; 7 days per call"),
    "latency": ("GET /networks/{networkId}/wireless/latencyStats", "180 days; 7 days per call"),
    "packetLoss": ("GET /organizations/{orgId}/wireless/devices/packetLoss/byDevice", "90 days"),
    "events": ("GET /networks/{networkId}/events (productType=wireless)", "60+ days tested; paged 1,000 events per call"),
    "airMarshal": ("GET /networks/{networkId}/wireless/airMarshal", "31 days"),
    "apStatus": ("GET /organizations/{orgId}/devices/statuses", "Current state at capture time"),
    "clientsNow": ("GET /organizations/{orgId}/wireless/clients/overview/byDevice", "Current state at capture time"),
    "ssids": ("GET /networks/{networkId}/wireless/ssids", "Current state at capture time"),
    "radius": ("GET /networks/{networkId}/events (802.1X success and RADIUS timeout events)", "Inferred from the snapshot window"),
    "overrides": ("GET /organizations/{orgId}/wireless/radio/overrides/byDevice", "Current state at capture time"),
    "clientList": ("GET /networks/{networkId}/clients", "31 days"),
    "clientConn": ("GET /networks/{networkId}/wireless/clients/connectionStats", "180 days; 7 days per call"),
    "clientLatency": ("GET /networks/{networkId}/wireless/clients/latencyStats", "180 days; 7 days per call"),
    "clientLoss": ("GET /organizations/{orgId}/wireless/devices/packetLoss/byClient", "90 days"),
    "roaming": ("GET /networks/{networkId}/wireless/clients/{clientId}/connectivityEvents (roam, connection, sticky)", "31 days; one call per client"),
}
# Every Meraki Dashboard API call the tool makes. All are read-only GETs.
# "calls" is per snapshot; N = APs, B = bands per AP, C = wireless clients, D = days in the period.
ENDPOINTS = [
    {"area": "Finding your networks", "path": "GET /organizations", "use": "Lists the organizations your API key can see.",
     "shows": "Network picker in the app", "lookback": "Current", "calls": "1"},
    {"area": "Finding your networks", "path": "GET /organizations/{organizationId}/networks", "use": "Lists each organization's networks; the app keeps the ones with wireless.",
     "shows": "Network picker in the app", "lookback": "Current", "calls": "1 per organization"},
    {"area": "Access points", "path": "GET /organizations/{organizationId}/devices", "use": "The network's access points: name, model, serial, firmware.",
     "shows": "Access points tile, RF configuration", "lookback": "Current", "calls": "1"},
    {"area": "Access points", "path": "GET /organizations/{organizationId}/devices/statuses", "use": "Online, alerting or offline; last time each AP reported; LAN IP.",
     "shows": "Access points tile and list", "lookback": "Current", "calls": "1"},
    {"area": "Access points", "path": "GET /organizations/{organizationId}/wireless/clients/overview/byDevice", "use": "Clients connected to each AP right now.",
     "shows": "Access points tile and list", "lookback": "Current", "calls": "1"},
    {"area": "RF configuration", "path": "GET /devices/{serial}/wireless/status", "use": "Each radio's band, channel, channel width, transmit power and the SSIDs it broadcasts.",
     "shows": "RF configuration; also works out which radio is which band", "lookback": "Current", "calls": "N"},
    {"area": "RF configuration", "path": "GET /devices/{serial}/wireless/radio/settings", "use": "Which RF profile each AP uses and any manual channel or power overrides.",
     "shows": "RF configuration", "lookback": "Current", "calls": "N"},
    {"area": "RF configuration", "path": "GET /networks/{networkId}/wireless/rfProfiles", "use": "Band steering, minimum bitrates, channel widths and flex-radio settings.",
     "shows": "RF configuration", "lookback": "Current", "calls": "1"},
    {"area": "RF configuration", "path": "GET /organizations/{organizationId}/wireless/radio/rrm/byNetwork", "use": "AutoRF settings: whether AI-RRM is on and when it was enabled, FRA, busy-hour behaviour, channel avoidance.",
     "shows": "AI-RRM tile, RF configuration", "lookback": "Current", "calls": "1"},
    {"area": "RF configuration", "path": "GET /organizations/{organizationId}/wireless/radio/overrides/byDevice", "use": "Radios with a manually fixed channel, power or channel width. These are outside AI-RRM's control even when it's on.",
     "shows": "AI-RRM tile", "lookback": "Current", "calls": "1+ (pages of 100)"},
    {"area": "RF configuration", "path": "GET /networks/{networkId}/wireless/airMarshal", "use": "Other networks your APs hear, with signal strength.",
     "shows": "Strong neighbouring networks", "lookback": "31 days (last 7 days used)", "calls": "1"},
    {"area": "RF configuration", "path": "GET /networks/{networkId}/wireless/ssids", "use": "Enabled SSIDs, their security mode and RADIUS servers.",
     "shows": "RADIUS servers tile", "lookback": "Current", "calls": "1"},
    {"area": "Radio utilization", "path": "GET /networks/{networkId}/networkHealth/channelUtilization", "use": "How busy every radio is, in 10-minute samples, including non-Wi-Fi interference. Each 5 GHz radio on dual-5 GHz APs separately.",
     "shows": "Busiest radio tile, radio utilization, scorecard", "lookback": "14 days (Meraki refuses older 10-minute data)", "calls": "2 (7-day pages)"},
    {"area": "Clients and traffic", "path": "GET /networks/{networkId}/wireless/clientCountHistory", "use": "Clients per day, for the whole network and for each band.",
     "shows": "Clients per day, band share", "lookback": "31 days", "calls": "4"},
    {"area": "Clients and traffic", "path": "GET /networks/{networkId}/wireless/usageHistory", "use": "Traffic per day for each AP and band.",
     "shows": "Traffic per day, by band", "lookback": "31 days", "calls": "N × B"},
    {"area": "Clients and traffic", "path": "GET /networks/{networkId}/wireless/signalQualityHistory", "use": "Client signal-to-noise (SNR) per day for each AP and band.",
     "shows": "Weakest client signal, by band", "lookback": "31 days", "calls": "N × B"},
    {"area": "Clients and traffic", "path": "GET /networks/{networkId}/clients", "use": "Every client: name, MAC, maker, OS, SSID, last AP, data used, and the client ID needed for roaming.",
     "shows": "Client lists in the detail panels", "lookback": "31 days", "calls": "1+ (pages of 1,000)"},
    {"area": "Connection quality", "path": "GET /networks/{networkId}/wireless/connectionStats", "use": "Successful joins and failures at association, authentication, DHCP and DNS; whole network, each band, and each day.",
     "shows": "Connection success, auth failure rate, failed joins by stage", "lookback": "180 days documented, about 65 days in practice; 7 days per call", "calls": "8 + D"},
    {"area": "Connection quality", "path": "GET /networks/{networkId}/wireless/clients/connectionStats", "use": "The same join results for each client.",
     "shows": "Connection success and auth detail panels", "lookback": "180 days; 7 days per call", "calls": "2"},
    {"area": "Connection quality", "path": "GET /networks/{networkId}/wireless/latencyStats", "use": "Wireless latency per traffic class (voice, video, best effort, background), whole network and each band.",
     "shows": "Latency, slow voice packets", "lookback": "180 days; 7 days per call", "calls": "8"},
    {"area": "Connection quality", "path": "GET /networks/{networkId}/wireless/clients/latencyStats", "use": "The same latency figures for each client.",
     "shows": "Latency and voice detail panels", "lookback": "180 days; 7 days per call", "calls": "2"},
    {"area": "Connection quality", "path": "GET /organizations/{organizationId}/wireless/devices/packetLoss/byDevice", "use": "Upstream and downstream packet loss per AP, whole network and each band.",
     "shows": "Upstream packet loss, by band", "lookback": "90 days", "calls": "4"},
    {"area": "Connection quality", "path": "GET /organizations/{organizationId}/wireless/devices/packetLoss/byClient", "use": "Packet loss per client.",
     "shows": "Packet loss detail panel", "lookback": "90 days", "calls": "1"},
    {"area": "Client events", "path": "GET /networks/{networkId}/events", "use": "Wireless event log (productType=wireless): disassociations, WPA and 802.1X deauthentications, 802.1X failures and RADIUS timeouts, 802.1X successes, fast roams.",
     "shows": "Disconnects, 802.1X failures, RADIUS reachability, client issues", "lookback": "60+ days in practice", "calls": "1 per 1,000 events"},
    {"area": "Roaming", "path": "GET /networks/{networkId}/wireless/clients/{clientId}/connectivityEvents", "use": "Each client's roam, connection and sticky events: the AP it left and joined, roam time, signal before and after.",
     "shows": "Roaming tile, scorecard rows and section", "lookback": "31 days", "calls": "C (up to 300 busiest clients)"},
]

MAX_ROAM_CLIENTS = 300   # per-client calls; busiest clients first on large sites
BAD_ROAM_MS, SUB_ROAM_MS = 3000, 1000
PING_PONG_SECONDS = 120
PING_PONG_CLIENT_MIN = 3     # bounces before a client counts as ping-ponging
GAP_SECONDS = 10             # off the network this long between APs makes a roam suboptimal


def _ssl_context():
    import ssl
    try:   # python.org builds on macOS ship without CA certs; use certifi when it's there
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


# ---------------------------------------------------------------- API client
class Meraki:
    def __init__(self, key, verbose=True):
        self.key = key
        self.verbose = verbose
        self.calls = []

    def get(self, path, params=None, allow_fail=False):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(_flatten(params), safe="[]")
        return self._get_url(url, path, allow_fail)[0]

    MAX_RPS = 8   # stay under Meraki's 10 requests per second per organization

    def _throttle(self):
        wait = getattr(self, "_last", 0) + 1.0 / self.MAX_RPS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _get_url(self, url, label, allow_fail=False):
        last = "too many retries (rate limited or server errors)"
        for attempt in range(6):
            self._throttle()
            # The only request this tool ever sends to Meraki: a GET. Nothing here can change the network.
            req = urllib.request.Request(url, method="GET", headers={
                "Authorization": "Bearer " + self.key,
                "Accept": "application/json",
                "User-Agent": "wlan-snapshot/" + VERSION,
            })
            try:
                with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                    body = r.read()
                    self.calls.append({"path": label, "status": r.status})
                    return (json.loads(body) if body else None), r.headers
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = float(e.headers.get("Retry-After") or 2 ** attempt)
                    time.sleep(min(wait, 30))
                    continue
                msg = e.read().decode("utf-8", "replace")[:300]
                self.calls.append({"path": label, "status": e.code, "error": msg})
                if allow_fail:
                    if self.verbose:
                        print("   ! %s -> %s %s" % (label, e.code, msg), file=sys.stderr)
                    return None, None
                raise SystemExit("API error %s on %s: %s" % (e.code, label, msg))
            except urllib.error.URLError as e:
                time.sleep(2 ** attempt)
                last = e
        msg = str(getattr(last, "reason", last))
        if "CERTIFICATE_VERIFY_FAILED" in msg:
            msg += " (on macOS, run 'Install Certificates.command' from your Python folder)"
        raise SystemExit("Could not reach the Meraki API: %s" % msg)

    def get_all(self, path, params=None):
        """Follow Link rel=next pagination."""
        url = API + path + ("?" + urllib.parse.urlencode(_flatten(params), safe="[]") if params else "")
        out = []
        while url:
            data, headers = self._get_url(url, path)
            out.extend(data.get("items", data) if isinstance(data, dict) else data)
            url = None
            for part in (headers.get("Link") or "").split(","):
                m = re.search(r"<([^>]+)>;\s*rel=next", part)
                if m:
                    url = m.group(1)
        return out


def _flatten(params):
    out = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out.extend((k + "[]", str(x)) for x in v)
        else:
            out.append((k, str(v)))
    return out


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def chunks(t0, t1, days):
    cur = t0
    while cur < t1:
        nxt = min(cur + dt.timedelta(days=days), t1)
        yield cur, nxt
        cur = nxt


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def pct(v, p):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def rnd(x, n=1):
    return None if x is None else round(x, n)


LOG_HOOK = None   # the local app sets this to show progress in the browser


def log(msg):
    print(msg, file=sys.stderr)
    if LOG_HOOK:
        LOG_HOOK(msg)


# ---------------------------------------------------------------- discovery
def find_network(m, name_or_id, org_id=None):
    orgs = m.get("/organizations")
    if org_id:
        orgs = [o for o in orgs if o["id"] == org_id]
    hits = []
    for o in orgs:
        for n in m.get_all("/organizations/%s/networks" % o["id"], {"perPage": 1000}):
            if n["id"] == name_or_id or n["name"].lower() == name_or_id.lower():
                hits.append((o, n))
    if not hits:
        raise SystemExit("No network named or with id '%s'. Run 'list' to see them." % name_or_id)
    if len(hits) > 1:
        raise SystemExit("Several networks match '%s'; pass --org." % name_or_id)
    return hits[0]


def cmd_list(m, args):
    for o in m.get("/organizations"):
        print("%s  %s" % (o["id"], o["name"]))
        for n in m.get_all("/organizations/%s/networks" % o["id"], {"perPage": 1000}):
            if "wireless" in n.get("productTypes", []):
                print("    %s  %s" % (n["id"], n["name"]))


# ---------------------------------------------------------------- snapshot
def radio_map(status):
    """Map wifi0/1/2 to (band, channel, width, power) using BSS order from wireless/status.

    The first enabled BSS of each distinct (band, channel) in listing order is
    taken as radio 0, 1, 2. This matched live data on CW9166I/CW9176I APs.
    """
    radios, seen = [], set()
    for b in (status or {}).get("basicServiceSets", []):
        key = (b.get("band"), b.get("channel"))
        if key in seen or b.get("channel") is None:
            continue
        seen.add(key)
        radios.append({
            "band": (b.get("band") or "").replace(" GHz", ""),
            "channel": b.get("channel"),
            "width": b.get("channelWidth"),
            "power": b.get("power"),
            "ssids": [],
        })
    for b in (status or {}).get("basicServiceSets", []):
        for r in radios:
            if (r["band"], r["channel"]) == ((b.get("band") or "").replace(" GHz", ""), b.get("channel")):
                if b.get("enabled") and b.get("broadcasting") and b.get("ssidName") not in r["ssids"]:
                    r["ssids"].append(b.get("ssidName"))
    return radios


def snapshot(m, args):
    org, net = find_network(m, args.network, args.org)
    oid, nid = org["id"], net["id"]
    t1 = parse_ts(args.end) if args.end else dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    t0 = t1 - dt.timedelta(days=args.days)
    now = dt.datetime.now(dt.timezone.utc)
    # Meraki refuses 10-minute radio data that starts more than 14 days ago; start 15 minutes inside that limit.
    radio_t0 = max(t0, now - dt.timedelta(days=MAX_RADIO_DAYS) + dt.timedelta(minutes=15))
    radio_ok = radio_t0 < t1
    log("Snapshot of %s / %s, %s to %s" % (org["name"], net["name"], iso(t0), iso(t1)))

    snap = {
        "tool": "wlan-snapshot", "version": VERSION, "label": args.label,
        "capturedAt": iso(now), "window": {"t0": iso(t0), "t1": iso(t1), "days": args.days},
        "org": {"id": oid, "name": org["name"]},
        "network": {"id": nid, "name": net["name"], "timeZone": net.get("timeZone")},
        "sources": SOURCES, "notes": [],
    }
    if args.end:
        snap["notes"].append("Backdated window: radio settings, RF profile and AutoRF are as of capture time, not the window.")

    log(" - access points")
    aps = [d for d in m.get_all("/organizations/%s/devices" % oid, {"networkIds": [nid], "productTypes": ["wireless"], "perPage": 1000})]
    snap["aps"] = []
    for d in aps:
        st = m.get("/devices/%s/wireless/status" % d["serial"], allow_fail=True)
        rs = m.get("/devices/%s/wireless/radio/settings" % d["serial"], allow_fail=True)
        snap["aps"].append({
            "serial": d["serial"], "name": d.get("name") or d["serial"], "model": d.get("model"),
            "firmware": d.get("firmware"), "radios": radio_map(st),
            "rfProfileId": (rs or {}).get("rfProfileId"),
            "overrides": {k: v for k, v in (rs or {}).items() if k.endswith("Settings")},
        })
    serials = [a["serial"] for a in snap["aps"]]
    if not serials:
        raise SystemExit("No wireless devices in %s." % net["name"])

    log(" - AP status and SSIDs")
    st = m.get_all("/organizations/%s/devices/statuses" % oid, {"networkIds": [nid], "productTypes": ["wireless"], "perPage": 1000})
    stmap = {x["serial"]: x for x in st}
    cn = m.get("/organizations/%s/wireless/clients/overview/byDevice" % oid, {"networkIds": [nid], "perPage": 1000}, allow_fail=True) or {}
    cnmap = {x["serial"]: (x.get("counts") or {}).get("byStatus", {}).get("online") for x in (cn.get("items", cn) if isinstance(cn, dict) else cn)}
    for a in snap["aps"]:
        x = stmap.get(a["serial"], {})
        a.update({"status": x.get("status"), "lastReportedAt": x.get("lastReportedAt"), "lanIp": x.get("lanIp"),
                  "clientsNow": cnmap.get(a["serial"])})
    ss = m.get("/networks/%s/wireless/ssids" % nid, allow_fail=True) or []
    snap["ssids"] = [{"number": x["number"], "name": x["name"], "authMode": x.get("authMode"),
                      "radiusServers": ["%s:%s" % (r.get("host"), r.get("port")) for r in (x.get("radiusServers") or [])],
                      "radiusTesting": x.get("radiusTestingEnabled")}
                     for x in ss if x.get("enabled")]

    log(" - RF profiles and AutoRF")
    profiles = m.get("/networks/%s/wireless/rfProfiles" % nid, allow_fail=True) or []
    snap["rfProfiles"] = [_profile_summary(p) for p in profiles]
    rrm = m.get("/organizations/%s/wireless/radio/rrm/byNetwork" % oid, {"networkIds": [nid]}, allow_fail=True)
    snap["autoRf"] = ((rrm or {}).get("items") or [None])[0]
    try:   # radios with a fixed channel, power or width are outside AutoRF and AI-RRM
        ov = m.get_all("/organizations/%s/wireless/radio/overrides/byDevice" % oid, {"perPage": 100})
    except SystemExit:
        ov = None
    names = {a["serial"]: a["name"] for a in snap["aps"]}
    snap["radioOverrides"] = [] if ov is not None else None
    for x in ov or []:
        if x.get("serial") not in names:
            continue
        fixed = [{"band": r.get("band"), "channel": r.get("channel"), "power": r.get("targetPower"), "width": r.get("channelWidth")}
                 for r in x.get("radios") or [] if r.get("channel") is not None or r.get("targetPower") is not None or r.get("channelWidth") is not None]
        if fixed:
            snap["radioOverrides"].append({"serial": x["serial"], "ap": names[x["serial"]], "radios": fixed})

    log(" - per-radio utilization (10-minute)")
    snap["radioUtil"] = {}
    if radio_ok:
        rows = []
        for c0, c1 in chunks(radio_t0, t1, 7):
            rows.append(m.get_all("/networks/%s/networkHealth/channelUtilization" % nid,
                                  {"t0": iso(c0), "t1": iso(c1), "resolution": 600, "perPage": 100}))
        snap["radioUtil"] = _radio_util(rows, snap["aps"], net.get("timeZone"))
    else:
        snap["notes"].append("Per-radio utilization skipped: the window ended more than 14 days ago.")

    res = 86400
    log(" - clients per band")
    snap["clients"] = {"all": _series(m.get("/networks/%s/wireless/clientCountHistory" % nid,
                                            {"t0": iso(t0), "t1": iso(t1), "resolution": res}, allow_fail=True), "clientCount")}
    for b in BANDS:
        snap["clients"][b] = _series(m.get("/networks/%s/wireless/clientCountHistory" % nid,
                                           {"t0": iso(t0), "t1": iso(t1), "resolution": res, "band": b}, allow_fail=True), "clientCount")

    log(" - signal and traffic per AP per band")
    snap["signal"], snap["usage"] = {}, {}
    for s in serials:
        snap["signal"][s], snap["usage"][s] = {}, {}
        bands = sorted({r["band"] for a in snap["aps"] if a["serial"] == s for r in a["radios"]}) or BANDS
        for b in bands:
            sq = m.get("/networks/%s/wireless/signalQualityHistory" % nid,
                       {"t0": iso(t0), "t1": iso(t1), "resolution": res, "deviceSerial": s, "band": b}, allow_fail=True)
            snap["signal"][s][b] = {"snr": _series(sq, "snr"), "rssi": _series(sq, "rssi")}
            us = m.get("/networks/%s/wireless/usageHistory" % nid,
                       {"t0": iso(t0), "t1": iso(t1), "resolution": res, "deviceSerial": s, "band": b}, allow_fail=True)
            snap["usage"][s][b] = _series(us, "totalKbps")

    log(" - connection results and latency per band")
    snap["connections"], snap["latency"] = {}, {}
    for b in [None] + BANDS:
        key = b or "all"
        tot = {"assoc": 0, "auth": 0, "dhcp": 0, "dns": 0, "success": 0}
        dist, wavg = {}, {}
        for c0, c1 in chunks(t0, t1, 7):
            p = {"t0": iso(c0), "t1": iso(c1)}
            if b:
                p["band"] = b
            cs = m.get("/networks/%s/wireless/connectionStats" % nid, p, allow_fail=True) or {}
            for k in tot:
                tot[k] += cs.get(k) or 0
            ls = m.get("/networks/%s/wireless/latencyStats" % nid, p, allow_fail=True) or {}
            for ac, v in ls.items():
                d = dist.setdefault(ac, {})
                raw = (v or {}).get("rawDistribution") or {}
                for bucket, n in raw.items():
                    d[bucket] = d.get(bucket, 0) + n
                if (v or {}).get("avg") is not None:
                    w = wavg.setdefault(ac, [0.0, 0])
                    n = sum(raw.values()) or 1
                    w[0] += v["avg"] * n
                    w[1] += n
        snap["connections"][key] = tot
        snap["latency"][key] = {}
        for ac, d in dist.items():
            lat = _latency(d)
            if ac in wavg and wavg[ac][1]:
                lat["avg"] = round(wavg[ac][0] / wavg[ac][1], 2)   # Meraki's own average, weighted by samples
            snap["latency"][key][ac] = lat

    log(" - daily connection results")
    snap["connectionsDaily"] = []
    for c0, c1 in chunks(t0, t1, 1):
        cs = m.get("/networks/%s/wireless/connectionStats" % nid, {"t0": iso(c0), "t1": iso(c1)}, allow_fail=True) or {}
        snap["connectionsDaily"].append(dict(cs, day=iso(c0)[:10]))

    log(" - per-client detail")
    cl = m.get_all("/networks/%s/clients" % nid, {"t0": iso(max(t0, now - dt.timedelta(days=31) + dt.timedelta(minutes=15))),
                                                  "perPage": 1000})
    info = {}
    for x in cl:
        if x.get("recentDeviceConnection") != "Wireless" or not x.get("mac"):
            continue
        u = x.get("usage") or {}
        info[x["mac"].lower()] = {"id": x.get("id"), "name": x.get("description") or x.get("dhcpHostname") or x.get("mac"), "manufacturer": x.get("manufacturer"),
                                  "os": x.get("os"), "ssid": x.get("ssid"), "ap": x.get("recentDeviceName"),
                                  "user": x.get("user"), "usageMb": rnd(((u.get("total") or 0) / 1024), 1)}
    conn, lat = {}, {}
    for c0, c1 in chunks(t0, t1, 7):
        for x in m.get("/networks/%s/wireless/clients/connectionStats" % nid, {"t0": iso(c0), "t1": iso(c1)}, allow_fail=True) or []:
            d = conn.setdefault(x["mac"].lower(), {"assoc": 0, "auth": 0, "dhcp": 0, "dns": 0, "success": 0})
            for k in d:
                d[k] += (x.get("connectionStats") or {}).get(k) or 0
        for x in m.get("/networks/%s/wireless/clients/latencyStats" % nid, {"t0": iso(c0), "t1": iso(c1)}, allow_fail=True) or []:
            d = lat.setdefault(x["mac"].lower(), {})
            for ac, v in (x.get("latencyStats") or {}).items():
                a = d.setdefault(ac, {"dist": {}, "w": 0.0, "n": 0})
                raw = (v or {}).get("rawDistribution") or {}
                for b, n in raw.items():
                    a["dist"][b] = a["dist"].get(b, 0) + n
                if (v or {}).get("avg") is not None and sum(raw.values()):
                    a["w"] += v["avg"] * sum(raw.values())
                    a["n"] += sum(raw.values())
    lat_out = {}
    for mac, acs in lat.items():
        o = {}
        for ac, a in acs.items():
            l = _latency(a["dist"])
            l["avg"] = round(a["w"] / a["n"], 2) if a["n"] else None
            o[ac] = {"avg": l["avg"], "over32Pct": l["over32Pct"], "samples": l["samples"]}
        lat_out[mac] = o
    pl = m.get("/organizations/%s/wireless/devices/packetLoss/byClient" % oid,
               {"networkIds": [nid], "t0": iso(t0), "t1": iso(t1), "perPage": 1000}, allow_fail=True) or []
    loss = {}
    for x in (pl.get("items", pl) if isinstance(pl, dict) else pl):
        mac = ((x.get("client") or {}).get("mac") or "").lower()
        if mac:
            loss[mac] = {"up": x.get("upstream"), "down": x.get("downstream")}
    snap["clientDetail"] = {"info": info, "conn": conn, "latency": lat_out, "loss": loss}

    log(" - roaming (one call per client)")
    ranked = sorted(info.items(), key=lambda kv: -(kv[1].get("usageMb") or 0))
    roam_clients = [(mac, i) for mac, i in ranked if i.get("id")][:MAX_ROAM_CLIENTS]
    roams, sticky, skipped = {}, {}, max(0, len([1 for _, i in ranked if i.get("id")]) - MAX_ROAM_CLIENTS)
    for n, (mac, i) in enumerate(roam_clients, 1):
        if n % 25 == 0:
            log("   %d of %d clients" % (n, len(roam_clients)))
        try:
            evs = m.get_all("/networks/%s/wireless/clients/%s/connectivityEvents" % (nid, i["id"]),
                            {"t0": iso(t0), "t1": iso(t1), "perPage": 1000, "types": ["roam", "connection", "sticky"]})
        except SystemExit:
            continue
        r, st = _roams(evs)
        if r:
            roams[mac] = r
        if st:
            sticky[mac] = st
    snap["roaming"] = {"roams": roams, "sticky": sticky, "clientsChecked": len(roam_clients), "clientsSkipped": skipped,
                       "rules": {"badMs": BAD_ROAM_MS, "subMs": SUB_ROAM_MS, "pingPongSeconds": PING_PONG_SECONDS,
                                 "pingPongClientMin": PING_PONG_CLIENT_MIN, "gapSeconds": GAP_SECONDS}}
    if skipped:
        snap["notes"].append("Roaming was checked for the %d busiest clients; %d others were skipped." % (len(roam_clients), skipped))

    log(" - packet loss per AP per band")
    snap["packetLoss"] = {}
    for b in [None] + BANDS:
        p = {"networkIds": [nid], "t0": iso(t0), "t1": iso(t1), "perPage": 1000}
        if b:
            p["bands"] = [b]
        pl = m.get("/organizations/%s/wireless/devices/packetLoss/byDevice" % oid, p, allow_fail=True) or []
        snap["packetLoss"][b or "all"] = {i["device"]["serial"]: {"up": i.get("upstream"), "down": i.get("downstream")}
                                          for i in (pl.get("items", pl) if isinstance(pl, dict) else pl)}

    log(" - client events (disassociation, deauth, 802.1X)")
    snap["events"] = _events(m, nid, t0, t1, snap["aps"])

    log(" - neighbouring networks (Air Marshal)")
    am = m.get("/networks/%s/wireless/airMarshal" % nid,
               {"t0": iso(max(t0, t1 - dt.timedelta(days=7)))},
               allow_fail=True) or []
    snap["neighbors"] = _neighbors(am, serials)

    snap["apiCalls"] = len(m.calls)
    snap["apiErrors"] = [c for c in m.calls if c.get("status", 200) >= 400]
    os.makedirs(args.out, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", net["name"]).strip("-")
    path = os.path.join(args.out, "%s-%s-%s.json" % (safe, args.label, t1.strftime("%Y%m%d-%H%M")))
    snap["summary"] = summarize(snap)
    with open(path, "w") as f:
        json.dump(snap, f, separators=(",", ":"))
    log("Saved %s (%d API calls, %d refused)" % (path, len(m.calls), len(snap["apiErrors"])))
    print(path)
    if not args.no_report:
        html = path[:-5] + ".html"
        build_report([snap], html)
        print(html)


def _profile_summary(p):
    out = {"id": p.get("id"), "name": p.get("name"),
           "bandSelection": p.get("bandSelectionType"),
           "bandSteering": ((p.get("apBandSettings") or {}).get("bandSteeringEnabled")),
           "bandsEnabled": (((p.get("apBandSettings") or {}).get("bands") or {}).get("enabled")),
           "clientBalancing": p.get("clientBalancingEnabled"),
           "flexRadios": {x["model"]: x["bands"] for x in ((p.get("flexRadios") or {}).get("byModel") or [])}}
    for k, b in (("twoFourGhzSettings", "2.4"), ("fiveGhzSettings", "5"), ("sixGhzSettings", "6")):
        s = p.get(k) or {}
        out[b] = {"minBitrate": s.get("minBitrate"), "maxPower": s.get("maxPower"), "minPower": s.get("minPower"),
                  "channelWidth": s.get("channelWidth"), "channels": len(s.get("validAutoChannels") or [])}
    return out


def _series(resp, field):
    if not resp:
        return []
    items = resp.get("items", resp) if isinstance(resp, dict) else resp
    return [{"t": i.get("startTs"), "v": i.get(field)} for i in items if i.get(field) is not None]


def _latency(dist):
    """p95 latency (ms) from Meraki's power-of-two histogram; avg is replaced by Meraki's own average."""
    pts = sorted((int(k), n) for k, n in dist.items() if n)
    total = sum(n for _, n in pts)
    if not total:
        return {"avg": None, "p95": None, "over32Pct": None, "samples": 0}
    mid = lambda k: (k * 1.5) if k else 0.5
    avg = sum(mid(k) * n for k, n in pts) / total
    run, p95 = 0, None
    for k, n in pts:
        run += n
        if run >= 0.95 * total:
            p95 = max(k * 2, 1)
            break
    slow = sum(n for k, n in pts if k >= 32)
    return {"avg": round(avg, 2), "p95": p95, "over32Pct": round(100 * slow / total, 1), "samples": total}


def _radio_util(chunks_rows, aps, tz):
    by = {}
    for rows in chunks_rows:
        for it in rows:
            for rk, pts in it.items():
                if not rk.startswith("wifi") or not isinstance(pts, list):
                    continue
                d = by.setdefault((it["serial"], rk), {})
                for p in pts:
                    d[p["start_ts"]] = (p.get("utilization"), p.get("wifi"), p.get("non_wifi"))
    out = {}
    names = {a["serial"]: a for a in aps}
    for (serial, rk), d in by.items():
        idx = int(rk[4:])
        ap = names.get(serial, {})
        radio = (ap.get("radios") or [])[idx] if idx < len(ap.get("radios") or []) else {}
        ts = sorted(d)
        util = [d[t][0] for t in ts]
        nonw = [d[t][2] for t in ts]
        hourly, daily = {}, {}
        for t in ts:
            hourly.setdefault(t[:13], []).append(d[t][0])
            daily.setdefault(t[:10], []).append(d[t][0])
        # busiest hour of day, averaged across days (UTC hour)
        hod = {}
        for t in ts:
            hod.setdefault(int(t[11:13]), []).append(d[t][0])
        out["%s|%s" % (serial, rk)] = {
            "serial": serial, "ap": ap.get("name", serial), "radio": rk,
            "band": radio.get("band"), "channel": radio.get("channel"), "width": radio.get("width"),
            "samples": len(ts), "avg": rnd(mean(util)), "p95": rnd(pct(util, .95)), "max": rnd(max(u for u in util if u is not None)) if util else None,
            "nonWifiAvg": rnd(mean(nonw)), "nonWifiP95": rnd(pct(nonw, .95)), "nonWifiMax": rnd(max(n for n in nonw if n is not None)) if nonw else None,
            "busyHourAvg": rnd(max(mean(v) for v in hod.values())) if hod else None,
            "hourly": [[h, rnd(mean(v))] for h, v in sorted(hourly.items())],
            "dailyP95": [[k, rnd(pct(v, .95))] for k, v in sorted(daily.items())],
        }
    return out


def _events(m, nid, t0, t1, aps):
    counts = {k: 0 for k in EVENT_TYPES}
    by_band, by_ap, by_day = {}, {}, {}
    radius = {}   # "ssid|ap" -> successes, timeouts, failures, last success
    client_types = {}
    clients = {}
    cursor = iso(t1)
    pages = 0
    while True:
        data = m.get("/networks/%s/events" % nid, {"productType": "wireless", "perPage": 1000,
                                                    "includedEventTypes": list(EVENT_TYPES), "endingBefore": cursor})
        evs = (data or {}).get("events") or []
        pages += 1
        stop = False
        for e in evs:
            t = parse_ts(e["occurredAt"])
            if t < t0:
                stop = True
                continue
            if t >= t1:
                continue
            ty = e["type"]
            counts[ty] = counts.get(ty, 0) + 1
            if ty.startswith("8021x_"):
                r = radius.setdefault("%s|%s" % (e.get("ssidNumber"), e.get("deviceName") or e.get("deviceSerial")),
                                      {"ssid": e.get("ssidNumber"), "ap": e.get("deviceName") or e.get("deviceSerial"),
                                       "success": 0, "timeout": 0, "failure": 0, "lastSuccess": None, "lastTimeout": None})
                if ty == "8021x_eap_success":
                    r["success"] += 1
                    r["lastSuccess"] = max(r["lastSuccess"] or "", e["occurredAt"])
                elif ty == "8021x_radius_timeout":
                    r["timeout"] += 1
                    r["lastTimeout"] = max(r["lastTimeout"] or "", e["occurredAt"])
                elif ty in ("8021x_eap_failure", "8021x_client_timeout"):
                    r["failure"] += 1
            if ty == "8021x_eap_success":
                continue
            band = str((e.get("eventData") or {}).get("band") or "unknown")
            band = {"2": "2.4", "24": "2.4"}.get(band, band)
            by_band.setdefault(band, {}).setdefault(ty, 0)
            by_band[band][ty] += 1
            ap = e.get("deviceName") or e.get("deviceSerial") or "unknown"
            by_ap.setdefault(ap, {}).setdefault(ty, 0)
            by_ap[ap][ty] += 1
            day = e["occurredAt"][:10]
            by_day.setdefault(day, {}).setdefault(ty, 0)
            by_day[day][ty] += 1
            if ty in DOT1X_FAIL or ty in DISCONNECT:
                c = (e.get("clientMac") or e.get("clientId") or "").lower()
                clients[c] = clients.get(c, 0) + 1
                ct = client_types.setdefault(c, {})
                ct[ty] = ct.get(ty, 0) + 1
        start = (data or {}).get("pageStartAt")
        if stop or not evs or not start or parse_ts(start) <= t0 or start == cursor or pages > 500:
            break
        cursor = start
    top = sorted(clients.items(), key=lambda x: -x[1])[:10]
    by_client = {c: client_types.get(c, {}) for c, _ in sorted(clients.items(), key=lambda x: -x[1])[:300]}
    return {"counts": counts, "byBand": by_band, "byAp": by_ap, "byDay": by_day, "radius": list(radius.values()), "byClient": by_client,
            "topClients": [{"client": c, "events": n} for c, n in top], "pages": pages}


def _neighbors(am, serials):
    bssids, strong = 0, 0
    ssids = set()
    top = []
    for s in am or []:
        ssids.add(s.get("ssid"))
        for b in s.get("bssids") or []:
            det = [d for d in b.get("detectedBy") or [] if d.get("device") in serials]
            if not det:
                continue
            bssids += 1
            best = max(det, key=lambda d: d.get("rssi") or 0)
            if (best.get("rssi") or 0) >= 25:
                strong += 1
            top.append({"ssid": s.get("ssid") or "(hidden)", "bssid": b.get("bssid"), "rssi": best.get("rssi"),
                        "heardBy": best.get("device"), "aps": len(det), "contained": b.get("contained")})
    top.sort(key=lambda x: -(x["rssi"] or 0))
    return {"ssids": len(ssids), "bssids": bssids, "strongBssids": strong, "top": top[:40],
            "note": "Strong = heard by one of your APs at 25 dB or more."}


def _roams(evs):
    """Turn one client's connectivity events into roams.

    A roam is a 'roam' event on the AP the client left (its SNR there, time spent there)
    followed by a 'connection' event with reassoc=1 on the new AP (SNR on arrival,
    roam time in durationMs). Meraki's API reports SNR in the 'rssi' field.
    """
    evs = sorted(evs or [], key=lambda e: e.get("occurredAt") or "")
    out, sticky, pending = [], 0, None
    for e in evs:
        ty = e.get("type")
        if ty == "sticky":
            sticky += 1
            continue
        if ty == "roam":
            if (e.get("rssi") or -1) >= 0 and e.get("subtype") != "grabber":
                pending = e
            continue
        if ty == "connection" and str((e.get("eventData") or {}).get("reassoc")) == "1" and pending:
            t_old, t_new = parse_ts(pending["occurredAt"]), parse_ts(e["occurredAt"])
            if (t_new - t_old).total_seconds() <= 60 and pending.get("deviceSerial") != e.get("deviceSerial"):
                out.append({"t": e["occurredAt"], "from": pending.get("deviceSerial"), "to": e.get("deviceSerial"),
                            "fromBand": pending.get("band"), "toBand": e.get("band"),
                            "fromCh": pending.get("channel"), "toCh": e.get("channel"),
                            "snrBefore": pending.get("rssi"), "snrAfter": e.get("rssi"),
                            "ms": e.get("durationMs") if (e.get("durationMs") or 0) >= 0 else None,
                            "dwellMs": pending.get("durationMs"), "ssid": e.get("ssidNumber"),
                            "gapS": round((t_new - t_old).total_seconds(), 1)})
            pending = None
    for k, r in enumerate(out):
        drop = (r["snrBefore"] or 0) - (r["snrAfter"] or 0)
        ms = r["ms"] or 0
        r["q"] = ("bad" if ms > BAD_ROAM_MS or drop >= 10
                  else "sub" if ms > SUB_ROAM_MS or drop > 0 or (r["gapS"] or 0) > GAP_SECONDS else "good")
        prev = out[k - 1] if k else None
        r["pingPong"] = bool(prev and prev["from"] == r["to"] and prev["to"] == r["from"]
                             and (parse_ts(r["t"]) - parse_ts(prev["t"])).total_seconds() <= PING_PONG_SECONDS)
    return out, sticky


def _roam_summary(s):
    ro = s.get("roaming")
    if not ro:
        return None
    names = {a["serial"]: a["name"] for a in s["aps"]}
    info = (s.get("clientDetail") or {}).get("info", {})
    for rs in ro["roams"].values():          # Meraki reports -1 when it didn't measure the roam time
        for r in rs:
            if r.get("ms") is not None and r["ms"] < 0:
                r["ms"] = None
    allr = [dict(r, mac=mac) for mac, rs in ro["roams"].items() for r in rs]
    tot = {"roams": len(allr), "good": sum(r["q"] == "good" for r in allr), "sub": sum(r["q"] == "sub" for r in allr),
           "bad": sum(r["q"] == "bad" for r in allr), "pingPong": sum(r["pingPong"] for r in allr),
           "stickyEvents": sum(ro["sticky"].values()), "stickyClients": len(ro["sticky"]),
           "pingPongClients": sum(1 for rs in ro["roams"].values() if sum(r["pingPong"] for r in rs) >= PING_PONG_CLIENT_MIN),
           "clientsRoaming": len(ro["roams"]), "clientsChecked": ro["clientsChecked"], "clientsSkipped": ro.get("clientsSkipped", 0),
           "avgMs": rnd(mean([r["ms"] for r in allr if r["ms"] is not None]), 0),
           "p95Ms": rnd(pct([r["ms"] for r in allr if r["ms"] is not None], .95), 0), "rules": ro.get("rules")}
    flows = {}
    for r in allr:
        f = flows.setdefault("%s|%s" % (r["from"], r["to"]), {"from": names.get(r["from"], r["from"]), "to": names.get(r["to"], r["to"]),
                                                                "n": 0, "good": 0, "sub": 0, "bad": 0, "ms": []})
        f["n"] += 1
        f[r["q"]] += 1
        if r["ms"] is not None:
            f["ms"].append(r["ms"])
    for f in flows.values():
        f["avgMs"] = rnd(mean(f.pop("ms")), 0)
    daily = {}
    for r in allr:
        d = daily.setdefault(r["t"][:10], {"good": 0, "sub": 0, "bad": 0})
        d[r["q"]] += 1
    clients = []
    for mac in set(ro["roams"]) | set(ro["sticky"]):
        rs = ro["roams"].get(mac, [])
        c = {"mac": mac, "name": (info.get(mac) or {}).get("name") or mac, "manufacturer": (info.get(mac) or {}).get("manufacturer"),
             "roams": len(rs), "good": sum(r["q"] == "good" for r in rs), "sub": sum(r["q"] == "sub" for r in rs),
             "bad": sum(r["q"] == "bad" for r in rs), "pingPong": sum(r["pingPong"] for r in rs), "sticky": ro["sticky"].get(mac, 0),
             "avgMs": rnd(mean([r["ms"] for r in rs if r["ms"] is not None]), 0),
             "worstDrop": max([(r["snrBefore"] or 0) - (r["snrAfter"] or 0) for r in rs], default=None)}
        c["score"] = c["bad"] * 3 + c["pingPong"] * 2 + c["sticky"] * 2 + c["sub"]
        clients.append(c)
    clients.sort(key=lambda c: (-c["score"], -c["roams"]))
    logs = {}
    for c in clients[:25]:
        logs[c["mac"]] = [{"t": r["t"], "from": names.get(r["from"], r["from"]), "to": names.get(r["to"], r["to"]),
                           "fromBand": r["fromBand"], "toBand": r["toBand"], "snrBefore": r["snrBefore"], "snrAfter": r["snrAfter"],
                           "ms": r["ms"], "dwellMs": r["dwellMs"], "gapS": r.get("gapS"), "q": r["q"], "pingPong": r["pingPong"]}
                          for r in ro["roams"].get(c["mac"], [])][-400:]
    return {"totals": tot, "flows": sorted(flows.values(), key=lambda f: -f["n"]), "daily": sorted(daily.items()),
            "clients": clients[:300], "logs": logs, "apNames": [a["name"] for a in s["aps"]]}


# ---------------------------------------------------------------- report
def summarize(s):
    """Reduce a snapshot to the numbers the report compares."""
    days = s["window"]["days"]
    out = {"label": s["label"], "window": s["window"], "capturedAt": s["capturedAt"], "network": s["network"]["name"],
           "networkId": s["network"]["id"], "org": s["org"]["name"],
           "notes": s.get("notes", []), "aps": s["aps"], "rfProfiles": s.get("rfProfiles"), "autoRf": s.get("autoRf"),
           "neighbors": s.get("neighbors"), "apiCalls": s.get("apiCalls"), "apiErrors": len(s.get("apiErrors", [])),
           "ssids": s.get("ssids", []), "radioOverrides": s.get("radioOverrides")}
    # clients
    daily = {b: mean([p["v"] for p in s["clients"].get(b, [])]) for b in ["all"] + BANDS}
    out["clients"] = {b: rnd(v) for b, v in daily.items()}
    band_sum = sum(v or 0 for b, v in daily.items() if b != "all")
    out["bandShare"] = {b: rnd(100 * (daily[b] or 0) / band_sum) if band_sum else None for b in BANDS}
    client_days = (daily["all"] or 0) * days
    # connections
    c = s["connections"]
    def succ(x):
        f = x["assoc"] + x["auth"] + x["dhcp"] + x["dns"]
        return rnd(100 * x["success"] / (x["success"] + f)) if x["success"] + f else None
    out["connections"] = {b: dict(c[b], successPct=succ(c[b]), failures=c[b]["assoc"] + c[b]["auth"] + c[b]["dhcp"] + c[b]["dns"]) for b in c}
    # latency
    out["latency"] = s["latency"]
    # packet loss
    pl = {}
    for b, devs in s["packetLoss"].items():
        up_t = sum(((d.get("up") or {}).get("total") or 0) for d in devs.values())
        up_l = sum(((d.get("up") or {}).get("lost") or 0) for d in devs.values())
        dn_t = sum(((d.get("down") or {}).get("total") or 0) for d in devs.values())
        dn_l = sum(((d.get("down") or {}).get("lost") or 0) for d in devs.values())
        pl[b] = {"upPct": rnd(100 * up_l / up_t, 2) if up_t else None, "downPct": rnd(100 * dn_l / dn_t, 2) if dn_t else None,
                 "upPackets": up_t}
    out["packetLoss"] = pl
    # radios
    out["radios"] = [{k: v for k, v in r.items() if k not in ("hourly", "dailyP95")} for r in s.get("radioUtil", {}).values()]
    util_band = {}
    for r in out["radios"]:
        util_band.setdefault(r["band"], []).append(r)
    out["utilByBand"] = {b: {"busiestP95": max((r["p95"] or 0) for r in rs), "busiestRadio": max(rs, key=lambda r: r["p95"] or 0)["ap"],
                             "avg": rnd(mean([r["avg"] for r in rs])), "nonWifiP95": max((r["nonWifiP95"] or 0) for r in rs)}
                         for b, rs in util_band.items() if b}
    # signal
    snr = {}
    for serial, bands in s["signal"].items():
        name = next((a["name"] for a in s["aps"] if a["serial"] == serial), serial)
        for b, v in bands.items():
            m_ = mean([p["v"] for p in v["snr"]])
            if m_ is not None:
                snr.setdefault(b, []).append({"ap": name, "snr": rnd(m_)})
    out["snr"] = {b: {"aps": v, "worst": min(v, key=lambda x: x["snr"]), "avg": rnd(mean([x["snr"] for x in v]))} for b, v in snr.items()}
    # traffic: GB per day
    gb = {}
    ap_gb = {}
    for serial, bands in s["usage"].items():
        name = next((a["name"] for a in s["aps"] if a["serial"] == serial), serial)
        for b, pts in bands.items():
            kbps = mean([p["v"] for p in pts])
            if kbps is None:
                continue
            g = kbps * 86400 / 8 / 1e6
            gb[b] = gb.get(b, 0) + g
            ap_gb[name] = ap_gb.get(name, 0) + g
    out["trafficGbPerDay"] = {b: rnd(v, 2) for b, v in gb.items()}
    out["trafficGbPerDay"]["all"] = rnd(sum(gb.values()), 2)
    out["trafficByAp"] = {k: rnd(v, 2) for k, v in ap_gb.items()}
    # events
    ev = s["events"]
    out["events"] = ev
    dis = sum(ev["counts"].get(k, 0) for k in DISCONNECT)
    dot = sum(ev["counts"].get(k, 0) for k in DOT1X_FAIL)
    # daily trends for sparklines
    def by_day(series):
        return {p["t"][:10]: p["v"] for p in series if p.get("t")}
    dly = {}
    dly["clients"] = sorted(by_day(s["clients"].get("all", [])).items())
    tr = {}
    for bands in s["usage"].values():
        for pts in bands.values():
            for k, v in by_day(pts).items():
                tr[k] = tr.get(k, 0) + v * 86400 / 8 / 1e6
    dly["trafficGb"] = sorted((k, rnd(v, 2)) for k, v in tr.items())
    ev_day = ev.get("byDay", {})
    dly["disconnects"] = sorted((k, sum(v.get(t, 0) for t in DISCONNECT)) for k, v in ev_day.items())
    dly["dot1x"] = sorted((k, sum(v.get(t, 0) for t in DOT1X_FAIL)) for k, v in ev_day.items())
    cd = s.get("connectionsDaily") or []
    def sp(x):
        f = sum((x.get(k) or 0) for k in ("assoc", "auth", "dhcp", "dns"))
        tot = f + (x.get("success") or 0)
        return rnd(100 * (x.get("success") or 0) / tot) if tot else None
    dly["successPct"] = [(x["day"], sp(x)) for x in cd]
    dly["authFail"] = [(x["day"], x.get("auth") or 0) for x in cd]
    ub = {}
    for r in s.get("radioUtil", {}).values():
        for k, v in r.get("dailyP95", []):
            if r.get("band") and v is not None:
                cur = ub.setdefault(r["band"], {})
                cur[k] = max(cur.get(k, 0), v)
    dly["busiestRadio"] = {b: sorted(v.items()) for b, v in ub.items()}
    sn = {}
    for bands in s["signal"].values():
        for v in bands.values():
            for k, x in by_day(v["snr"]).items():
                sn[k] = x if k not in sn else min(sn[k], x)
    dly["worstSnr"] = sorted(sn.items())
    out["daily"] = dly
    # one row per wireless client, joining every per-client source
    cd = s.get("clientDetail") or {}
    macs = set(cd.get("info", {})) | set(cd.get("conn", {})) | set(cd.get("latency", {})) | set(cd.get("loss", {})) | set(ev.get("byClient", {}))
    serial_name = {a["serial"]: a["name"] for a in s["aps"]}
    rows = []
    for mac in macs:
        i = cd.get("info", {}).get(mac, {})
        c = cd.get("conn", {}).get(mac)
        l = cd.get("latency", {}).get(mac, {})
        lo = cd.get("loss", {}).get(mac, {})
        e = ev.get("byClient", {}).get(mac, {})
        up, dn = lo.get("up") or {}, lo.get("down") or {}
        r = {"mac": mac, "name": i.get("name") or mac, "manufacturer": i.get("manufacturer"), "os": i.get("os"),
             "ssid": i.get("ssid"), "ap": i.get("ap"), "usageMb": i.get("usageMb")}
        if c:
            f = c["assoc"] + c["auth"] + c["dhcp"] + c["dns"]
            r.update({"success": c["success"], "assoc": c["assoc"], "auth": c["auth"], "dhcp": c["dhcp"], "dns": c["dns"],
                      "failures": f, "successPct": rnd(100 * c["success"] / (c["success"] + f)) if c["success"] + f else None})
        be, vo = l.get("bestEffortTraffic") or {}, l.get("voiceTraffic") or {}
        r.update({"latency": be.get("avg"), "latSamples": be.get("samples"), "voiceSlowPct": vo.get("over32Pct"), "voiceSamples": vo.get("samples")})
        if up.get("total"):
            r.update({"upLossPct": rnd(100 * (up.get("lost") or 0) / up["total"], 1), "upPackets": up["total"]})
        if dn.get("total"):
            r["downLossPct"] = rnd(100 * (dn.get("lost") or 0) / dn["total"], 1)
        r["disconnects"] = sum(e.get(t, 0) for t in DISCONNECT)
        r["dot1x"] = sum(e.get(t, 0) for t in DOT1X_FAIL)
        rows.append(r)
    rows.sort(key=lambda r: -((r.get("success") or 0) + (r.get("failures") or 0) + r["disconnects"]))
    out["clientRows"] = rows[:500]
    out["roaming"] = _roam_summary(s)
    out["rates"] = {
        "disconnectsPerClientDay": rnd(dis / client_days, 2) if client_days else None,
        "dot1xFailPerClientDay": rnd(dot / client_days, 3) if client_days else None,
        "disconnects": dis, "dot1xFailures": dot, "clientDays": rnd(client_days),
    }
    return out


def load_snapshot(path):
    with open(path) as f:
        s = json.load(f)
    if s.get("tool") != "wlan-snapshot":
        raise SystemExit("%s is not a wlan-snapshot file." % path)
    s["summary"] = summarize(s)   # always rebuild, so report fixes reach older snapshots
    return s


def report_payload(s):
    """What the HTML report needs from one snapshot (also the format the page accepts when a file is loaded)."""
    return {"tool": "wlan-snapshot", "version": s.get("version"), "summary": s["summary"],
            "radioHourly": {k: v.get("hourly", []) for k, v in s.get("radioUtil", {}).items()}}


def build_report(snaps, out):
    snaps = sorted(snaps, key=lambda x: x["window"]["t1"])
    payload = {"snapshots": [report_payload(s) for s in snaps], "sources": SOURCES, "eventTypes": EVENT_TYPES, "endpoints": ENDPOINTS,
               "generatedAt": iso(dt.datetime.now(dt.timezone.utc))}
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "report_template.html")) as f:
        tpl = f.read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))
    with open(out, "w") as f:
        f.write(html)
    log("Report written to %s" % out)


def cmd_report(args):
    snaps = [load_snapshot(p) for p in args.snapshots]
    if len({s["network"]["id"] for s in snaps}) > 1:
        log("Warning: the snapshots are from different networks.")
    latest = max(snaps, key=lambda x: x["window"]["t1"])
    out = args.out or "wlan-report-%s.html" % re.sub(r"[^A-Za-z0-9_-]+", "-", latest["network"]["name"])
    build_report(snaps, out)
    print(out)


def endpoints_markdown():
    rows = ["| Area | Endpoint | What it reads | Shown in | How far back | Calls per snapshot |", "|---|---|---|---|---|---|"]
    for e in ENDPOINTS:
        rows.append("| %s | `%s` | %s | %s | %s | %s |" % (e["area"], e["path"], e["use"], e["shows"], e["lookback"], e["calls"]))
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List orgs and wireless networks your key can see")
    sub.add_parser("endpoints", help="Print the Meraki API endpoints this tool uses, as a Markdown table")
    sn = sub.add_parser("snapshot", help="Capture a snapshot of one wireless network")
    sn.add_argument("--network", required=True, help="Network name or id")
    sn.add_argument("--org", help="Organization id, if the name is ambiguous")
    sn.add_argument("--label", default="snapshot", help="e.g. pre or post")
    sn.add_argument("--days", type=int, default=14, help="Window length ending now (max 14 for per-radio detail)")
    sn.add_argument("--end", help="End of window, ISO time (default: now). For testing only.")
    sn.add_argument("--out", default="snapshots", help="Folder for snapshot files")
    sn.add_argument("--no-report", action="store_true", help="Save the snapshot file only, without its HTML report")
    rp = sub.add_parser("report", help="Write an HTML report for one snapshot, or several to compare in the page")
    rp.add_argument("snapshots", nargs="+", help="One or more snapshot .json files")
    rp.add_argument("--out", help="Output HTML path")
    args = ap.parse_args()

    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "endpoints":
        return print(endpoints_markdown())
    key = os.environ.get("MERAKI_API_KEY")
    if not key:
        raise SystemExit("Set MERAKI_API_KEY to a Meraki Dashboard API key (read-only is enough).")
    m = Meraki(key)
    if args.cmd == "list":
        return cmd_list(m, args)
    if args.days < 1 or args.days > 31:
        raise SystemExit("--days must be between 1 and 31.")
    snapshot(m, args)


if __name__ == "__main__":
    main()
