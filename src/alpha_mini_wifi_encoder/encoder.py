"""High-level facade that encodes a Wi-Fi provisioning request offline.

:class:`WifiProvisioningEncoder` composes JSON serialisation, the encoding layers and GATT
chunking into a single call. :class:`EncodedProvisioning` collects the JSON text, every
intermediate layer output, the final frame and the write values. The facade performs no BLE
input or output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from alpha_mini_wifi_encoder.chunking import ChunkingStrategy, TrailerChunker
from alpha_mini_wifi_encoder.constants import WifiCommand
from alpha_mini_wifi_encoder.ids import FixedRandomId
from alpha_mini_wifi_encoder.layers import (
    BindOrSwitchWifiLayer,
    EncodingPipeline,
    FrameLayer,
    LayerOutput,
    MessageLayer,
)
from alpha_mini_wifi_encoder.models import WifiPayload
from alpha_mini_wifi_encoder.serializers import GsonCompatSerializer, PayloadSerializer

if TYPE_CHECKING:
    from alpha_mini_wifi_encoder.ids import RandomIdProvider
    from alpha_mini_wifi_encoder.models import WifiCredentials


@dataclass(frozen=True, slots=True)
class EncodedProvisioning:
    """Result of an encoding run.

    Attributes:
        json_text (str): Inner JSON document.
        layers (tuple[LayerOutput, ...]): Output of every layer, innermost first.
        frame (bytes): Final ``MiniBleProto`` frame (last layer output).
        chunks (tuple[bytes, ...]): GATT write values (frame slices plus trailers).
    """

    json_text: str
    layers: tuple[LayerOutput, ...]
    frame: bytes
    chunks: tuple[bytes, ...]


class WifiProvisioningEncoder:
    """Facade over serialisation, layering and chunking. Performs no BLE I/O."""

    def __init__(self, serializer: PayloadSerializer, chunker: ChunkingStrategy) -> None:
        """Create the encoder from explicit strategies (dependency injection).

        Args:
            serializer (PayloadSerializer): JSON serialisation strategy.
            chunker (ChunkingStrategy): GATT chunking strategy.

        Returns:
            None: This function does not return a value.
        """
        self._serializer = serializer
        self._chunker = chunker

    @classmethod
    def with_defaults(cls, random_ids: RandomIdProvider | None = None) -> WifiProvisioningEncoder:
        """Build an encoder matching the app: Gson-compatible JSON, 16-byte chunks.

        Args:
            random_ids (RandomIdProvider | None): Random-id provider; defaults to the fixed
                id ``0x1234``.

        Returns:
            WifiProvisioningEncoder: A ready-to-use encoder.
        """
        return cls(GsonCompatSerializer(), TrailerChunker(random_ids or FixedRandomId()))

    def encode(
        self,
        credentials: WifiCredentials,
        *,
        command: WifiCommand = WifiCommand.WIFI_INFO_TRANS_FOR_BINDING,
        send_serial: int = 1,
    ) -> EncodedProvisioning:
        """Encode ``credentials`` into a frame and GATT chunks.

        Args:
            credentials (WifiCredentials): Validated network description.
            command (WifiCommand): ``co`` value (bind or change Wi-Fi).
            send_serial (int): Header ``sendSerial`` (1 for the first request).

        Returns:
            EncodedProvisioning: Every intermediate and final artefact of the encoding.
        """
        payload = WifiPayload.from_credentials(credentials, command)
        json_text = self._serializer.serialize(payload)
        pipeline = EncodingPipeline(
            (BindOrSwitchWifiLayer(), MessageLayer(send_serial=send_serial), FrameLayer()),
        )
        layers = pipeline.run(json_text.encode())
        frame = layers[-1].data
        return EncodedProvisioning(json_text, layers, frame, self._chunker.split(frame))
