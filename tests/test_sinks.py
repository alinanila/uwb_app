from __future__ import annotations

import types

from uwb_app.measurements import Measurement
from uwb_app.sinks import SourceMetadata, ZmqMeasurementSink, build_measurement_event


def test_build_measurement_event_serialization_fields() -> None:
    measurement = Measurement(
        timestamp=123.4,
        source_id="ANCHOR:A",
        session_handle=7,
        idx=5,
        peer_short_address=0x0002,
        peer_id="TAG",
        status="ok",
        distance_m=1.25,
    )
    event = build_measurement_event(
        measurement,
        SourceMetadata(role="anchor", source_mac=0x0001),
    )
    assert event["schema"] == "uwb.measurement"
    assert event["schema_version"] == 1
    assert event["source_id"] == "ANCHOR:A"
    assert event["source_role"] == "anchor"
    assert event["source_mac"] == "0x0001"
    assert event["peer_mac"] == "0x0002"
    assert event["distance_m"] == 1.25


def test_zmq_sink_drops_on_again(monkeypatch) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.send_calls = 0

        def setsockopt(self, option: int, value: int) -> None:
            return

        def bind(self, endpoint: str) -> None:
            return

        def connect(self, endpoint: str) -> None:
            return

        def send_multipart(self, parts: list[bytes], flags: int) -> None:
            self.send_calls += 1
            raise FakeAgain()

        def close(self) -> None:
            return

    class FakeAgain(Exception):
        pass

    class FakeContext:
        @staticmethod
        def instance() -> "FakeContext":
            return FakeContext()

        def socket(self, sock_type: int) -> FakeSocket:
            return FakeSocket()

    fake_zmq = types.SimpleNamespace(
        PUB=1,
        SNDHWM=2,
        LINGER=3,
        NOBLOCK=4,
        Again=FakeAgain,
        Context=FakeContext,
    )

    monkeypatch.setitem(__import__("sys").modules, "zmq", fake_zmq)

    sink = ZmqMeasurementSink(
        endpoint="ipc:///tmp/uwb-events",
        topic="meas",
        bind=True,
        sndhwm=4,
        linger_ms=0,
    )
    sink.publish({}, Measurement(0.0, "TAG", 1, 1, None, None, "ok", None))
    assert sink.drop_count == 1
