from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class HubCfg:
    enabled: bool = False
    upstream_endpoints: tuple[str, ...] = ()
    upstream_topic: str = "meas"
    downstream_endpoint: str = "tcp://127.0.0.1:5560"
    downstream_bind: bool = True
    rcvhwm: int = 64
    sndhwm: int = 64
    linger_ms: int = 0


@dataclass(frozen=True)
class PoseSinkCfg:
    enabled: bool = False
    endpoint: str = "tcp://127.0.0.1:5561"
    bind: bool = True
    topic: str = "pose"
    sndhwm: int = 32
    linger_ms: int = 0


@dataclass(frozen=True)
class LocalizerCfg:
    enabled: bool = False
    subscribe_endpoint: str = "tcp://127.0.0.1:5556"
    subscribe_topic: str = "meas"
    rcvhwm: int = 128
    linger_ms: int = 0
    batch_timeout_s: float = 0.25
    round_join_window_s: float = 0.06
    max_round_age_s: float = 1.0
    min_anchors: int = 4
    total_anchors: int | None = None
    console: bool = True
    layout_path: str | None = None
    pose_sink: PoseSinkCfg = PoseSinkCfg()


@dataclass(frozen=True)
class LayoutCfg:
    anchors: dict[str, tuple[float, float]]


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root for {path} must be a mapping")
    return data


def _section_or_root(data: dict[str, Any], section: str) -> dict[str, Any]:
    scoped = data.get(section)
    if isinstance(scoped, dict):
        return scoped
    return data


def load_hub_cfg(path: Path) -> HubCfg:
    data = load_yaml_mapping(path)
    hub_in = _section_or_root(data, "hub")
    upstream = hub_in.get("upstream_endpoints", []) or []
    return HubCfg(
        enabled=bool(hub_in.get("enabled", False)),
        upstream_endpoints=tuple(str(endpoint) for endpoint in upstream),
        upstream_topic=str(hub_in.get("upstream_topic", "meas")),
        downstream_endpoint=str(
            hub_in.get("downstream_endpoint", "tcp://127.0.0.1:5560")
        ),
        downstream_bind=bool(hub_in.get("downstream_bind", True)),
        rcvhwm=int(hub_in.get("rcvhwm", 64)),
        sndhwm=int(hub_in.get("sndhwm", 64)),
        linger_ms=int(hub_in.get("linger_ms", 0)),
    )


def load_localizer_cfg(path: Path) -> LocalizerCfg:
    data = load_yaml_mapping(path)
    loc_in = _section_or_root(data, "localizer")
    pose_in = loc_in.get("pose_zmq", {}) or {}
    return LocalizerCfg(
        enabled=bool(loc_in.get("enabled", False)),
        subscribe_endpoint=str(loc_in.get("subscribe_endpoint", "tcp://127.0.0.1:5556")),
        subscribe_topic=str(loc_in.get("subscribe_topic", "meas")),
        rcvhwm=int(loc_in.get("rcvhwm", 128)),
        linger_ms=int(loc_in.get("linger_ms", 0)),
        batch_timeout_s=float(loc_in.get("batch_timeout_s", 0.25)),
        round_join_window_s=float(loc_in.get("round_join_window_s", 0.06)),
        max_round_age_s=float(loc_in.get("max_round_age_s", 1.0)),
        min_anchors=max(3, int(loc_in.get("min_anchors", 4))),
        total_anchors=(
            max(1, int(loc_in["total_anchors"]))
            if loc_in.get("total_anchors") is not None
            else None
        ),
        console=bool(loc_in.get("console", True)),
        layout_path=(str(loc_in["layout_path"]) if "layout_path" in loc_in else None),
        pose_sink=PoseSinkCfg(
            enabled=bool(pose_in.get("enabled", False)),
            endpoint=str(pose_in.get("endpoint", "tcp://127.0.0.1:5561")),
            bind=bool(pose_in.get("bind", True)),
            topic=str(pose_in.get("topic", "pose")),
            sndhwm=int(pose_in.get("sndhwm", 32)),
            linger_ms=int(pose_in.get("linger_ms", 0)),
        ),
    )


def load_layout_cfg(path: Path) -> LayoutCfg:
    data = load_yaml_mapping(path)
    return parse_layout_cfg(data)


def parse_layout_cfg(data: dict[str, Any]) -> LayoutCfg:
    layout_in = data.get("layout", data)
    anchors_in = layout_in.get("anchors", {}) if isinstance(layout_in, dict) else {}
    if not isinstance(anchors_in, dict):
        raise ValueError("layout.anchors must be a mapping")

    anchors: dict[str, tuple[float, float]] = {}
    for source_id, pos in anchors_in.items():
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise ValueError(f"Anchor {source_id} must be [x, y]")
        anchors[str(source_id)] = (float(pos[0]), float(pos[1]))

    if len(anchors) < 3:
        raise ValueError("Layout must define at least 3 anchors")
    return LayoutCfg(anchors=anchors)
