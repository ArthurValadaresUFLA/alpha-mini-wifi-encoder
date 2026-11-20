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
