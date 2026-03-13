from __future__ import annotations

import argparse
import json
import logging
import signal
from pathlib import Path
from typing import Optional

from .local_apps_config import load_hub_cfg

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "uwb_hub.yaml"

log = logging.getLogger(__name__)


class MeasurementHub:
    def __init__(
        self,
        *,
        upstream_endpoints: tuple[str, ...],
        upstream_topic: str,
        downstream_endpoint: str,
        downstream_bind: bool,
        rcvhwm: int,
        sndhwm: int,
        linger_ms: int,
    ) -> None:
        import zmq

        if not upstream_endpoints:
            raise ValueError("hub.upstream_endpoints must contain at least one endpoint")

        self._zmq = zmq
        ctx = zmq.Context.instance()

        self._sub = ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, rcvhwm)
        self._sub.setsockopt(zmq.LINGER, linger_ms)
        topic_bytes = upstream_topic.encode("utf-8")
        self._sub.setsockopt(zmq.SUBSCRIBE, topic_bytes)
        for endpoint in upstream_endpoints:
            self._sub.connect(endpoint)

        self._pub = ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, sndhwm)
        self._pub.setsockopt(zmq.LINGER, linger_ms)
        if downstream_bind:
            self._pub.bind(downstream_endpoint)
        else:
            self._pub.connect(downstream_endpoint)

        self._topic = topic_bytes
        self.forwarded = 0
        self.drop_count = 0

    def tick(self, timeout_ms: int = 100) -> None:
        poller = self._zmq.Poller()
        poller.register(self._sub, self._zmq.POLLIN)
        events = dict(poller.poll(timeout=timeout_ms))
        if self._sub not in events:
            return

        while True:
            try:
                parts = self._sub.recv_multipart(flags=self._zmq.NOBLOCK)
            except self._zmq.Again:
                break

            if len(parts) < 2:
                continue

            topic, payload = parts[0], parts[1]
            out_parts = [topic if topic else self._topic, payload]
            try:
                self._pub.send_multipart(out_parts, flags=self._zmq.NOBLOCK)
                self.forwarded += 1
            except self._zmq.Again:
                self.drop_count += 1
                drops = self.drop_count
                if drops == 1 or (drops % 100) == 0:
                    log.warning(
                        "Hub dropping forwarded events due to downstream backpressure (drops=%d)",
                        drops,
                    )

    def close(self) -> None:
        self._sub.close()
        self._pub.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="UWB measurement hub/collector")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to YAML config file (default: %(default)s)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    cfg = load_hub_cfg(args.config)
    if not cfg.enabled:
        log.info("Hub is disabled in config (hub.enabled=false); exiting")
        return

    hub = MeasurementHub(
        upstream_endpoints=cfg.upstream_endpoints,
        upstream_topic=cfg.upstream_topic,
        downstream_endpoint=cfg.downstream_endpoint,
        downstream_bind=cfg.downstream_bind,
        rcvhwm=cfg.rcvhwm,
        sndhwm=cfg.sndhwm,
        linger_ms=cfg.linger_ms,
    )
    mode = "bind" if cfg.downstream_bind else "connect"
    log.info(
        "Hub started: upstream=%s topic=%s downstream=%s %s",
        json.dumps(cfg.upstream_endpoints),
        cfg.upstream_topic,
        mode,
        cfg.downstream_endpoint,
    )

    stop = False

    def _sigint_handler(signum: int, frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        while not stop:
            hub.tick(timeout_ms=100)
    finally:
        hub.close()
        log.info("Hub stopped: forwarded=%d dropped=%d", hub.forwarded, hub.drop_count)


if __name__ == "__main__":
    main()
