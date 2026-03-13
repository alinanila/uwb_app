from __future__ import annotations

import json
import time
import types

import zmq

from uwb_app.hub import MeasurementHub


def test_hub_forwards_payload_unchanged() -> None:
    ctx = zmq.Context.instance()

    upstream_pub = ctx.socket(zmq.PUB)
    upstream_pub.bind("tcp://127.0.0.1:6011")

    hub = MeasurementHub(
        upstream_endpoints=("tcp://127.0.0.1:6011",),
        upstream_topic="meas",
        downstream_endpoint="tcp://127.0.0.1:6012",
        downstream_bind=True,
        rcvhwm=16,
        sndhwm=16,
        linger_ms=0,
    )

    downstream_sub = ctx.socket(zmq.SUB)
    downstream_sub.setsockopt(zmq.SUBSCRIBE, b"meas")
    downstream_sub.connect("tcp://127.0.0.1:6012")

    time.sleep(0.2)
    payload = {"schema": "uwb.measurement", "idx": 99, "source_id": "ANCHOR:A"}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    upstream_pub.send_multipart([b"meas", payload_bytes])

    deadline = time.time() + 2.0
    received: bytes | None = None
    while time.time() < deadline:
        hub.tick(timeout_ms=50)
        try:
            parts = downstream_sub.recv_multipart(flags=zmq.NOBLOCK)
            received = parts[1]
            break
        except zmq.Again:
            continue

    hub.close()
    upstream_pub.close()
    downstream_sub.close()

    assert received == payload_bytes


def test_hub_drop_counter_on_downstream_again(monkeypatch) -> None:
    class FakeAgain(Exception):
        pass

    class FakeSubSocket:
        def __init__(self) -> None:
            self.recv_calls = 0

        def setsockopt(self, option: int, value: object) -> None:
            return

        def connect(self, endpoint: str) -> None:
            return

        def recv_multipart(self, flags: int) -> list[bytes]:
            self.recv_calls += 1
            if self.recv_calls == 1:
                return [b"meas", b'{"idx":1}']
            raise FakeAgain()

        def close(self) -> None:
            return

    class FakePubSocket:
        def setsockopt(self, option: int, value: object) -> None:
            return

        def bind(self, endpoint: str) -> None:
            return

        def connect(self, endpoint: str) -> None:
            return

        def send_multipart(self, parts: list[bytes], flags: int) -> None:
            raise FakeAgain()

        def close(self) -> None:
            return

    class FakeContext:
        @staticmethod
        def instance() -> "FakeContext":
            return FakeContext()

        def socket(self, sock_type: int) -> object:
            if sock_type == 1:
                return FakeSubSocket()
            return FakePubSocket()

    class FakePoller:
        def __init__(self) -> None:
            self._socket = None

        def register(self, socket: object, flags: int) -> None:
            self._socket = socket

        def poll(self, timeout: int) -> list[tuple[object, int]]:
            return [(self._socket, 1)] if self._socket is not None else []

    fake_zmq = types.SimpleNamespace(
        SUB=1,
        PUB=2,
        RCVHWM=3,
        SNDHWM=4,
        LINGER=5,
        SUBSCRIBE=6,
        NOBLOCK=7,
        POLLIN=8,
        Again=FakeAgain,
        Context=FakeContext,
        Poller=FakePoller,
    )
    monkeypatch.setitem(__import__("sys").modules, "zmq", fake_zmq)

    hub = MeasurementHub(
        upstream_endpoints=("tcp://127.0.0.1:1111",),
        upstream_topic="meas",
        downstream_endpoint="tcp://127.0.0.1:2222",
        downstream_bind=True,
        rcvhwm=4,
        sndhwm=4,
        linger_ms=0,
    )
    hub.tick(timeout_ms=1)
    assert hub.drop_count == 1
    hub.close()
