# Cisco Meraki WLAN Snapshot

A small app that runs on your own computer and takes a WLAN snapshot of a Meraki wireless network. Each snapshot is saved as a file with its own report:

- **Overview:** access points online, RADIUS servers reachable, AI-RRM on or off (and any radios fixed manually, outside its control), connection success, auth failures, disconnects, busiest radio, weakest client signal, packet loss, slow voice traffic, good roams and clients per day. Click any tile for what it means and what to review.
- **Scorecard:** 21 metrics, each with a plain-language explanation and a daily trend line. Click any row to see the clients, radios, APs or neighbouring networks behind that number.
- **RF configuration:** band, channel, width, power and SSIDs of every radio, plus RF profile, AutoRF and neighbouring networks.
- **By band:** how 2.4, 5 and 6 GHz each perform.
- **Radio utilization:** how busy every radio is, from 10-minute samples, including each 5 GHz radio on dual-5 GHz APs.
- **Roaming:** good, suboptimal and bad roams, a roam map between APs, the clients struggling most, and a roam timeline for any client.
- **Client issues:** failed joins by stage, disconnects and 802.1X failures, the worst APs and clients.

Take a snapshot before an install and another after, or one every month. Open any report and load another snapshot to see what improved and what got worse.

Meraki only keeps detailed wireless history for a short time: per-radio utilization for 14 days, most other history for 31 days. Saving snapshots as files means you can compare against a point in time long after Meraki has deleted that data.

> **Disclaimer: read-only.** This tool only sends GET (read) requests to the Meraki Dashboard API. It never sends POST, PUT, PATCH or DELETE requests and pushes no changes whatsoever, so it can't change any setting, device or network in your Meraki environment. A **read-only admin API key** is sufficient, and is recommended.

## Requirements

- **Python 3.8 or newer.** If it's missing, the launcher offers to install it: on Windows through Windows Package Manager (just for your account, no admin rights needed), and on a Mac through Apple's Command Line Tools installer. You can also get it from <https://www.python.org/downloads/>. On Windows, tick **"Add python.exe to PATH"** during install. No other packages are needed.
- **A Meraki Dashboard API key.** A key for a read-only admin is enough, because the tool never makes changes. See [how to create an API key](https://developer.cisco.com/meraki/api-v1/authorization/).

## Start it

1. Unzip the folder anywhere.
2. Double-click the launcher for your system:
   - **Mac:** `Start WLAN Snapshot (Mac).command`. The first time, macOS may block it because it was downloaded. Right-click it, choose **Open**, then **Open** again.
   - **Windows:** `Start WLAN Snapshot (Windows).bat`.
3. Your browser opens the app. Paste your API key and click **Connect**.
4. Pick a wireless network, give the snapshot a label (for example `pre-install`) and click **Generate report**.

A snapshot takes about a minute for a small network and longer for a large one; the page shows progress. The report opens when it's done. Keep the small terminal window open while you use the app. Closing it stops the app.

From a terminal you can also run `python3 server.py --open`, adding `--port 9000` if you want a specific port.

## About your API key

- It's stored only in your browser on your computer. Untick "Remember on this computer" to keep it only until the browser tab closes.
- It's sent only to this app running locally, which passes it to `api.meraki.com` and nowhere else.
- It's never written into a snapshot or a report.

## Snapshots and reports

Each snapshot saves two files in the `snapshots` folder:

- `Network-label-YYYYMMDD-HHMM.json`: the data. Keep it; it's the only record once Meraki deletes the history.
- `Network-label-YYYYMMDD-HHMM.html`: the report for that snapshot.

**The period** is how much history a snapshot captures, counting back from when you click Generate report. The default and maximum is 14 days, because Meraki keeps per-radio detail for only 14 days. Use the same period every time so snapshots compare fairly. AP status and radio settings are read at the moment of capture.

**Comparing:** open a report through the app and pick another snapshot of the same network under **Compare with**, or use **Import snapshot…** to pick a `.json` from anywhere. A single snapshot shows values with no judgement; a comparison colours each change **green** (better), **red** (worse) or **grey** (no change beyond normal day-to-day variation). Snapshots can be any distance apart, today and a year from now. The report warns you if two periods overlap or are different lengths.

**Comparing with another network:** the **Compare with** list also shows snapshots of your other wireless networks, under **Other networks**. The report then puts the two networks side by side: columns are named after each network, green means this network does better and red means it does worse. AP-by-AP sections (radio settings, per-radio utilization) show this network only, because the APs are different. Networks differ in size, layout and use, so treat differences as prompts to look rather than verdicts. Use snapshots of the same period length.

**Exporting and keeping snapshots:** every report has **Export**:

- **HTML:** the report as one self-contained file. It opens on its own in any browser, and it can be loaded into a later report to compare, so it's a safe way to keep a snapshot. If a comparison is showing, the file holds both snapshots.
- **Data:** just the snapshot's data as a small `.json`, also loadable later.
- **PDF:** opens your browser's print dialog with a print layout; choose **Save as PDF**. PDFs are for reading and can't be loaded back.

**Import snapshot…** in any report accepts a report (`.html`) or data file (`.json`) saved by this app, from anywhere on your computer. You don't need to export anything to compare on the same computer: every snapshot is already saved in the `snapshots` folder and listed under **Compare with**. Export when you want a copy somewhere else, for example a project folder, a shared drive or an email.

## Meraki API endpoints used

Every figure in a report comes from these calls. The same list is shown in the app (**What this app reads from Meraki**) and at the bottom of every report (**Sources and method**), and `python3 wlan_snapshot.py endpoints` prints it.

| Area | Endpoint | What it reads | Shown in | How far back | Calls per snapshot |
|---|---|---|---|---|---|
| Finding your networks | `GET /organizations` | Lists the organizations your API key can see. | Network picker in the app | Current | 1 |
| Finding your networks | `GET /organizations/{organizationId}/networks` | Lists each organization's networks; the app keeps the ones with wireless. | Network picker in the app | Current | 1 per organization |
| Access points | `GET /organizations/{organizationId}/devices` | The network's access points: name, model, serial, firmware. | Access points tile, RF configuration | Current | 1 |
| Access points | `GET /organizations/{organizationId}/devices/statuses` | Online, alerting or offline; last time each AP reported; LAN IP. | Access points tile and list | Current | 1 |
| Access points | `GET /organizations/{organizationId}/wireless/clients/overview/byDevice` | Clients connected to each AP right now. | Access points tile and list | Current | 1 |
| RF configuration | `GET /devices/{serial}/wireless/status` | Each radio's band, channel, channel width, transmit power and the SSIDs it broadcasts. | RF configuration; also works out which radio is which band | Current | N |
| RF configuration | `GET /devices/{serial}/wireless/radio/settings` | Which RF profile each AP uses and any manual channel or power overrides. | RF configuration | Current | N |
| RF configuration | `GET /networks/{networkId}/wireless/rfProfiles` | Band steering, minimum bitrates, channel widths and flex-radio settings. | RF configuration | Current | 1 |
| RF configuration | `GET /organizations/{organizationId}/wireless/radio/rrm/byNetwork` | AutoRF settings: whether AI-RRM is on and when it was enabled, FRA, busy-hour behaviour, channel avoidance. | AI-RRM tile, RF configuration | Current | 1 |
| RF configuration | `GET /organizations/{organizationId}/wireless/radio/overrides/byDevice` | Radios with a manually fixed channel, power or channel width. These are outside AI-RRM's control even when it's on. | AI-RRM tile | Current | 1+ (pages of 100) |
| RF configuration | `GET /networks/{networkId}/wireless/airMarshal` | Other networks your APs hear, with signal strength. | Strong neighbouring networks | 31 days (last 7 days used) | 1 |
| RF configuration | `GET /networks/{networkId}/wireless/ssids` | Enabled SSIDs, their security mode and RADIUS servers. | RADIUS servers tile | Current | 1 |
| Radio utilization | `GET /networks/{networkId}/networkHealth/channelUtilization` | How busy every radio is, in 10-minute samples, including non-Wi-Fi interference. Each 5 GHz radio on dual-5 GHz APs separately. | Busiest radio tile, radio utilization, scorecard | 14 days (Meraki refuses older 10-minute data) | 2 (7-day pages) |
| Clients and traffic | `GET /networks/{networkId}/wireless/clientCountHistory` | Clients per day, for the whole network and for each band. | Clients per day, band share | 31 days | 4 |
| Clients and traffic | `GET /networks/{networkId}/wireless/usageHistory` | Traffic per day for each AP and band. | Traffic per day, by band | 31 days | N × B |
| Clients and traffic | `GET /networks/{networkId}/wireless/signalQualityHistory` | Client signal-to-noise (SNR) per day for each AP and band. | Weakest client signal, by band | 31 days | N × B |
| Clients and traffic | `GET /networks/{networkId}/clients` | Every client: name, MAC, maker, OS, SSID, last AP, data used, and the client ID needed for roaming. | Client lists in the detail panels | 31 days | 1+ (pages of 1,000) |
| Connection quality | `GET /networks/{networkId}/wireless/connectionStats` | Successful joins and failures at association, authentication, DHCP and DNS; whole network, each band, and each day. | Connection success, auth failure rate, failed joins by stage | 180 days documented, about 65 days in practice; 7 days per call | 8 + D |
| Connection quality | `GET /networks/{networkId}/wireless/clients/connectionStats` | The same join results for each client. | Connection success and auth detail panels | 180 days; 7 days per call | 2 |
| Connection quality | `GET /networks/{networkId}/wireless/latencyStats` | Wireless latency per traffic class (voice, video, best effort, background), whole network and each band. | Latency, slow voice packets | 180 days; 7 days per call | 8 |
| Connection quality | `GET /networks/{networkId}/wireless/clients/latencyStats` | The same latency figures for each client. | Latency and voice detail panels | 180 days; 7 days per call | 2 |
| Connection quality | `GET /organizations/{organizationId}/wireless/devices/packetLoss/byDevice` | Upstream and downstream packet loss per AP, whole network and each band. | Upstream packet loss, by band | 90 days | 4 |
| Connection quality | `GET /organizations/{organizationId}/wireless/devices/packetLoss/byClient` | Packet loss per client. | Packet loss detail panel | 90 days | 1 |
| Client events | `GET /networks/{networkId}/events` | Wireless event log (productType=wireless): disassociations, WPA and 802.1X deauthentications, 802.1X failures and RADIUS timeouts, 802.1X successes, fast roams. | Disconnects, 802.1X failures, RADIUS reachability, client issues | 60+ days in practice | 1 per 1,000 events |
| Roaming | `GET /networks/{networkId}/wireless/clients/{clientId}/connectivityEvents` | Each client's roam, connection and sticky events: the AP it left and joined, roam time, signal before and after. | Roaming tile, scorecard rows and section | 31 days | C (up to 300 busiest clients) |

N = access points, B = bands per AP, C = wireless clients, D = days in the period. A 14-day snapshot of a 3-AP network makes about 130 calls. On large sites most calls go to roaming, one per client, capped at the 300 busiest clients. Calls are made one at a time, at most 8 a second (Meraki allows 10 per organization), and the tool waits and retries when Meraki asks it to slow down (HTTP 429).

Wireless event types read from the event log: `disassociation`, `wpa_deauth`, `8021x_deauth`, `8021x_eap_failure`, `8021x_radius_timeout`, `8021x_client_timeout`, `8021x_eap_success`, `aps_association_reject`, `11r_fast_roam`.

### How each metric is calculated

| Metric | Calculation | From |
|---|---|---|
| Access points online | APs whose status is `online`, out of all APs in the network | `devices/statuses` |
| AI-RRM | On or off for the network, from `ai.enabled`, with the date it was enabled. Radios with a fixed channel, power or width are listed, because AutoRF and AI-RRM can't manage them | `wireless/radio/rrm/byNetwork`, `wireless/radio/overrides/byDevice` |
| RADIUS servers reachable | A server counts as reachable when at least one AP logged a successful 802.1X login through it; APs with RADIUS timeouts are listed as warnings | `wireless/ssids`, `events` |
| Connection success | Successful joins ÷ (successful + failed at association, authentication, DHCP or DNS) | `wireless/connectionStats` |
| Auth failure rate | Joins failed at authentication ÷ all join attempts | `wireless/connectionStats` |
| 802.1X failures per day | EAP failures + RADIUS timeouts + client timeouts, ÷ days | `events` |
| Disconnects per client per day | Disassociations + WPA and 802.1X deauthentications, ÷ (average daily clients × days) | `events`, `wireless/clientCountHistory` |
| Latency, normal traffic | Meraki's average best-effort latency, weighted by samples across the 7-day pages | `wireless/latencyStats` |
| Slow voice packets | Voice samples in Meraki's 32 ms-or-slower buckets ÷ all voice samples | `wireless/latencyStats` |
| Upstream packet loss | Packets lost ÷ packets sent, client to AP, all APs | `wireless/devices/packetLoss/byDevice` |
| Busiest 2.4 / 5 / 6 GHz radio | The highest 95th percentile of 10-minute utilization among that band's radios | `networkHealth/channelUtilization` |
| Non-Wi-Fi interference | The highest 95th percentile of non-Wi-Fi utilization on any radio | `networkHealth/channelUtilization` |
| Weakest client signal | The lowest average client SNR of any AP and band | `wireless/signalQualityHistory` |
| Clients on 5 or 6 GHz | 5 GHz + 6 GHz share of average daily clients per band | `wireless/clientCountHistory` |
| Strong neighbouring networks | Other networks' BSSIDs heard by one of your APs at 25 dB or more, last 7 days | `wireless/airMarshal` |
| Good roams / bad roams | Share of roams in each class (see below) | `clients/{clientId}/connectivityEvents` |
| Average roam time | Mean of Meraki's measured roam times; roams Meraki didn't time are left out | `clients/{clientId}/connectivityEvents` |
| Ping-pong / sticky clients | See below | `clients/{clientId}/connectivityEvents` |
| Clients per day | Average of daily unique wireless clients | `wireless/clientCountHistory` |
| Traffic per day | Each AP and band's average daily throughput, converted to GB per day and summed | `wireless/usageHistory` |

**RADIUS reachability is worked out, not measured.** Meraki's API has no read-only RADIUS health check, so the report counts, per AP and per 802.1X SSID, successful 802.1X logins (proof that AP reached the server) and RADIUS timeouts (it didn't). An AP with no 802.1X logins in the period shows grey: unknown, not down.

### How roams are classed

Meraki's API has no roaming-analytics endpoint, so roams are rebuilt from each client's connectivity events: a `roam` event on the AP it left (signal there, time spent there), then a `connection` event with `reassoc` on the new AP (signal on arrival, roam time).

- **Bad:** roam time over 3 seconds, or the new AP's signal is 10 dB or more weaker (Meraki's definition).
- **Suboptimal:** roam time over 1 second, a weaker signal on the new AP (under 10 dB worse), or the client was off the network for more than 10 seconds between APs.
- **Good:** everything else.
- **Ping-pong:** back to the AP the client just left within 2 minutes. A client counts as ping-ponging after 3 of these.
- **Sticky:** Meraki's own sticky-client events.

Meraki doesn't publish its rules for suboptimal and ping-pong, so counts can differ slightly from the Roaming page in Dashboard. Change the thresholds at the top of `wlan_snapshot.py`.

## Guidelines

Guidelines are hidden by default. Click **Show guidelines** above the scorecard to add a column with each one. Values outside a guideline get a neutral "Outside guideline" note, not a red mark, and guidelines are never used to colour a comparison. They're rules of thumb for enterprise Wi-Fi, not Meraki thresholds or pass/fail tests; each one's (i) explains its basis and when it can mislead.

| Metric | Guideline | Can mislead when |
|---|---|---|
| Connection success | ≥ 95% | A few devices retrying a wrong password pull it down |
| Auth failure rate | < 1% | One device with an old saved password fails all day |
| 802.1X failures per day | < 1 | Test accounts or one laptop with an expired certificate |
| Disconnects per client per day | < 10 (rough estimate) | Phones disassociate when they sleep and when they roam |
| Latency, normal traffic | < 10 ms | Meraki's average looks better than its own histogram |
| Slow voice packets | < 5% | No real voice devices, so a few mismarked packets dominate |
| Upstream packet loss | < 1% | A few clients at the edge of coverage cause most of it |
| Busiest radio per band | < 40% | In dense buildings, neighbours fill 2.4 GHz whatever you do |
| Non-Wi-Fi interference | < 10% | Brief spikes from microwaves or radar are normal |
| Weakest client signal | ≥ 25 dB | A few distant clients drag the weakest AP down |
| Clients on 5 or 6 GHz | ≥ 60% (rough estimate) | IoT devices are often 2.4 GHz only |
| Good roams | ≥ 90% (rough estimate) | A couple of devices at the edge of coverage |
| Bad roams | < 2% | Clients walking out of the building |
| Average roam time | < 150 ms | One slow 802.1X roam among few roams |
| Ping-pong / sticky clients | 0 | Some devices are sticky whatever the network does |

Edit them in `report_template.html` (`tgt` in the `M` list and the `GUIDE` notes).

## Limits to know

- **Per-radio utilization only goes back 14 days.** Meraki refuses 10-minute data older than 14 days, so a snapshot can't cover an older period.
- **A snapshot can't be taken for the past.** To have a "before", take it before the change.
- **Radio settings are read when the snapshot runs.** They describe the network at that moment, not the whole period.
- **Radio-to-band mapping:** wifi0, wifi1 and wifi2 are matched to bands by the order Meraki lists each radio's BSSIDs. Verified on CW9166I and CW9176I.
- **Latency:** Meraki's reported average can look much better than its own histogram, which is why the slow-voice figure comes from the histogram.
- **Roaming on large sites** covers the 300 busiest clients; the report says when others were skipped.

## Privacy

Snapshots and reports include each wireless client's name, MAC address, maker, OS and per-client statistics, plus AP IP addresses. Treat them as customer data. They stay on your computer in the `snapshots` folder; nothing is sent anywhere except the API calls to Meraki.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Meraki rejected that API key" | Check the key in Meraki Dashboard (My Profile → API access) and that API access is enabled for the organization. |
| A network you expect isn't listed | Only networks with wireless are listed, and only in organizations where API access is enabled. |
| Windows: nothing happens, or the window flashes and closes | Make sure you extracted the zip (right-click → **Extract All…**) and are running the launcher from the extracted folder, not from inside the zip. |
| Windows: Python was just installed but the app doesn't start | Close the launcher window and double-click it again. |
| Windows: "Windows protected your PC" | Click **More info** → **Run anyway**. |
| `CERTIFICATE_VERIFY_FAILED` on Mac | Open your Python folder in Applications and run **Install Certificates.command**. |
| A snapshot is slow | Large sites make one roaming call per client (up to 300). Leave the window open; progress shows on the page. |
| A report looks out of date | Hard refresh with ⌘⇧R or Ctrl+Shift+R. |

## Command line

The same tool works without the app:

```bash
export MERAKI_API_KEY=your_key_here
python3 wlan_snapshot.py list
python3 wlan_snapshot.py snapshot --network "Ontario" --label pre-install
python3 wlan_snapshot.py report snapshots/Ontario-*.json
python3 wlan_snapshot.py endpoints
```

`--days` sets the period (default and maximum for per-radio detail: 14). `report` builds one report holding several snapshots.

## Files

- `server.py`: the local app. Serves the pages and runs snapshots.
- `app.html`: the start page (API key, network, Generate report, saved snapshots).
- `wlan_snapshot.py`: takes snapshots, works out the metrics, builds reports. The `ENDPOINTS` list at the top documents every API call.
- `report_template.html`: the report.
- `Start WLAN Snapshot (Mac).command`, `Start WLAN Snapshot (Windows).bat`: the launchers.
