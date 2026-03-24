from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import zmq
import yaml

from .local_apps_config import (
    load_localizer_cfg, 
    load_yaml_mapping,
)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_LOCALIZER_CONFIG = PROJECT_ROOT / "config" / "uwb_localizer.yaml"


@dataclass
class AnchorDistances:
    """accumulate distances for window averaging"""
    count: int = 0
    sum_dists: Dict[str, float] = field(default_factory=dict)

    def add(self, source_id: str, dist: float) -> None:
        self.count += 1
        self.sum_dists[source_id] = self.sum_dists.get(source_id, 0.0) + dist

    def averages(self) -> Dict[str, float]:
        if self.count == 0:
            return {}
        return {k: v / self.count for k, v in self.sum_dists.items()}


def collect_distances(
    endpoint: str,
    topic: str,
    duration_s: float,
    peer_id: str,
) -> Dict[str, float]:
    """
    collect average distances for each anchor to the tag in given duration
    subscribe to 'meas' topic, make sure status=ok
    return avg dist in m for anchor
    """
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(endpoint)
    sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))

    acc = AnchorDistances()
    deadline = time.monotonic() + duration_s

    try:
        while time.monotonic() < deadline:
            try:
                parts = sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                time.sleep(0.01)
                continue

            if len(parts) < 2:
                continue
            _, payload = parts

            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if str(event.get("status", "")) != "Ok":
                continue
            if str(event.get("peer_id", "")) != peer_id:
                continue

            source_id = str(event.get("source_id", ""))
            dist_raw = event.get("distance_m")
            if not isinstance(dist_raw, (int, float)):
                continue

            dist_m = float(dist_raw)
            if dist_m <= 0:
                continue

            acc.add(source_id, dist_m)
    finally:
        sub.close()

    return acc.averages()


def solve_bilateration(D_x: float, dist_A: float, dist_D: float) -> tuple[float, float]:
    """
    assuming A = (0,0) and D = (D_x,0), solve for position of anchor B or C with tag held over it
    making the assumption that anchors B and C are 'above' the x-axis by taking positive sqrt
    """
    x = (D_x**2 + dist_A**2 - dist_D**2) / (2.0 * D_x)
    y_sq = dist_A**2 - x**2
    if y_sq < 0:
        y_sq = 0.0
    y = math.sqrt(y_sq)
    return x, y


def update_layout(
    config_path: Path,
    anchors_out: Dict[str, tuple[float, float]],
) -> None:
    """
    update anchor layout in uwb_localizer.yaml with calibrated layout
    """
    cfg = load_localizer_cfg(config_path)

    # if you wanna make a separate layout yaml, would call from that file
    # otherwise calls the layout from localizer yaml
    if cfg.layout_path is not None:
        layout_file = config_path.parent / cfg.layout_path
        data = load_yaml_mapping(layout_file)
        target_path = layout_file
    else:
        data = load_yaml_mapping(config_path)
        target_path = config_path

    layout = data.setdefault("layout", {})
    anchors = layout.setdefault("anchors", {})

    # set new anchor positions
    for source_id, (x, y) in anchors_out.items():
        anchors[source_id] = [float(x), float(y)]

    with target_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)

    print(f"updated layout in {target_path} with calibrated anchor positions")


def main() -> None:
    ### parsing
    parser = argparse.ArgumentParser(description="anchor self-calibration")
    parser.add_argument(
        "--hub-endpoint",
        default="tcp://127.0.0.1:5560",
        help="ZMQ endpoint where hub publishes meas (default: %(default)s)",
    )
    parser.add_argument(
        "--topic",
        default="meas",
        help="ZMQ topic for measurements (default: %(default)s)",
    )
    parser.add_argument(
        "--peer-id",
        default="TAG",
        help="tag peer_id used for calibration (default: %(default)s)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_LOCALIZER_CONFIG,
        help="path to uwb_localizer.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--avg-seconds",
        type=float,
        default=10.0,
        help="averaging window in seconds at each calibration position (default: %(default)s)",
    )
    args = parser.parse_args()


    ### calibration

    print("anchor calibration: make sure tag is powered and agents & hub running")
    print()

    # A (0,0)
    input("place tag on anchor 1 and press enter")
    dists_Apos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id
    )
    print("distances at anchor 1:", dists_Apos)
    A = (0.0, 0.0)

    # D (dist, 0) (defining the x-axis)
    input("place tag at anchor 2 and press enter")
    dists_Dpos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id
    )
    print("distances at anchor 2:", dists_Dpos)
    # only measure from A
    dist_AD = dists_Dpos.get("ANCHOR:A")
    if dist_AD is None:
        raise RuntimeError(
            "no distance from anchor 1 when tag at anchor 2; check IDs/config"
        )
    D = (dist_AD, 0.0)

    # solve for C from A and D
    input("place tag at anchor 3 and press enter")
    dists_Cpos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id
    )
    print("distances at anchor 3:", dists_Cpos)
    # measure from A and D
    dist_AC = dists_Cpos.get("ANCHOR:A")
    dist_DC = dists_Cpos.get("ANCHOR:D")
    if dist_AC is None or dist_DC is None:
        raise RuntimeError(
            "missing distance from anchor 1 and/or 2; check IDs/config"
        )

    C = solve_bilateration(D_x=D[0], dist_A=dist_AC, dist_D=dist_DC)

    # solve for B from A and D (same as C)
    input("place tag at anchor 4 and press enter")
    dists_Bpos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id
    )
    print("distances at anchor 4:", dists_Bpos)
    # measure from A and D
    dist_AB = dists_Bpos.get("ANCHOR:A")
    dist_DB = dists_Bpos.get("ANCHOR:D")
    if dist_AB is None or dist_DB is None:
        raise RuntimeError(
            "missing distance from anchor 1 and/or 2; check IDs/config"
        )
    
    B = solve_bilateration(D_x=D[0], dist_A=dist_AB, dist_D=dist_DB)

    print("\n calibrated anchor positions:")
    print(f"ANCHOR:A = {A}")
    print(f"ANCHOR:D = {D}")
    print(f"ANCHOR:C = {C}")
    print(f"ANCHOR:B = {B}")

    anchors_out = {
        "ANCHOR:A": A,
        "ANCHOR:B": B,
        "ANCHOR:C": C,
        "ANCHOR:D": D,
    }
    update_layout(args.config, anchors_out)

    print("\n calibration complete")
    print("restart uwb-localize to apply new layout:")
    print("  sudo systemctl restart uwb-localize")
    print()
    

if __name__ == "__main__":
    main()