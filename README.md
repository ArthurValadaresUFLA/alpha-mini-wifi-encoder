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

## Wire format (summary)

| Layer | Content |
| --- | --- |
| JSON | `{"b","c","p","s","v","co"}`, Gson order, nulls omitted, HTML-safe escaping |
| 1 | `BindOrSwitchWifiRequest{data=json, versionCode=168}` |
| 2/3 | `Message{header{commandId=32,sendSerial,versionCode=1}, bodyData}` |
| 4 | `FB BF 01 <len BE> <content> 01 ED` (`len` = total size - 1) |
| 5 | 16-byte slices + `randomId(2B)` + `serialNo(2B)` per GATT write |

## Quality gates

```bash
uv run pre-commit install --install-hooks && uv run pre-commit install --hook-type pre-push
uv run ruff check . && uv run ruff format --check .
uv run mypy                # strict
uv run pytest              # fails below 95 % coverage
```

Pre-commit runs hygiene hooks, `uv-lock`, ruff and mypy on commit, and pytest on push.
