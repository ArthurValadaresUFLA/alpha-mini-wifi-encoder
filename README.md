# alpha-mini-wifi-encoder

Offline encoder for the **UBTECH Alpha Mini** BLE Wi-Fi provisioning payload.
It reproduces, byte for byte, the frames the companion app would write to the
robot's GATT write characteristic. **It performs no BLE I/O** and never talks to a device.

## Install and run

```bash
uv sync
uv run alpha-mini-wifi-encode --cap '[WPA-PSK-CCMP][WPA2-PSK-CCMP][ESS]' \
  --ssid MyWiFi --pwd password123 --bssid aa:bb:cc:dd:ee:ff --dont-ping 0 --layers
```

Options: `--change-wifi` (`co=2`), `--send-serial`, `--random-id` / `--randomize`,
`--json-style gson|compact`, `--output pretty|json`. The password may be given via
`ALPHA_MINI_WIFI_PASSWORD` to keep it out of shell history.

### Use the Wi-Fi network this machine is connected to

```bash
uv run alpha-mini-wifi-encode --current-network            # asks for the password (hidden)
uv run alpha-mini-wifi-encode --current-network --include-bssid --layers
```

SSID and security are detected (Linux: `nmcli`; Windows: `netsh`; macOS: `system_profiler`,
best effort, may need Location Services permission). Security is mapped to the app's values
`WPA`, `WPA2`, `WEP`, `N` (open; unconfirmed on hardware); WPA3-only and enterprise networks are
rejected. The BSSID is omitted unless `--include-bssid` is given, like the app's native flow.
`--ssid`, `--cap` and `--bssid` override detected values. **The password is never read from the
OS**: pass `--pwd`, `ALPHA_MINI_WIFI_PASSWORD`, or type it at the prompt.

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
| `cli` | click + rich front end | - |

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
