from __future__ import annotations

from typing import Any

from uci import App

from .config import FiraCfg

TOKEN_MAP: dict[str, int] = {
    # multi-node
    "unicast": 0,
    "onetomany": 1,
    # device type
    "controlee": 0,
    "controller": 1,
    # round usage
    "ss_deferred": 1,
    "ds_deferred": 2,
    "ss_non_deferred": 3,
    "ds_non_deferred": 4,
    # frame
    "sp1": 1,
    "sp3": 3,
    # schedule
    "time": 1,
    # report bits
    "tof": 1,
    "azimuth": 2,
    "fom": 8,
    # sts
    "static": 0,
    "provisioned": 3,
    "provisioned_key": 4,
    # aoa report
    "all_disabled": 0,
    "all_enabled": 1,
    "azimuth_only": 2,
    # hopping mode
    "disabled": 0,
    "enabled": 1,
    # diag fields (vendor)
    "aoa": 0x2,
    "cfo": 0x8,
    "metrics": 0x20,
    "cir": 0x40,
}

PRF_MODE_TOKEN_MAP: dict[str, int] = {
    "bprf": 0,
}

PRFSET_PRESETS: dict[str, dict[str, int | str]] = {
    "bprf4": {
        "prf_mode": "bprf",
        "preamble_code_index": 10,
        "sfd_id": 2,
        "sts_length": 1,
        "number_of_sts_segments": 1,
        "psdu_data_rate": 0,
        "bprf_phr_data_rate": 0,
        "frame": "sp3",
    }
}

def resolve_static_sts_values(
    fira: FiraCfg,
) -> tuple[int | None, int | None, bytes | None, bytes | None]:
    if fira.sts != "static":
        return fira.vendor_id, fira.static_sts_iv, None, None

    vendor_id = fira.vendor_id
    static_iv = fira.static_sts_iv
    vendor_bytes = (
        vendor_id.to_bytes(2, "little") if vendor_id is not None else None
    )
    static_bytes = (
        static_iv.to_bytes(6, "little") if static_iv is not None else None
    )
    return vendor_id, static_iv, vendor_bytes, static_bytes


def _parse_pipe_flags(s: str, mapping: dict[str, int]) -> int:
    parts = [p.strip().replace("-", "_") for p in s.split("|") if p.strip()]
    value = 0
    for p in parts:
        if p not in mapping:
            raise ValueError(f"Unknown flag token: {p!r}")
        value |= mapping[p]
    return value


def _get_app_field(name: str) -> int:
    if not hasattr(App, name):
        raise RuntimeError(
            f"UCI App enum is missing {name}. Update uci bindings."
        )
    return getattr(App, name)


def _parse_optional_token(
    value: str | int | None, *, label: str, mapping: dict[str, int]
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an int or token string, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        token = value.strip().replace("-", "_")
        if token in mapping:
            return mapping[token]
        try:
            return int(token, 0)
        except ValueError as exc:
            raise ValueError(
                f"{label} must be one of {sorted(mapping)} or an int, got {value!r}"
            ) from exc
    raise TypeError(f"{label} must be an int or string, got {type(value)}: {value!r}")


def _parse_optional_int(value: str | int | None, *, label: str) -> int | None:
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


def _validate_optional_int(
    value: int | None, *, label: str, allowed: set[int]
) -> int | None:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"{label} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _resolve_phy_value(
    *,
    label: str,
    explicit: int | str | None,
    prfset: str | None,
) -> int | str | None:
    if explicit is not None:
        return explicit
    if prfset is None:
        return None
    preset = PRFSET_PRESETS.get(prfset.lower())
    if preset is None:
        raise ValueError(f"Unknown fira.prfset {prfset!r}")
    return preset.get(label)


def build_app_configs(
    *,
    fira: FiraCfg,
    device_type: str,
    device_role: int,
    mac: int,
    dest_macs: list[int],
    multi_node_mode: str = "unicast",
    n_controlees: int = 1,
) -> list[tuple[int, Any]]:
    def _as_int(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        return int(value)

    dev_type_v = TOKEN_MAP[device_type.replace("-", "_")]
    node_v = TOKEN_MAP[multi_node_mode.replace("-", "_")]
    round_v = TOKEN_MAP[fira.round.replace("-", "_")]
    sched_v = TOKEN_MAP[fira.schedule.replace("-", "_")]
    sts_v = TOKEN_MAP[fira.sts.replace("-", "_")]
    frame_value = _resolve_phy_value(
        label="frame", explicit=fira.frame, prfset=fira.prfset
    )
    frame_v = TOKEN_MAP[str(frame_value).replace("-", "_")]
    hop_v = TOKEN_MAP[fira.hopping_mode.replace("-", "_")]

    report_v = _parse_pipe_flags(fira.report, TOKEN_MAP)
    aoa_req_v = TOKEN_MAP[fira.aoa_report.replace("-", "_")]
    mac_v = _as_int(mac)
    dest_macs_v = [_as_int(addr) for addr in dest_macs]

    app_configs: list[tuple[int, Any]] = [
        (App.DeviceType, _as_int(dev_type_v)),
        (App.DeviceRole, _as_int(device_role)),
        (App.MultiNodeMode, _as_int(node_v)),
        (App.RangingRoundUsage, _as_int(round_v)),
        (App.DeviceMacAddress, mac_v),
        (App.ChannelNumber, _as_int(fira.channel)),
        (App.ScheduleMode, _as_int(sched_v)),
        (App.StsConfig, _as_int(sts_v)),
        (App.RframeConfig, _as_int(frame_v)),
        (App.ResultReportConfig, _as_int(report_v)),
        (App.AoaResultReq, _as_int(aoa_req_v)),
        (App.SlotDuration, _as_int(fira.slot_duration)),
        (App.RangingInterval, _as_int(fira.ranging_interval)),
        (App.SlotsPerRr, _as_int(fira.slots_per_rr)),
        (App.MaxNumberOfMeasurements, _as_int(fira.max_measurements)),
        (App.HoppingMode, _as_int(hop_v)),
        (App.RssiReporting, _as_int(fira.rssi_reporting)),
        (App.DstMacAddress, dest_macs_v),
    ]

    vendor_id, static_sts_iv, vendor_bytes, static_bytes = resolve_static_sts_values(
        fira
    )

    if fira.sts == "static":
        insert_at = app_configs.index((App.StsConfig, sts_v)) + 1
        if vendor_id is not None and vendor_bytes is not None:
            app_configs.insert(
                insert_at, (_get_app_field("VendorId"), vendor_bytes)
            )
            insert_at += 1
        if static_sts_iv is not None and static_bytes is not None:
            app_configs.insert(
                insert_at, (_get_app_field("StaticStsIv"), static_bytes)
            )

    if multi_node_mode == "onetomany" and device_type == "controller":
        app_configs.append((App.NumberOfControlees, _as_int(n_controlees)))

    if fira.enable_diagnostics:
        diag_v = _parse_pipe_flags(fira.diag_fields, TOKEN_MAP)
        app_configs.extend(
            [
                (App.EnableDiagnostics, 1),
                (App.DiagsFrameReportsFields, _as_int(diag_v)),
            ]
        )

    prf_mode_value = _resolve_phy_value(
        label="prf_mode", explicit=fira.prf_mode, prfset=fira.prfset
    )
    prf_mode_v = _parse_optional_token(
        prf_mode_value, label="fira.prf_mode", mapping=PRF_MODE_TOKEN_MAP
    )
    preamble_code_index_value = _resolve_phy_value(
        label="preamble_code_index",
        explicit=fira.preamble_code_index,
        prfset=fira.prfset,
    )
    preamble_code_index_v = _parse_optional_int(
        preamble_code_index_value, label="fira.preamble_code_index"
    )
    sfd_id_value = _resolve_phy_value(
        label="sfd_id", explicit=fira.sfd_id, prfset=fira.prfset
    )
    sfd_id_v = _parse_optional_int(sfd_id_value, label="fira.sfd_id")
    psdu_data_rate_value = _resolve_phy_value(
        label="psdu_data_rate", explicit=fira.psdu_data_rate, prfset=fira.prfset
    )
    psdu_data_rate_v = _parse_optional_int(
        psdu_data_rate_value, label="fira.psdu_data_rate"
    )
    bprf_phr_data_rate_value = _resolve_phy_value(
        label="bprf_phr_data_rate",
        explicit=fira.bprf_phr_data_rate,
        prfset=fira.prfset,
    )
    bprf_phr_data_rate_v = _parse_optional_int(
        bprf_phr_data_rate_value, label="fira.bprf_phr_data_rate"
    )
    sts_length_value = _resolve_phy_value(
        label="sts_length", explicit=fira.sts_length, prfset=fira.prfset
    )
    sts_length_v = _parse_optional_int(sts_length_value, label="fira.sts_length")
    number_of_sts_segments_value = _resolve_phy_value(
        label="number_of_sts_segments",
        explicit=fira.number_of_sts_segments,
        prfset=fira.prfset,
    )
    number_of_sts_segments_v = _parse_optional_int(
        number_of_sts_segments_value, label="fira.number_of_sts_segments"
    )
    selected_uwb_config_id_v = _parse_optional_int(
        fira.selected_uwb_config_id, label="fira.selected_uwb_config_id"
    )
    if prf_mode_v is not None:
        app_configs.append((App.PrfMode, _as_int(prf_mode_v)))
    if preamble_code_index_v is not None:
        _validate_optional_int(
            preamble_code_index_v,
            label="fira.preamble_code_index",
            allowed={9, 10, 11, 12},
        )
        app_configs.append((App.PreambleCodeIndex, _as_int(preamble_code_index_v)))
    if sfd_id_v is not None:
        _validate_optional_int(sfd_id_v, label="fira.sfd_id", allowed={0, 2, 4})
        app_configs.append((App.SfdId, _as_int(sfd_id_v)))
    if psdu_data_rate_v is not None:
        _validate_optional_int(
            psdu_data_rate_v, label="fira.psdu_data_rate", allowed={0}
        )
        app_configs.append((App.PsduDataRate, _as_int(psdu_data_rate_v)))
    if bprf_phr_data_rate_v is not None:
        _validate_optional_int(
            bprf_phr_data_rate_v, label="fira.bprf_phr_data_rate", allowed={0, 1}
        )
        app_configs.append((App.BprfPhrDataRate, _as_int(bprf_phr_data_rate_v)))
    if sts_length_v is not None:
        _validate_optional_int(sts_length_v, label="fira.sts_length", allowed={0, 1, 2})
        app_configs.append((App.StsLength, _as_int(sts_length_v)))
    if number_of_sts_segments_v is not None:
        _validate_optional_int(
            number_of_sts_segments_v,
            label="fira.number_of_sts_segments",
            allowed={0, 1},
        )
        app_configs.append(
            (App.NumberOfStsSegments, _as_int(number_of_sts_segments_v))
        )
    if selected_uwb_config_id_v is not None:
        app_configs.append(
            (App.SelectedUwbConfigId, _as_int(selected_uwb_config_id_v))
        )

    return app_configs
