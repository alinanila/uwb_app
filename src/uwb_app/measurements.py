from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Iterable, Optional

from uci import RangingData


@dataclass(frozen=True)
class Measurement:
    timestamp: float
    source_id: str
    session_handle: int
    idx: int
    peer_short_address: Optional[int]
    peer_id: Optional[str]
    status: str
    distance_m: Optional[float]


def _mac_to_int(mac_str: str) -> Optional[int]:
    try:
        mac_bytes = bytes.fromhex(mac_str.replace(":", ""))
    except ValueError:
        return None

    if len(mac_bytes) == 2:
        return int.from_bytes(mac_bytes, "big")
    if len(mac_bytes) == 8:
        # Extended MACs do not contain a short address; map to the lower 16 bits
        # for consistent labeling while keeping a deterministic value.
        return int.from_bytes(mac_bytes[-2:], "big")
    return None


def measurements_from_payload(source_id: str, payload: bytes) -> Iterable[Measurement]:
    rd = RangingData(payload)
    idx = getattr(rd, "idx", -1)
    session_handle = getattr(rd, "session_handle", -1)

    if not rd.meas:
        yield Measurement(
            timestamp=time.time(),
            source_id=source_id,
            session_handle=session_handle,
            idx=idx,
            peer_short_address=None,
            peer_id=None,
            status="unknown",
            distance_m=None,
        )
        return

    for meas in rd.meas:
        status = getattr(meas, "status", None)
        status_name = getattr(status, "name", None) or str(status)
        mac_field = getattr(meas, "mac_add", "")
        peer_short = _mac_to_int(mac_field) if isinstance(mac_field, str) else None
        distance_cm = getattr(meas, "distance", None)
        distance_m = None
        if isinstance(distance_cm, (int, float)) and int(distance_cm) != 0xFFFF:
            distance_m = float(distance_cm) / 100.0

        yield Measurement(
            timestamp=time.time(),
            source_id=source_id,
            session_handle=session_handle,
            idx=idx,
            peer_short_address=peer_short,
            peer_id=None,
            status=status_name,
            distance_m=distance_m,
        )


def format_measurement(measurement: Measurement) -> str:
    if measurement.peer_short_address is None:
        peer = "unknown"
    else:
        peer_mac = f"0x{measurement.peer_short_address:04X}"
        peer = f"{measurement.peer_id}({peer_mac})" if measurement.peer_id else peer_mac
    dist = f"{measurement.distance_m:.2f}m" if measurement.distance_m is not None else "NA"
    return (
        f"[{measurement.source_id}] sess={measurement.session_handle} "
        f"idx={measurement.idx} peer={peer} status={measurement.status} dist={dist}"
    )
