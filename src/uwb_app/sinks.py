from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import sys
import threading
import time
from typing import Optional, Protocol

from .measurements import Measurement, format_measurement


log = logging.getLogger(__name__)


class MeasurementSink(Protocol):
    def publish(self, event: dict[str, object], measurement: Measurement) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SourceMetadata:
    role: Optional[str]
    source_mac: Optional[int]


def build_measurement_event(
    measurement: Measurement,
    source_metadata: Optional[SourceMetadata],
) -> dict[str, object]:
    source_mac = source_metadata.source_mac if source_metadata is not None else None
    role = source_metadata.role if source_metadata is not None else None
    event: dict[str, object] = {
        "schema": "uwb.measurement",
        "schema_version": 1,
        "timestamp": measurement.timestamp,
        "published_timestamp": time.time(),
        "source_id": measurement.source_id,
        "source_role": role,
        "source_mac": f"0x{source_mac:04X}" if source_mac is not None else None,
        "session_handle": measurement.session_handle,
        "idx": measurement.idx,
        "status": measurement.status,
        "distance_m": measurement.distance_m,
        "peer_id": measurement.peer_id,
        "peer_mac": (
            f"0x{measurement.peer_short_address:04X}"
            if measurement.peer_short_address is not None
            else None
        ),
    }
    return event


class ConsoleMeasurementSink:
    def publish(self, event: dict[str, object], measurement: Measurement) -> None:
        print(format_measurement(measurement))

    def close(self) -> None:
        return


class ZmqMeasurementSink:
    def __init__(
        self,
        *,
        endpoint: str,
        topic: str,
        bind: bool,
        sndhwm: int,
        linger_ms: int,
    ) -> None:
        import zmq

        self._zmq = zmq
        if endpoint.startswith("ipc://") and sys.platform.startswith("win"):
            log.warning(
                "IPC endpoint %s requested on Windows; pyzmq IPC support is often unavailable. "
                "Use tcp://127.0.0.1:5556 (or another TCP endpoint) instead.",
                endpoint,
            )

        self._socket = zmq.Context.instance().socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, sndhwm)
        self._socket.setsockopt(zmq.LINGER, linger_ms)
        if bind:
            self._socket.bind(endpoint)
        else:
            self._socket.connect(endpoint)
        self._topic = topic.encode("utf-8")
        self.drop_count = 0
        self._lock = threading.Lock()

    def publish(self, event: dict[str, object], measurement: Measurement) -> None:
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        try:
            self._socket.send_multipart(
                [self._topic, payload],
                flags=self._zmq.NOBLOCK,
            )
        except self._zmq.Again:
            with self._lock:
                self.drop_count += 1
                drops = self.drop_count
            if drops == 1 or (drops % 100) == 0:
                log.warning("ZMQ sink dropping events due to backpressure (drops=%d)", drops)

    def close(self) -> None:
        self._socket.close()


class MeasurementPublisher:
    def __init__(
        self,
        sinks: list[MeasurementSink],
        source_metadata: dict[str, SourceMetadata],
    ) -> None:
        self._sinks = sinks
        self._source_metadata = source_metadata

    def publish(self, measurement: Measurement) -> None:
        metadata = self._source_metadata.get(measurement.source_id)
        event = build_measurement_event(measurement, metadata)
        for sink in self._sinks:
            try:
                sink.publish(event, measurement)
            except Exception as exc:
                log.warning("sink publish failed (%s): %s", type(sink).__name__, exc)

    def close(self) -> None:
        for sink in self._sinks:
            try:
                sink.close()
            except Exception as exc:
                log.warning("sink close failed (%s): %s", type(sink).__name__, exc)

