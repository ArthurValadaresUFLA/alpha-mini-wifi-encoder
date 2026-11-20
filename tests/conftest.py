import pytest

from alpha_mini_wifi_encoder import WifiCredentials, WifiProvisioningEncoder


@pytest.fixture
def credentials() -> WifiCredentials:
    """The worked example of the analysis report."""
    return WifiCredentials(
        ssid="MyWiFi",
        capabilities="[WPA-PSK-CCMP][WPA2-PSK-CCMP][ESS]",
        password="password123",
        bssid="aa:bb:cc:dd:ee:ff",
        dont_ping="0",
    )


@pytest.fixture
def encoder() -> WifiProvisioningEncoder:
    return WifiProvisioningEncoder.with_defaults()
