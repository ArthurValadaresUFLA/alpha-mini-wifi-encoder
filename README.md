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

## Programming mode and online operation (v0.2)

Besides Wi-Fi provisioning, the package implements the app's **programming (CodeMao) skill**
over two transports, based on the analysis report (static analysis only; **not validated on
hardware**).

```console
# Offline: show the exact frame and GATT writes of a command
alpha-mini-wifi-encode program encode --programme-version 3 --action handshake
alpha-mini-wifi-encode program encode --programme-version 3 --action start --output json

# BLE: handshake (cmdId 29), then run / stop / revert origin (33 / 33 / 28)
alpha-mini-wifi-encode scan
alpha-mini-wifi-encode program run --programme-version 3 --device AA:BB:CC:DD:EE:FF \
    --do start --hold 10

# LAN (robot already online on Wi-Fi): discover via mDNS, then connect over TCP
alpha-mini-wifi-encode lan-scan
alpha-mini-wifi-encode program run --programme-version 3 --via lan --host 192.168.0.50 --port 9000
```

| Layer | Module | Role |
| --- | --- | --- |
| Messages | `codemao_messages`, `codemao` | Protobuf bodies, envelope codec, typed results |
| Framing | `frames` | Incremental `FB BF 01 ... ED` stream decoder |
| Session | `session` | `sendSerial` counter, reply correlation, events, timeouts |
| BLE | `ble_transport` | Persistent GATT link, 16-byte slices, notification reassembly |
| LAN | `lan`, `lan_discovery` | TCP + app handshake (0 -> 1000), heartbeat, mDNS scan |

```python
async with CodeMaoSession(LanTransport(host, port)) as session:
    await session.handshake(user_id="0", nickname="me", programme_version=3)
    await session.start_program()
```

Safety: only handshake, start/stop program and revert origin are exposed. There is no reset,
erase, unbind, factory or ADB command (ADB goes through Tencent IM in the app and is out of
scope), and the LAN handshake uses freshly generated random identifiers, never copied ones.
`program run` asks for confirmation unless `--yes` is given.

### Assumptions to verify on hardware

* **Frame checksum byte.** The report states `(len + 2) & 0xFF`, but the existing, byte-exact
  Wi-Fi encoder (and these commands) write the constant `0x01`. The report's definition of
  `len` is ambiguous; if the robot rejects frames, start here (`layers.FrameLayer`).
* **Response field numbers** and the **LAN handshake fields** (`uuid`, `appVersion`, `locale`,
  `deviceType`, `sessionId`) assume consecutive numbers in the order listed in the report.
* **Reply correlation** assumes `responseSerial == sendSerial` (falling back to `commandId`).
* **LAN heartbeat** payload `0x00` (ping) / `0x01`, the 7 s read timeout and the mDNS service
  type (`_DEDU_mini_robot_control._tcp.local.`) come from the report, unconfirmed.
* **BLE notifications** are assumed to carry the 4-byte trailer (`--raw-notifications` otherwise).

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
