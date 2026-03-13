from __future__ import annotations

import argparse
import logging
import random
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .config import AppCfg
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "uwb_agent.yaml"
from .measurements import Measurement
from .sinks import (
    ConsoleMeasurementSink,
    MeasurementPublisher,
    MeasurementSink,
    SourceMetadata,
    ZmqMeasurementSink,
)

log = logging.getLogger(__name__)


def _build_source_metadata(cfg: "AppCfg") -> dict[str, SourceMetadata]:
    metadata: dict[str, SourceMetadata] = {
        "TAG": SourceMetadata(role="tag", source_mac=cfg.tag.mac)
    }
    for anchor in cfg.anchors:
        metadata[f"ANCHOR:{anchor.id}"] = SourceMetadata(
            role="anchor",
            source_mac=anchor.mac,
        )
    return metadata


def _build_sinks(cfg: "AppCfg") -> list[MeasurementSink]:
    sinks: list[MeasurementSink] = []
    if cfg.sinks.enabled:
        sinks.append(ConsoleMeasurementSink())
    if cfg.zmq_sink.enabled:
        sinks.append(
            ZmqMeasurementSink(
                endpoint=cfg.zmq_sink.endpoint,
                topic=cfg.zmq_sink.topic,
                bind=cfg.zmq_sink.bind,
                sndhwm=cfg.zmq_sink.sndhwm,
                linger_ms=cfg.zmq_sink.linger_ms,
            )
        )
        mode = "bind" if cfg.zmq_sink.bind else "connect"
        log.info(
            "Enabled ZMQ measurement sink: %s %s topic=%s",
            mode,
            cfg.zmq_sink.endpoint,
            cfg.zmq_sink.topic,
        )
    return sinks


def _build_simulated_measurement(
    *,
    anchor_id: str,
    anchor_mac: int,
    tag_mac: int,
    idx: int,
    session_handle: int,
    rng: random.Random,
) -> Measurement:
    base_distance = 2.0 + ((anchor_mac & 0xFF) * 0.05)
    jitter = rng.uniform(-0.2, 0.2)
    status = "Ok" if rng.random() > -1 else "RxTimeout"
    distance_m = max(0.1, base_distance + jitter) if status == "Ok" else None
    return Measurement(
        timestamp=time.time(),
        source_id=f"ANCHOR:{anchor_id}",
        session_handle=session_handle,
        idx=idx,
        peer_short_address=tag_mac,
        peer_id="TAG",
        status=status,
        distance_m=distance_m,
    )


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Simulate measurement events without hardware and publish to sinks"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to YAML config file (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=-1.0,
        help="Run duration in seconds. -1 = forever (default: %(default)s)",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=5.0,
        help="Per-anchor output rate in Hz (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Random seed for repeatable outputs (default: %(default)s)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    from .config import load_config

    cfg = load_config(args.config)
    if args.hz <= 0:
        raise ValueError("--hz must be > 0")

    publisher = MeasurementPublisher(
        sinks=_build_sinks(cfg),
        source_metadata=_build_source_metadata(cfg),
    )

    stop = False

    def _sigint_handler(signum: int, frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sigint_handler)

    rng = random.Random(args.seed)
    idx_by_anchor: dict[str, int] = {anchor.id: 0 for anchor in cfg.anchors}
    period_s = 1.0 / args.hz
    start = time.time()

    log.info(
        "Starting simulated measurement stream: anchors=%d hz=%.2f duration=%s",
        len(cfg.anchors),
        args.hz,
        "forever" if args.duration < 0 else args.duration,
    )

    try:
        while not stop:
            now = time.time()
            if args.duration >= 0 and (now - start) >= args.duration:
                break
            tick_started = time.time()
            for anchor in cfg.anchors:
                idx_by_anchor[anchor.id] += 1
                measurement = _build_simulated_measurement(
                    anchor_id=anchor.id,
                    anchor_mac=anchor.mac,
                    tag_mac=cfg.tag.mac,
                    idx=idx_by_anchor[anchor.id],
                    session_handle=cfg.fira.session_id,
                    rng=rng,
                )
                publisher.publish(measurement)
            elapsed = time.time() - tick_started
            time.sleep(max(0.0, period_s - elapsed))
    finally:
        publisher.close()
        log.info("Simulation stopped")


if __name__ == "__main__":
    main()
