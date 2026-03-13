from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .local_apps_config import (
    LayoutCfg,
    LocalizerCfg,
    load_layout_cfg,
    load_localizer_cfg,
    load_yaml_mapping,
    parse_layout_cfg,
)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "uwb_localizer.yaml"

log = logging.getLogger(__name__)


@dataclass
class RoundState:
    first_seen_mono: float
    last_seen_mono: float
    representative_timestamp: float | None = None
    source_idxs: dict[str, int] = field(default_factory=dict)
    measurements: dict[str, float] = field(default_factory=dict)


def _solve_2d_position(
    anchor_positions: dict[str, tuple[float, float]],
    distances: dict[str, float],
    *,
    max_iterations: int = 8,
) -> tuple[float, float] | None:
    usable = [
        (anchor_positions[source_id], distance)
        for source_id, distance in distances.items()
        if source_id in anchor_positions and distance > 0
    ]
    if len(usable) < 3:
        return None

    x = sum(position[0] for position, _ in usable) / len(usable)
    y = sum(position[1] for position, _ in usable) / len(usable)

    for _ in range(max_iterations):
        h11 = h12 = h22 = 0.0
        g1 = g2 = 0.0
        for (ax, ay), measured in usable:
            dx = x - ax
            dy = y - ay
            predicted = math.hypot(dx, dy)
            if predicted < 1e-9:
                continue
            residual = predicted - measured
            jx = dx / predicted
            jy = dy / predicted
            h11 += jx * jx
            h12 += jx * jy
            h22 += jy * jy
            g1 += jx * residual
            g2 += jy * residual

        det = (h11 * h22) - (h12 * h12)
        if abs(det) < 1e-12:
            return None

        inv11 = h22 / det
        inv12 = -h12 / det
        inv22 = h11 / det
        step_x = (inv11 * g1) + (inv12 * g2)
        step_y = (inv12 * g1) + (inv22 * g2)
        x -= step_x
        y -= step_y

        if math.hypot(step_x, step_y) < 1e-6:
            break

    return (x, y)


class PosePublisher:
    def __init__(self, cfg: LocalizerCfg) -> None:
        import zmq

        self._zmq = zmq
        self._topic = cfg.pose_sink.topic.encode("utf-8")
        self._socket = zmq.Context.instance().socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, cfg.pose_sink.sndhwm)
        self._socket.setsockopt(zmq.LINGER, cfg.pose_sink.linger_ms)
        if cfg.pose_sink.bind:
            self._socket.bind(cfg.pose_sink.endpoint)
        else:
            self._socket.connect(cfg.pose_sink.endpoint)
        self.drop_count = 0

    def publish(self, event: dict[str, object]) -> None:
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        try:
            self._socket.send_multipart([self._topic, payload], flags=self._zmq.NOBLOCK)
        except self._zmq.Again:
            self.drop_count += 1
            if self.drop_count == 1 or (self.drop_count % 100) == 0:
                log.warning(
                    "Pose PUB dropping events due to backpressure (drops=%d)",
                    self.drop_count,
                )

    def close(self) -> None:
        self._socket.close()


class Localizer:
    def __init__(self, cfg: LocalizerCfg, layout: LayoutCfg) -> None:
        import zmq

        self.cfg = cfg
        self.layout = layout
        self._zmq = zmq
        self._sub = zmq.Context.instance().socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, cfg.rcvhwm)
        self._sub.setsockopt(zmq.LINGER, cfg.linger_ms)
        self._sub.setsockopt(zmq.SUBSCRIBE, cfg.subscribe_topic.encode("utf-8"))
        self._sub.connect(cfg.subscribe_endpoint)

        self._poller = zmq.Poller()
        self._poller.register(self._sub, zmq.POLLIN)
        self._rounds: dict[tuple[int, str], list[RoundState]] = {}
        self._round_seq: dict[tuple[int, str], int] = {}
        self._emitted = 0
        self._dropped_incomplete = 0
        self._dropped_bad = 0
        self._publisher = PosePublisher(cfg) if cfg.pose_sink.enabled else None

    def _process_message(self, payload: bytes, now_mono: float) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            self._dropped_bad += 1
            return
        if not isinstance(event, dict):
            self._dropped_bad += 1
            return

        if str(event.get("status", "")) != "Ok":
            return

        source_id = str(event.get("source_id", ""))
        if source_id not in self.layout.anchors:
            return

        peer_id = event.get("peer_id")
        peer_label = str(peer_id) if peer_id is not None else str(event.get("peer_mac", "unknown"))

        distance_raw = event.get("distance_m")
        if not isinstance(distance_raw, (int, float)):
            return
        distance_m = float(distance_raw)

        session_handle = int(event.get("session_handle", -1))
        source_idx = int(event.get("idx", -1))
        key = (session_handle, peer_label)

        states = self._rounds.setdefault(key, [])
        state = next(
            (
                candidate
                for candidate in states
                if (now_mono - candidate.last_seen_mono) <= self.cfg.round_join_window_s
            ),
            None,
        )
        if state is None:
            state = RoundState(first_seen_mono=now_mono, last_seen_mono=now_mono)
            states.append(state)
        else:
            state.last_seen_mono = now_mono

        timestamp_raw = event.get("timestamp")
        if isinstance(timestamp_raw, (int, float)):
            state.representative_timestamp = float(timestamp_raw)

        state.measurements[source_id] = distance_m
        state.source_idxs[source_id] = source_idx

        if self.cfg.total_anchors is not None and len(state.measurements) >= self.cfg.total_anchors:
            self._emit_round(key, state, event)

    def _emit_round(
        self,
        key: tuple[int, str],
        state: RoundState,
        exemplar_event: dict[str, object],
    ) -> None:
        solved = _solve_2d_position(self.layout.anchors, state.measurements)
        if solved is None:
            self._dropped_incomplete += 1
            self._remove_state(key, state)
            return

        x_m, y_m = solved
        out_event: dict[str, object] = {
            "schema": "uwb.pose",
            "schema_version": 1,
            "timestamp": state.representative_timestamp
            if state.representative_timestamp is not None
            else float(exemplar_event.get("timestamp", time.time())),
            "published_timestamp": time.time(),
            "session_handle": key[0],
            "round_seq": self._next_round_seq(key),
            "peer_id": key[1],
            "x_m": x_m,
            "y_m": y_m,
            "anchors_used": sorted(state.measurements.keys()),
            "n_anchors": len(state.measurements),
            "debug": {
                "source_idxs": {
                    source_id: state.source_idxs[source_id]
                    for source_id in sorted(state.source_idxs.keys())
                }
            },
        }
        self._emitted += 1
        if self.cfg.console:
            print(
                "POSE "
                f"peer={out_event['peer_id']} round_seq={out_event['round_seq']} "
                f"x={x_m:.3f} y={y_m:.3f} n={out_event['n_anchors']}"
            )
        if self._publisher is not None:
            self._publisher.publish(out_event)
        self._remove_state(key, state)

    def _next_round_seq(self, key: tuple[int, str]) -> int:
        seq = self._round_seq.get(key, 0) + 1
        self._round_seq[key] = seq
        return seq

    def _remove_state(self, key: tuple[int, str], state: RoundState) -> None:
        states = self._rounds.get(key)
        if not states:
            return
        try:
            states.remove(state)
        except ValueError:
            return
        if not states:
            self._rounds.pop(key, None)

    def _expire_rounds(self, now_mono: float) -> None:
        stale: list[tuple[tuple[int, str], RoundState]] = []
        very_stale: list[tuple[tuple[int, str], RoundState]] = []
        for key, states in list(self._rounds.items()):
            for state in list(states):
                age = now_mono - state.first_seen_mono
                if age > self.cfg.max_round_age_s:
                    very_stale.append((key, state))
                    continue

                if age >= self.cfg.batch_timeout_s:
                    if len(state.measurements) >= self.cfg.min_anchors:
                        self._emit_round(key, state, {})
                    else:
                        stale.append((key, state))

        for key, state in stale:
            self._dropped_incomplete += 1
            self._remove_state(key, state)

        # Guardrail in case a round stalls forever.
        for key, state in very_stale:
            self._dropped_incomplete += 1
            self._remove_state(key, state)

    def tick(self, timeout_ms: int = 100) -> None:
        now_mono = time.monotonic()
        events = dict(self._poller.poll(timeout=timeout_ms))
        if self._sub in events:
            while True:
                try:
                    parts = self._sub.recv_multipart(flags=self._zmq.NOBLOCK)
                except self._zmq.Again:
                    break
                if len(parts) < 2:
                    continue
                self._process_message(parts[1], now_mono)
        self._expire_rounds(time.monotonic())

    def close(self) -> None:
        if self._publisher is not None:
            self._publisher.close()
        self._sub.close()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="UWB 2D localizer from measurement stream")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to YAML config file (default: %(default)s)",
    )
    parser.add_argument(
        "--layout",
        type=Path,
        default=None,
        help="Optional explicit layout file (overrides localizer.layout_path)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    cfg = load_localizer_cfg(args.config)
    if not cfg.enabled:
        log.info("Localizer is disabled in config (localizer.enabled=false); exiting")
        return

    if args.layout is not None:
        layout = load_layout_cfg(args.layout)
    elif cfg.layout_path is not None:
        layout = load_layout_cfg(args.config.parent / cfg.layout_path)
    else:
        layout = parse_layout_cfg(load_yaml_mapping(args.config))

    localizer = Localizer(cfg, layout)
    log.info(
        "Localizer started: subscribe=%s topic=%s anchors=%d",
        cfg.subscribe_endpoint,
        cfg.subscribe_topic,
        len(layout.anchors),
    )

    stop = False

    def _sigint_handler(signum: int, frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        while not stop:
            localizer.tick(timeout_ms=100)
    finally:
        localizer.close()
        log.info("Localizer stopped")


if __name__ == "__main__":
    main()
