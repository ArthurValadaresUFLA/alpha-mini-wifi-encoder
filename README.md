# alpha-mini-wifi-encoder

Encoder and BLE sender for the **UBTECH Alpha Mini** Wi-Fi provisioning payload.
It reproduces, byte for byte, the frames the companion app writes to the robot's GATT write
characteristic. Only `send` and `scan` touch Bluetooth; `encode` is fully offline.

## Install and run

```bash
uv sync
uv run alpha-mini-wifi-encode --help
```

Commands:

| Command | What it does |
|---|---|
| `encode` | Offline: prints the JSON, frame and GATT writes. No I/O. |
| `scan` | Lists nearby BLE devices. |
| `send` | Encodes the payload and **writes it to the robot over BLE**. |

### Sending to the robot (interactive)

```bash
uv run alpha-mini-wifi-encode send            # menus for robot and network
uv run alpha-mini-wifi-encode send -i         # force menus even if flags are given
```

When `--device` or the network flags (`--ssid` and `--cap`, or `--current-network`) are missing,
`send` opens arrow-key menus (up/down, Enter, Ctrl-C to cancel):

1. **Network**: the network this machine is connected to, networks already configured on it
   (Linux `nmcli`, Windows `netsh`, macOS `networksetup`), or *Enter another network...* (SSID +
   security). The password is always typed in a hidden prompt (or `--pwd` /
   `ALPHA_MINI_WIFI_PASSWORD`); it is **never read from the OS and never written to disk**.
2. **Purpose**: bind a new robot (`co=21`) or change Wi-Fi of a bound one (`co=2`).
3. **Robot**: BLE scan results, devices advertising the Alpha Mini service first; *Rescan* available.
4. **Confirmation** before anything is written.

Non-interactive example (no terminal needed):

```bash
ALPHA_MINI_WIFI_PASSWORD=... uv run alpha-mini-wifi-encode send \
  --ssid MyWiFi --cap WPA2 --device AA:BB:CC:DD:EE:FF --yes
```

`--dry-run` shows the bytes without connecting (they contain the password). After writing, the
tool listens for `--listen` seconds and decodes the robot's replies (best effort: the response
layout is inferred from the analysis report and not confirmed on hardware).

### Current network / encode options

```bash
uv run alpha-mini-wifi-encode encode --current-network --include-bssid --layers
```

SSID and security of the current network are detected and mapped to the app's values `WPA`,
`WPA2`, `WEP`, `N` (open; unconfirmed on hardware); WPA3-only and enterprise networks are rejected.
The BSSID is omitted unless `--include-bssid` is given, like the app's native flow. `--ssid`,
`--cap` and `--bssid` override detected values.

## Programming mode and online operation (v0.3)

Besides Wi-Fi provisioning, the package implements the app's **programming (CodeMao) skill**
over two transports, based on the analysis report (static analysis only; **not validated on
hardware**).

```console
# Offline: show the exact frame of a command (BLE shows GATT writes, LAN only the TCP frame)
alpha-mini-wifi-encode program encode --programme-version 3 --action handshake
alpha-mini-wifi-encode program encode --programme-version 3 --via lan --output json

# BLE: handshake (cmdId 29), then run / stop / revert origin (33 / 33 / 28)
alpha-mini-wifi-encode scan
alpha-mini-wifi-encode program run --programme-version 3 --device AA:BB:CC:DD:EE:FF \
    --do start --hold 10

# LAN (robot already online): inventory first, then connect over TCP
alpha-mini-wifi-encode lan-scan --expect-ssid <your-ssid>
alpha-mini-wifi-encode program run --programme-version 3 --via lan --host <ip> --port <port>
```

The TCP port comes from mDNS in the app; the code has no default. The only port observed on
hardware was `7000`, a single data point that is **not** a specification (`8801` of the SDK and
`5555` of classic ADB were not seen, but the scanned range was not recorded).

### Discovery and diagnosis (read-only)

* `lan-scan` browses **all** known mDNS types in one run: the app's `_services._dns-sd._udp.local.`
  (a meta-query, expanded into the types it advertises) and `_DEDU_mini_robot_control._tcp.local.`,
  plus the SDK's `_Mini_mini_channel_server._tcp.local.` and `_Dedu_mini_channel_server._tcp.local.`
  (the SDK's EDU/KOR variants were not recovered). It prints counters, TXT records and a
  diagnostic. **An empty result is inconclusive**: it does not prove that mDNS is off.
* `--expect-ssid` aborts when the machine is on another Wi-Fi network (mDNS is link-local, so a
  scan from there would be a false negative); `--interface <local-ip>` selects the multicast
  interface. Without a detectable Wi-Fi it only warns.
* `lan-diag` adds an optional `--diag-connect --host <ip> [--ports 7000,8801,5555]`: after a
  confirmation it opens and closes a TCP connection to each listed port. **No byte is sent**, and
  the report records the tool and the exact ports, because "closed" means nothing without them.

| Layer | Module | Role |
| --- | --- | --- |
| Messages | `codemao_messages`, `codemao` | Protobuf bodies, envelope codec, typed results |
| Framing | `layers`, `frames` | `FrameLayer` (BLE tail `01 ED`), `LanFrameLayer` (checksum), stream decoder |
| Safety | `stop_guard` | Allow-list of commands and handshake stop criteria |
| Session | `session` | `sendSerial` counter, reply correlation, events, timeouts |
| BLE | `ble_transport` | Persistent GATT link, 16-byte slices, notification reassembly |
| LAN | `lan`, `lan_discovery`, `port_probe` | TCP + 7-field handshake (0 -> 1000), heartbeat, mDNS, TCP probe |

```python
async with CodeMaoSession(LanTransport(host, port)) as session:
    await session.handshake(user_id="0", nickname="me", programme_version=3)
    await session.start_program()
```

### Safety

* Only handshake, start/stop program and revert origin can be sent: `StopGuard` refuses every
  other command id (so no reset, erase, unbind or factory opcode can leave the client), and stops
  on a refused LAN handshake, a non-zero `errorCode` or an unexpected interface version.
* **ADB is not implemented.** There are three distinct paths and none is wired here: the app's
  settings screen sends IM `316` through Tencent IM (cloud, needs the account session); the
  official SDK sends cmd `7` (`AdbSwitchRequest{open}`) over WebSocket `ws://<ip>:8801`; neither
  is the LAN CodeMao channel. Cmd `7` enables USB debugging and is not known to open TCP/5555.
  Any future module would need explicit authorisation and an enforced switch-off.
* The LAN handshake uses freshly generated random identifiers, never copied ones. `program run`
  asks for confirmation unless `--yes` is given.

### Wire details taken from the app

* **LAN frame:** `FB BF 01 <len BE> <payload> <checksum> ED` with `checksum = (len + 2) & 0xFF`,
  where `len` is the length field (`payload + 6`). BLE keeps the constant `01 ED`.
* **LAN heartbeat:** the robot sends `0x00` (ping, discarded); the client writes `0x01` (pong)
  when its writer is idle for 5 s; the link drops after 7 s without reads.
* **LAN handshake:** `BluetoothHandShakeRequest` (7 fields, `clientVersionCode=1030`,
  `deviceType=1`) answered by `BluetoothShakeResponse` (9 fields, with `errorCode`, versions and
  `sessionId`, shown by `program run --via lan`).
* The BLE (`int64`) and LAN (`int32`) message headers encode identically for the small values
  used here; they are different protocol definitions and are documented as such.

### Assumptions to verify on hardware

* **CodeMao response field numbers** (the three result triples) assume consecutive numbers in
  the order of the report; the request layouts and the LAN handshake pair are recovered.
* **Reply correlation** assumes `responseSerial == sendSerial` (falling back to `commandId`).
* **Whether the LAN channel accepts the CodeMao commands** (29/33/28): in the app the programming
  session starts over BLE.
* **BLE notifications** are assumed to carry the 4-byte trailer (`--raw-notifications` otherwise).
* `deviceCountry` and `clientLan` defaults (`US`, `en`) are placeholders; set them on `LanIdentity`.

## Wire format (summary)

| Layer | Content |
|---|---|
| JSON | `{"b","c","p","s","v","co"}`, Gson order, nulls omitted, HTML-safe escaping |
| 1 | `BindOrSwitchWifiRequest{data=json, versionCode=168}` |
| 2/3 | `Message{header{commandId=32,sendSerial,versionCode=1}, bodyData}` |
| 4 | `FB BF 01 <len BE> <content> 01 ED` (`len` = total size - 1) |
| 5 | 16-byte slices + `randomId(2B)` + `serialNo(2B)` per GATT write |

## Architecture and patterns

| Module | Role | Pattern |
|---|---|---|
| `models` | Validated, immutable value objects (pydantic) | Value Object |
| `serializers` | `GsonCompatSerializer`, `CompactJsonSerializer` | Strategy |
| `ids` | `FixedRandomId`, `SystemRandomId` | Strategy |
| `layers` | Protocol layers chained by `EncodingPipeline` | Pipeline / Chain |
| `chunking` | `TrailerChunker` | Strategy |
| `encoder` | `WifiProvisioningEncoder` (+ `with_defaults`) | Facade, Factory method, DI |
| `network` | `NmcliDetector`, `NetshDetector`, `SystemProfilerDetector`, `create_detector` | Strategy, Factory |
| `saved_networks` | Saved-network providers per OS | Strategy, Factory |
| `ble` | `BleakDeviceScanner`, `BleakGattWriter` (bleak) | Strategy, Adapter |
| `interactive` | `Prompter` over questionary, menu flows | Adapter |
| `responses` | Best-effort decoding of robot notifications | - |
| `cli` | click + rich front end (`encode`, `scan`, `send`) | - |

Protobuf encoding uses the official `protobuf` runtime (messages declared via a
descriptor, no hand-written varints).

## Quality gates

```bash
uv run pre-commit install --install-hooks && uv run pre-commit install --hook-type pre-push
uv run ruff check . && uv run ruff format --check .
uv run mypy                # strict
uv run pytest              # fails below 95 % coverage
```

Pre-commit runs hygiene hooks, `uv-lock`, ruff and mypy on commit, and pytest on push.
