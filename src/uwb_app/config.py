from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml


class TopologyMode(str, Enum):
    """Topology modes supported by the demo.

    tag_initiates_anchors_respond: Tag is controller/initiator, anchors respond.
    anchors_initiate_tag_responds: Anchors are controllers/initiators, tag responds.
    """

    ANCHORS_INITIATE_TAG_RESPONDS = "anchors_initiate_tag_responds"
    TAG_INITIATES_ANCHORS_RESPOND = "tag_initiates_anchors_respond"


@dataclass(frozen=True)
class AnchorCfg:
    id: str
    port: str
    mac: int


@dataclass(frozen=True)
class TagCfg:
    mac: int
    port: Optional[str] = None
    connect: bool = False


@dataclass(frozen=True)
class FiraCfg:
    session_id: int = 42
    channel: int = 9
    round: str = "ds-deferred"
    schedule: str = "time"
    sts: str = "static"
    vendor_id: Optional[int] = None
    static_sts_iv: Optional[int] = None
    frame: str = "sp3"
    report: str = "tof|azimuth|fom"
    slot_duration: int = 2400
    ranging_interval: int = 200
    slots_per_rr: int = 25
    hopping_mode: str = "disabled"
    aoa_report: str = "all-enabled"
    max_measurements: int = 0
    rssi_reporting: bool = False
    enable_diagnostics: bool = False
    diag_fields: str = "metrics|aoa|cfo"
    multi_node_mode: str = "unicast"
    n_controlees: Optional[int] = None
    prfset: Optional[str] = None
    prf_mode: Optional[str] = None
    preamble_code_index: Optional[int] = None
    sfd_id: Optional[int] = None
    psdu_data_rate: Optional[int] = None
    bprf_phr_data_rate: Optional[int] = None
    sts_length: Optional[int] = None
    number_of_sts_segments: Optional[int] = None
    selected_uwb_config_id: Optional[int] = None


class ListenMode(str, Enum):
    ANCHORS = "anchors"
    TAG = "tag"
    BOTH = "both"


@dataclass(frozen=True)
class DedupCfg:
    enabled: bool = False
    window_s: float = 0.25


@dataclass(frozen=True)
class SinkCfg:
    enabled: bool = True


@dataclass(frozen=True)
class ZmqSinkCfg:
    enabled: bool = False
    endpoint: str = "tcp://127.0.0.1:5556"
    bind: bool = True
    topic: str = "meas"
    sndhwm: int = 32
    linger_ms: int = 0


@dataclass(frozen=True)
class AppCfg:
    topology: TopologyMode
    listen: ListenMode
    fira: FiraCfg
    tag: TagCfg
    anchors: list[AnchorCfg]
    dedup: DedupCfg
    sinks: SinkCfg
    zmq_sink: ZmqSinkCfg


def _as_int(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 0)
    raise TypeError(f"Expected int-like value, got {type(v)}: {v!r}")


def _parse_hex_bytes(value: str, *, length: int, label: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a colon-delimited hex string")
    parts = value.split(":")
    if len(parts) != length:
        raise ValueError(f"{label} must have {length} bytes, got {len(parts)}")
    try:
        parsed = [int(part, 16) for part in parts]
    except ValueError as exc:
        raise ValueError(f"{label} must be hex bytes like 01:02") from exc
    if any(byte < 0 or byte > 0xFF for byte in parsed):
        raise ValueError(f"{label} contains out-of-range byte values")
    return bytes(parsed)


def _validate_static_value(value: int, *, length: int, label: str) -> int:
    if value < 0:
        raise ValueError(f"{label} must be non-negative, got {value}")
    max_value = (1 << (length * 8)) - 1
    if value > max_value:
        raise ValueError(f"{label} must fit in {length} bytes, got {value:#x}")
    return value


def _parse_vendor_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and ":" in value:
        parsed = _parse_hex_bytes(value, length=2, label="fira.vendor_id")
        if parsed[0] <= parsed[1]:
            return _validate_static_value(
                int.from_bytes(parsed, "big"), length=2, label="fira.vendor_id"
            )
        return _validate_static_value(
            int.from_bytes(parsed, "little"), length=2, label="fira.vendor_id"
        )
    parsed_int = _optional_int(value, label="fira.vendor_id")
    if parsed_int is None:
        return None
    return _validate_static_value(parsed_int, length=2, label="fira.vendor_id")


def _parse_static_sts_iv(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and ":" in value:
        parsed = _parse_hex_bytes(value, length=6, label="fira.static_sts_iv")
        return _validate_static_value(
            int.from_bytes(parsed, "little"), length=6, label="fira.static_sts_iv"
        )
    parsed_int = _optional_int(value, label="fira.static_sts_iv")
    if parsed_int is None:
        return None
    return _validate_static_value(parsed_int, length=6, label="fira.static_sts_iv")


def _optional_int(value: Any, *, label: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an int, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as exc:
            raise ValueError(f"{label} must be an int-like string") from exc
    raise TypeError(f"{label} must be an int, got {type(value)}: {value!r}")


def _optional_str(value: Any, *, label: str) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise TypeError(f"{label} must be a string, got {type(value)}: {value!r}")


def load_config(path: Path) -> AppCfg:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config root must be a mapping")

    topology = TopologyMode(
        str(data.get("mode", TopologyMode.TAG_INITIATES_ANCHORS_RESPOND.value))
    )
    listen = ListenMode(str(data.get("listen", ListenMode.ANCHORS.value)))

    fira_in = data.get("fira", {}) or {}
    fira = FiraCfg(
        session_id=int(fira_in.get("session_id", 42)),
        channel=int(fira_in.get("channel", 9)),
        round=str(fira_in.get("round", "ds-deferred")),
        schedule=str(fira_in.get("schedule", "time")),
        sts=str(fira_in.get("sts", "static")),
        vendor_id=_parse_vendor_id(fira_in.get("vendor_id")),
        static_sts_iv=_parse_static_sts_iv(fira_in.get("static_sts_iv")),
        frame=str(fira_in.get("frame", "sp3")),
        report=str(fira_in.get("report", "tof|azimuth|fom")),
        slot_duration=int(fira_in.get("slot_duration", 2400)),
        ranging_interval=int(fira_in.get("ranging_interval", 200)),
        slots_per_rr=int(fira_in.get("slots_per_rr", 25)),
        hopping_mode=str(fira_in.get("hopping_mode", "disabled")),
        aoa_report=str(fira_in.get("aoa_report", "all-enabled")),
        max_measurements=int(fira_in.get("max_measurements", 0)),
        rssi_reporting=bool(fira_in.get("rssi_reporting", False)),
        enable_diagnostics=bool(fira_in.get("enable_diagnostics", False)),
        diag_fields=str(fira_in.get("diag_fields", "metrics|aoa|cfo")),
        multi_node_mode=str(fira_in.get("multi_node_mode", "unicast")),
        n_controlees=(
            int(fira_in["n_controlees"]) if "n_controlees" in fira_in else None
        ),
        prfset=_optional_str(fira_in.get("prfset"), label="fira.prfset"),
        prf_mode=_optional_str(fira_in.get("prf_mode"), label="fira.prf_mode"),
        preamble_code_index=_optional_int(
            fira_in.get("preamble_code_index"), label="fira.preamble_code_index"
        ),
        sfd_id=_optional_int(fira_in.get("sfd_id"), label="fira.sfd_id"),
        psdu_data_rate=_optional_int(
            fira_in.get("psdu_data_rate"), label="fira.psdu_data_rate"
        ),
        bprf_phr_data_rate=_optional_int(
            fira_in.get("bprf_phr_data_rate"), label="fira.bprf_phr_data_rate"
        ),
        sts_length=_optional_int(fira_in.get("sts_length"), label="fira.sts_length"),
        number_of_sts_segments=_optional_int(
            fira_in.get("number_of_sts_segments"),
            label="fira.number_of_sts_segments",
        ),
        selected_uwb_config_id=_optional_int(
            fira_in.get("selected_uwb_config_id"),
            label="fira.selected_uwb_config_id",
        ),
    )
    tag_in = data.get("tag") or {}
    tag = TagCfg(
        mac=_as_int(tag_in.get("mac", 0x0000)),
        port=tag_in.get("port"),
        connect=bool(tag_in.get("connect", False)),
    )

    anchors_in = data.get("anchors") or []
    anchors: list[AnchorCfg] = []
    for a in anchors_in:
        anchors.append(
            AnchorCfg(
                id=str(a["id"]),
                port=str(a["port"]),
                mac=_as_int(a["mac"]),
            )
        )
    if not anchors:
        raise ValueError("Config must define at least one anchor")

    dedup_in = data.get("dedup", {}) or {}
    dedup = DedupCfg(
        enabled=bool(dedup_in.get("enabled", False)),
        window_s=float(dedup_in.get("window_s", 0.25)),
    )

    sinks_in = data.get("sinks", {}) or {}
    sink_cfg = SinkCfg(enabled=bool(sinks_in.get("console", True)))
    zmq_in = sinks_in.get("zmq", {}) or {}
    zmq_cfg = ZmqSinkCfg(
        enabled=bool(zmq_in.get("enabled", False)),
        endpoint=str(zmq_in.get("endpoint", "tcp://127.0.0.1:5556")),
        bind=bool(zmq_in.get("bind", True)),
        topic=str(zmq_in.get("topic", "meas")),
        sndhwm=int(zmq_in.get("sndhwm", 32)),
        linger_ms=int(zmq_in.get("linger_ms", 0)),
    )

    if tag.connect and not tag.port:
        raise ValueError("tag.port is required when tag.connect is true")

    return AppCfg(
        topology=topology,
        listen=listen,
        fira=fira,
        tag=tag,
        anchors=anchors,
        dedup=dedup,
        sinks=sink_cfg,
        zmq_sink=zmq_cfg,
    )
