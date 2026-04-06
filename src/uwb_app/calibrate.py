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
    """accumulate distances for window averaging — tracked per anchor"""
    counts: Dict[str, int] = field(default_factory=dict)
    sum_dists: Dict[str, float] = field(default_factory=dict)

    def add(self, source_id: str, dist: float) -> None:
        self.counts[source_id] = self.counts.get(source_id, 0) + 1  # per-anchor count
        self.sum_dists[source_id] = self.sum_dists.get(source_id, 0.0) + dist

    def averages(self) -> Dict[str, float]:
        return {
            k: self.sum_dists[k] / self.counts[k]  # divide by per-anchor count
            for k in self.sum_dists
            if self.counts.get(k, 0) > 0
        }

def collect_distances(
    endpoint: str,
    topic: str,
    duration_s: float,
    peer_id: str,
    expected_ids: set[str] | None = None,
    settle_threshold_m: float = 0.05,  # max std dev to consider stable
    settle_window: int = 20,           # number of readings to check stability over
) -> Dict[str, float]:
    """
    wait for distances to stabilise before averaging
    """
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(endpoint)
    sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))

    # settling
    print("  waiting for tag to settle...")
    recent: Dict[str, list] = {}
    settled = False

    while not settled:
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

        buf = recent.setdefault(source_id, [])
        buf.append(dist_m)
        if len(buf) > settle_window:
            buf.pop(0)

        # check all anchors have settled
        if expected_ids:
            ready = all(len(recent.get(a, [])) >= settle_window for a in expected_ids)
        else:
            ready = all(len(v) >= settle_window for v in recent.values()) and len(recent) > 0

        if ready:
            vals = [recent[a] for a in (expected_ids or recent.keys())]
            stds = [
                (sum((x - sum(v)/len(v))**2 for x in v) / len(v)) ** 0.5
                for v in vals
            ]

            if all(s < settle_threshold_m for s in stds):
                settled = True
                print("  tag settled, accumulating...")

    # accumulating
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

    avgs = acc.averages()
    if expected_ids:
        missing = sorted(a for a in expected_ids if a not in avgs)
        if missing:
            raise RuntimeError(f"missing expected anchors: {missing}")
        
    return avgs


def solve_bilateration(
    D_x: float, 
    dist_A: float, 
    dist_D: float
) -> tuple[float, float, float]:        # if doing 3d, three floats in the tuple
    """
    solve for x,y (anchor C specifically)
    assuming A = (0,0) and D = (D_x,0) to define the x-axis
    making the assumption that anchors B and C are above the x-axis by taking positive sqrt
    """
    x = (D_x**2 + dist_A**2 - dist_D**2) / (2.0 * D_x)
    y_sq = dist_A**2 - x**2
    if y_sq < 0:
        y_sq = 0.0
    y = math.sqrt(y_sq)
    z = 0.0
    return x, y, z
    # return x, y 


def solve_trilateration(
    D_x: float,
    C_x: float,
    C_y: float,
    dist_A: float,
    dist_D: float,
    dist_C: float,
) -> tuple[float, float, float]:
    """
    solve for x,y,z (anchor B specifically)
    assuming A = (0,0,0), D = (D_x,0,0) and C = (C_x,C_y,0) to define the xy-plane
    making the assumption that anchor B is above the xy-plane by taking positive sqrt
    requires anchor B to not be coplanar with other three
    """
    x = (D_x**2 + dist_A**2 - dist_D**2) / (2.0 * D_x)
    y = (C_x**2 + C_y**2 - 2.0 * C_x * x + dist_A**2 - dist_C**2) / (2.0 * C_y)
    z_sq = dist_A**2 - x**2 - y**2
    if z_sq < 0:
        z_sq = 0.0
    z = math.sqrt(z_sq)
    return x, y, z


def update_layout(
    config_path: Path,
    # anchors_out: Dict[str, tuple[float, float]],
    anchors_out: Dict [str, tuple[float, float, float]],
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
    # for source_id, (x, y) in anchors_out.items():
    #     anchors[source_id] = [float(x), float(y)]

    for source_id, (x, y, z) in anchors_out.items():
        anchors[source_id] = [float(x), float(y), float(z)]

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
        default=15.0,
        help="averaging window in seconds at each calibration position (default: %(default)s)",
    )
    args = parser.parse_args()


    ### calibration

    print("anchor calibration: make sure tag is powered and agents & hub running")
    print()

    # A (0,0)
    input("place tag on anchor A and press enter")
    dists_Apos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id
    )
    print("distances at anchor A:", dists_Apos)
    # A = (0.0, 0.0)
    A = (0.0, 0.0, 0.0)

    # D (dist, 0) (defining the x-axis)
    input("place tag at anchor D and press enter")
    dists_Dpos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id, {"ANCHOR:A"}
    )
    print("distances at anchor D:", dists_Dpos)
    # only measure from A
    dist_AD = dists_Dpos.get("ANCHOR:A")
    if dist_AD is None:
        raise RuntimeError(
            "no distance from anchor A when tag at anchor D; check IDs/config"
        )
    # D = (dist_AD, 0.0)
    D = (dist_AD, 0.0, 0.0)

    # solve for C from A and D
    input("place tag at anchor C and press enter")
    dists_Cpos = collect_distances(
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id, {"ANCHOR:A", "ANCHOR:D"}
    )
    print("distances at anchor C:", dists_Cpos)
    # measure from A and D
    dist_AC = dists_Cpos.get("ANCHOR:A")
    dist_DC = dists_Cpos.get("ANCHOR:D")
    if dist_AC is None or dist_DC is None:
        raise RuntimeError(
            "missing distance from anchor A and/or D; check IDs/config"
        )

    C = solve_bilateration(D_x=D[0], dist_A=dist_AC, dist_D=dist_DC)

    # solve for B from A and D (same as C)
    input("place tag at anchor B and press enter")
    dists_Bpos = collect_distances(
        # args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id, {"ANCHOR:A", "ANCHOR:D"}
        args.hub_endpoint, args.topic, args.avg_seconds, args.peer_id, {"ANCHOR:A", "ANCHOR:D", "ANCHOR:C"}
    )
    print("distances at anchor B:", dists_Bpos)
    # measure from A and D
    dist_AB = dists_Bpos.get("ANCHOR:A")
    dist_DB = dists_Bpos.get("ANCHOR:D")
    dist_CB = dists_Bpos.get("ANCHOR:C")
    if dist_AB is None or dist_DB is None or dist_CB is None:
        raise RuntimeError(
            "missing distance from anchor A and/or D and/or C; check IDs/config"
        )
    
    # B = solve_bilateration(D_x=D[0], dist_A=dist_AB, dist_D=dist_DB)
    B = solve_trilateration(D_x=D[0], C_x=C[0], C_y=C[1], dist_A=dist_AB, dist_D=dist_DB, dist_C=dist_CB)

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
