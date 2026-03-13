from __future__ import annotations

import logging
import time
from queue import Empty, Queue
from threading import Event
from typing import Callable, Optional

from uci import App, Gid, OidQorvo, OidRanging, Status

from .config import AppCfg, ListenMode, TopologyMode
from .fira_session import build_app_configs, resolve_static_sts_values
from .measurements import Measurement, measurements_from_payload
from .sinks import (
    ConsoleMeasurementSink,
    MeasurementPublisher,
    MeasurementSink,
    SourceMetadata,
    ZmqMeasurementSink,
)
from .uci_device import UciDevice


log = logging.getLogger(__name__)


class DemoCoordinator:
    """Coordinate UCI devices for FiRa TWR sessions.

    Topology notes:
    - tag_initiates_anchors_respond is the default mode. The tag is the
      initiator/controller and anchors respond.
    - anchors_initiate_tag_responds allows anchors to initiate and the tag to
      respond (useful for a battery-powered tag with no PC link).
    """

    def __init__(self, cfg: AppCfg) -> None:
        self.cfg = cfg
        self.stop_event = Event()
        self.measurements: Queue[Measurement] = Queue()

        self.anchor_devices: list[UciDevice] = []
        self.tag_device: Optional[UciDevice] = None

        self._mac_to_id: dict[int, str] = {
            anchor.mac: f"ANCHOR:{anchor.id}" for anchor in self.cfg.anchors
        }
        self._mac_to_id[self.cfg.tag.mac] = "TAG"

        self._last_seen: dict[str, tuple[float, tuple[object, ...]]] = {}

        self.publisher = MeasurementPublisher(
            sinks=self._build_sinks(),
            source_metadata=self._build_source_metadata(),
        )

        self._build_devices()

    def _build_source_metadata(self) -> dict[str, SourceMetadata]:
        metadata: dict[str, SourceMetadata] = {
            "TAG": SourceMetadata(role="tag", source_mac=self.cfg.tag.mac),
        }
        for anchor in self.cfg.anchors:
            metadata[f"ANCHOR:{anchor.id}"] = SourceMetadata(
                role="anchor",
                source_mac=anchor.mac,
            )
        return metadata

    def _build_sinks(self) -> list[MeasurementSink]:
        sinks: list[MeasurementSink] = []
        if self.cfg.sinks.enabled:
            sinks.append(ConsoleMeasurementSink())
        if self.cfg.zmq_sink.enabled:
            sinks.append(
                ZmqMeasurementSink(
                    endpoint=self.cfg.zmq_sink.endpoint,
                    topic=self.cfg.zmq_sink.topic,
                    bind=self.cfg.zmq_sink.bind,
                    sndhwm=self.cfg.zmq_sink.sndhwm,
                    linger_ms=self.cfg.zmq_sink.linger_ms,
                )
            )
            mode = "bind" if self.cfg.zmq_sink.bind else "connect"
            log.info(
                "Enabled ZMQ measurement sink: %s %s topic=%s",
                mode,
                self.cfg.zmq_sink.endpoint,
                self.cfg.zmq_sink.topic,
            )
        return sinks

    def _build_devices(self) -> None:
        def ranging_handler(source_id: str) -> Callable[[bytes], None]:
            def _handler(payload: bytes) -> None:
                try:
                    for measurement in measurements_from_payload(source_id, payload):
                        peer_id = None
                        if measurement.peer_short_address is not None:
                            peer_id = self._mac_to_id.get(measurement.peer_short_address)
                        updated = Measurement(
                            timestamp=measurement.timestamp,
                            source_id=measurement.source_id,
                            session_handle=measurement.session_handle,
                            idx=measurement.idx,
                            peer_short_address=measurement.peer_short_address,
                            peer_id=peer_id,
                            status=measurement.status,
                            distance_m=measurement.distance_m,
                        )
                        self.measurements.put(updated)
                except Exception as exc:
                    log.warning("Failed to decode ranging data from %s: %s", source_id, exc)

            return _handler

        listen_anchors = self.cfg.listen in {ListenMode.ANCHORS, ListenMode.BOTH}
        listen_tag = self.cfg.listen in {ListenMode.TAG, ListenMode.BOTH}

        for anchor in self.cfg.anchors:
            handlers = {}
            if listen_anchors:
                handlers[(Gid.Ranging, OidRanging.Start)] = ranging_handler(
                    f"ANCHOR:{anchor.id}"
                )
            handlers[("default", "default")] = lambda gid, oid, payload: None
            self.anchor_devices.append(
                UciDevice(
                    f"anchor:{anchor.id}",
                    anchor.port,
                    notif_handlers=handlers,
                    use_default_handlers=False,
                )
            )

        if self.cfg.tag.port and (self.cfg.tag.connect or listen_tag):
            handlers = {}
            if listen_tag:
                handlers[(Gid.Ranging, OidRanging.Start)] = ranging_handler("TAG")
            handlers[("default", "default")] = lambda gid, oid, payload: None
            if self.cfg.fira.enable_diagnostics:
                handlers[(Gid.Qorvo, OidQorvo.TestDiag)] = lambda payload: log.info(
                    "[tag] diag: %s", payload.hex()
                )
            self.tag_device = UciDevice(
                "tag",
                self.cfg.tag.port,
                notif_handlers=handlers,
                use_default_handlers=False,
            )
        elif listen_tag:
            log.warning("listen=tag requested, but no tag.port is configured.")

    def request_stop(self) -> None:
        self.stop_event.set()

    def _log_static_sts_fields(self, device_label: str) -> None:
        fira = self.cfg.fira
        if fira.sts != "static":
            return
        vendor_id, static_iv, vendor_bytes, static_bytes = resolve_static_sts_values(
            fira
        )
        fields: list[str] = []
        if vendor_id is not None:
            fields.append(f"vendor_id=0x{vendor_id:04x}")
        if static_iv is not None:
            fields.append(f"static_sts_iv=0x{static_iv:012x}")
        if vendor_bytes is not None:
            fields.append(f"vendor_id_bytes={vendor_bytes.hex(':')}")
        if static_bytes is not None:
            fields.append(f"static_sts_iv_bytes={static_bytes.hex(':')}")
        if fields:
            log.debug("[%s] STS static config: %s", device_label, ", ".join(fields))

    def _verify_static_sts_config(self, device: UciDevice, device_label: str) -> None:
        fira = self.cfg.fira
        if fira.sts != "static":
            return

        app_ids = []
        if hasattr(App, "VendorId"):
            app_ids.append(int(App.VendorId))
        if hasattr(App, "StaticStsIv"):
            app_ids.append(int(App.StaticStsIv))
        if not app_ids:
            log.warning(
                "[%s] Device did not accept static STS configuration: "
                "UCI App enum is missing VendorId/StaticStsIv.",
                device_label,
            )
            return

        status, values = device.get_app_config(app_ids)
        if status is None:
            log.warning(
                "[%s] Device did not accept static STS configuration: "
                "session_get_app_config not available.",
                device_label,
            )
            return
        if status != Status.Ok or values is None:
            log.warning(
                "[%s] Device did not accept static STS configuration: "
                "readback failed (status=%s).",
                device_label,
                status.name if isinstance(status, Status) else status,
            )
            return

        def _as_bytes(value: object, length: int) -> Optional[bytes]:
            if isinstance(value, bytes):
                return value
            if isinstance(value, list):
                return bytes(value)
            if isinstance(value, int):
                return value.to_bytes(length, "little")
            return None

        warnings: list[str] = []
        fields: list[str] = []
        _, _, expected_vendor_bytes, expected_static_bytes = resolve_static_sts_values(
            fira
        )
        checks = [
            ("vendor_id", int(App.VendorId), expected_vendor_bytes, 2),
            ("static_sts_iv", int(App.StaticStsIv), expected_static_bytes, 6),
        ]
        for label, app_id, expected, length in checks:
            if app_id not in values:
                warnings.append(f"{label} missing")
                continue
            read_bytes = _as_bytes(values[app_id], length)
            if read_bytes is None or len(read_bytes) != length:
                warnings.append(f"{label} length mismatch")
                continue
            fields.append(f"{label}={read_bytes.hex(':')}")
            if expected is not None and read_bytes != expected:
                warnings.append(f"{label} mismatch")

        if fields:
            log.debug("[%s] STS static readback: %s", device_label, ", ".join(fields))
        if warnings:
            log.warning(
                "[%s] Device did not accept static STS configuration: %s.",
                device_label,
                "; ".join(warnings),
            )

    def _verify_phy_config(self, device: UciDevice, device_label: str) -> None:
        fira = self.cfg.fira
        fields = [
            ("prf_mode", "PrfMode", fira.prf_mode),
            ("preamble_code_index", "PreambleCodeIndex", fira.preamble_code_index),
            ("sfd_id", "SfdId", fira.sfd_id),
            ("psdu_data_rate", "PsduDataRate", fira.psdu_data_rate),
            ("bprf_phr_data_rate", "BprfPhrDataRate", fira.bprf_phr_data_rate),
            ("sts_length", "StsLength", fira.sts_length),
            (
                "number_of_sts_segments",
                "NumberOfStsSegments",
                fira.number_of_sts_segments,
            ),
            (
                "selected_uwb_config_id",
                "SelectedUwbConfigId",
                fira.selected_uwb_config_id,
            ),
        ]
        active = [
            (label, app_name, value)
            for label, app_name, value in fields
            if value is not None
        ]
        if not active:
            return
        log.info(
            "[%s] PHY app-config: %s",
            device_label,
            ", ".join(f"{label}={value}" for label, _, value in active),
        )

        app_ids: list[int] = []
        missing_fields: list[str] = []
        for _, app_name, _ in active:
            if hasattr(App, app_name):
                app_ids.append(int(getattr(App, app_name)))
            else:
                missing_fields.append(app_name)
        if missing_fields:
            log.warning(
                "[%s] UCI App enum missing PHY fields: %s",
                device_label,
                ", ".join(missing_fields),
            )
        if not app_ids:
            return

        status, values = device.get_app_config(app_ids)
        if status is None:
            log.warning(
                "[%s] PHY app-config readback unavailable: session_get_app_config missing.",
                device_label,
            )
            return
        if status != Status.Ok or values is None:
            log.warning(
                "[%s] PHY app-config readback failed (status=%s).",
                device_label,
                status.name if isinstance(status, Status) else status,
            )
            return

        read_fields: list[str] = []
        missing_values: list[str] = []
        for label, app_name, _ in active:
            if not hasattr(App, app_name):
                continue
            app_id = int(getattr(App, app_name))
            if app_id not in values:
                missing_values.append(label)
                continue
            read_value = values[app_id]
            read_fields.append(f"{label}={read_value}")
        if read_fields:
            log.info(
                "[%s] PHY app-config readback: %s",
                device_label,
                ", ".join(read_fields),
            )
        if missing_values:
            log.warning(
                "[%s] PHY app-config readback missing: %s",
                device_label,
                ", ".join(missing_values),
            )

    def _readback_phy_bundle(
        self, device: UciDevice, device_label: str
    ) -> Optional[dict[int, object]]:
        app_names = [
            "RframeConfig",
            "PrfMode",
            "PreambleCodeIndex",
            "SfdId",
            "PsduDataRate",
            "BprfPhrDataRate",
            "PreambleDuration",
            "LinkLayerMode",
            "MacFcsType",
            "StsLength",
            "NumberOfStsSegments",
            "StsConfig",
            "VendorId",
            "StaticStsIv",
        ]
        app_ids: list[int] = []
        missing: list[str] = []
        for name in app_names:
            if hasattr(App, name):
                app_ids.append(int(getattr(App, name)))
            else:
                missing.append(name)
        if missing:
            log.debug(
                "[%s] PHY bundle readback missing App enums: %s",
                device_label,
                ", ".join(missing),
            )
        if not app_ids:
            return None
        status, values = device.get_app_config(app_ids)
        if status is None:
            log.warning(
                "[%s] PHY bundle readback unavailable: session_get_app_config missing.",
                device_label,
            )
            return None
        if status != Status.Ok or values is None:
            log.warning(
                "[%s] PHY bundle readback failed (status=%s).",
                device_label,
                status.name if isinstance(status, Status) else status,
            )
            return None
        ordered = {
            app_id: values.get(app_id)
            for app_id in app_ids
            if app_id in values
        }
        formatted = []
        for app_id, value in ordered.items():
            try:
                name = App(app_id).name
            except ValueError:
                name = hex(app_id)
            formatted.append(f"{name}={value!r}")
        if formatted:
            log.debug(
                "[%s] PHY bundle readback: %s", device_label, ", ".join(formatted)
            )
        return values

    def _verify_prfset_parity(
        self, device_label: str, values: Optional[dict[int, object]]
    ) -> None:
        if values is None or self.cfg.fira.prfset is None:
            return
        prfset = self.cfg.fira.prfset.lower()
        if prfset != "bprf4":
            return
        frame_map = {
            "sp1": 1,
            "sp3": 3,
        }
        expected = {
            "RframeConfig": frame_map.get(self.cfg.fira.frame, 3),
            "PrfMode": 0,
            "PreambleCodeIndex": 10,
            "SfdId": 2,
            "PsduDataRate": 0,
            "BprfPhrDataRate": 0,
            "PreambleDuration": 1,
            "StsLength": 1,
            "NumberOfStsSegments": 1,
        }
        overrides = {
            "PrfMode": self.cfg.fira.prf_mode,
            "PreambleCodeIndex": self.cfg.fira.preamble_code_index,
            "SfdId": self.cfg.fira.sfd_id,
            "PsduDataRate": self.cfg.fira.psdu_data_rate,
            "BprfPhrDataRate": self.cfg.fira.bprf_phr_data_rate,
            "StsLength": self.cfg.fira.sts_length,
            "NumberOfStsSegments": self.cfg.fira.number_of_sts_segments,
        }
        for key, override in overrides.items():
            if override is None:
                continue
            if key == "PrfMode" and isinstance(override, str):
                expected[key] = 0 if override.lower() == "bprf" else override
            else:
                expected[key] = override
        mismatches: list[str] = []
        for name, exp_value in expected.items():
            if not hasattr(App, name):
                continue
            app_id = int(getattr(App, name))
            if app_id not in values:
                mismatches.append(f"{name} missing (expected {exp_value})")
                continue
            actual = values[app_id]
            if actual != exp_value:
                mismatches.append(f"{name} expected {exp_value} got {actual}")
        if mismatches:
            log.warning(
                "[%s] PRFSET bprf4 parity mismatch: %s",
                device_label,
                "; ".join(mismatches),
            )
    def _log_dest_config(
        self,
        *,
        device_label: str,
        device_type: str,
        mac: int,
        dest_macs: list[int],
        multi_node_mode: str,
        n_controlees: int,
    ) -> None:
        role = "controller" if device_type == "controller" else "controlee"
        summary = [
            f"role={role}",
            f"DeviceMacAddress=0x{mac:04X}",
            f"DstMacAddress={[f'0x{dest:04X}' for dest in dest_macs]}",
            f"MultiNodeMode={multi_node_mode}",
        ]
        if device_type == "controller" and multi_node_mode == "onetomany":
            summary.append(f"NumberOfControlees={n_controlees}")
        log.debug("[%s] App-config destinations: %s", device_label, ", ".join(summary))

    def _configure_anchor_devices(self) -> None:
        fira = self.cfg.fira
        session_id = fira.session_id
        dest_macs = [self.cfg.tag.mac]

        for runner, anchor in zip(self.anchor_devices, self.cfg.anchors, strict=True):
            runner.connect()
            runner.init_session(session_id)
            app_cfgs = build_app_configs(
                fira=fira,
                device_type="controller",
                device_role=1,
                mac=anchor.mac,
                dest_macs=dest_macs,
                multi_node_mode=fira.multi_node_mode,
                n_controlees=1,
            )
            runner.set_app_config(app_cfgs)
            self._log_dest_config(
                device_label=f"anchor:{anchor.id}",
                device_type="controller",
                mac=anchor.mac,
                dest_macs=dest_macs,
                multi_node_mode=fira.multi_node_mode,
                n_controlees=1,
            )
            self._log_static_sts_fields(f"anchor:{anchor.id}")
            readback = self._readback_phy_bundle(runner, f"anchor:{anchor.id}")
            self._verify_prfset_parity(f"anchor:{anchor.id}", readback)
            self._verify_static_sts_config(runner, f"anchor:{anchor.id}")
            self._verify_phy_config(runner, f"anchor:{anchor.id}")

    def _configure_tag_device(self, dest_macs: list[int], n_controlees: int) -> None:
        if not self.tag_device:
            return
        if not self.cfg.tag.connect:
            log.info("Tag is not connected; skipping tag configuration.")
            return

        fira = self.cfg.fira
        session_id = fira.session_id

        self.tag_device.connect()
        self.tag_device.init_session(session_id)
        app_cfgs = build_app_configs(
            fira=fira,
            device_type="controlee",
            device_role=0,
            mac=self.cfg.tag.mac,
            dest_macs=dest_macs,
            multi_node_mode=fira.multi_node_mode,
            n_controlees=n_controlees,
        )
        self.tag_device.set_app_config(app_cfgs)
        self._log_dest_config(
            device_label="tag",
            device_type="controlee",
            mac=self.cfg.tag.mac,
            dest_macs=dest_macs,
            multi_node_mode=fira.multi_node_mode,
            n_controlees=n_controlees,
        )
        self._verify_static_sts_config(self.tag_device, "tag")

    def _configure_tag_initiator(self, dest_macs: list[int], n_controlees: int) -> None:
        if not self.tag_device:
            raise RuntimeError("Tag device is required for this mode")
        if not self.cfg.tag.connect:
            log.info("Tag is not connected; skipping tag configuration.")
            return

        fira = self.cfg.fira
        session_id = fira.session_id

        self.tag_device.connect()
        self.tag_device.init_session(session_id)
        app_cfgs = build_app_configs(
            fira=fira,
            device_type="controller",
            device_role=1,
            mac=self.cfg.tag.mac,
            dest_macs=dest_macs,
            multi_node_mode=fira.multi_node_mode,
            n_controlees=n_controlees,
        )
        self.tag_device.set_app_config(app_cfgs)
        self._log_dest_config(
            device_label="tag",
            device_type="controller",
            mac=self.cfg.tag.mac,
            dest_macs=dest_macs,
            multi_node_mode=fira.multi_node_mode,
            n_controlees=n_controlees,
        )
        self._verify_static_sts_config(self.tag_device, "tag")

    def start(self) -> None:
        if self.cfg.fira.multi_node_mode == "onetomany" and not self.cfg.anchors:
            log.warning("multi_node_mode=onetomany set, but no anchors are configured.")
            return

        if self.cfg.topology is TopologyMode.ANCHORS_INITIATE_TAG_RESPONDS:
            self._configure_anchor_devices()
            self._configure_tag_device(
                [a.mac for a in self.cfg.anchors], n_controlees=len(self.cfg.anchors)
            )
            if self.tag_device:
                if self.cfg.tag.connect:
                    self._log_static_sts_fields("tag")
                    self.tag_device.start_ranging()

            if len(self.anchor_devices) > 1:
                log.warning(
                    "Multiple anchors are initiating concurrently. "
                    "O2M is only supported when the tag is the controller."
                )
            for runner, anchor in zip(
                self.anchor_devices, self.cfg.anchors, strict=True
            ):
                self._log_static_sts_fields(f"anchor:{anchor.id}")
                runner.start_ranging()
                log.info(
                    "Started anchor %s (port=%s mac=0x%04X dest=0x%04X)",
                    anchor.id,
                    anchor.port,
                    anchor.mac,
                    self.cfg.tag.mac,
                )
        else:
            self._configure_anchor_devices_as_responders()
            log_starting_tag = True
            first_anchor = self.cfg.anchors[0]
            dest_macs = [anchor.mac for anchor in self.cfg.anchors]
            n_controlees = self.cfg.fira.n_controlees or len(dest_macs)
            if self.cfg.fira.multi_node_mode == "onetomany":
                log.info(
                    "Using O2M with %d controlees dest_macs=%s",
                    n_controlees,
                    [f"0x{mac:04X}" for mac in dest_macs],
                )
            if self.tag_device and self.cfg.tag.connect:
                if self.cfg.fira.multi_node_mode == "onetomany":
                    self._configure_tag_initiator(dest_macs, n_controlees)
                else:
                    self._configure_tag_initiator([first_anchor.mac], 1)
                self._log_static_sts_fields("tag")
                self.tag_device.start_ranging()
            else:
                log.warning(
                    "Tag initiator topology selected, but tag is not connected. "
                    "Anchors are configured to listen only."
                )
                log_starting_tag = False
            if len(self.cfg.anchors) > 1 and self.cfg.fira.multi_node_mode != "onetomany":
                log.warning(
                    "Tag is ranging only to the first anchor. Set "
                    "fira.multi_node_mode=onetomany to range to all anchors."
                )
            if log_starting_tag:
                if self.cfg.fira.multi_node_mode == "onetomany":
                    log.info(
                        "Started tag (port=%s mac=0x%04X dests=%s)",
                        self.cfg.tag.port,
                        self.cfg.tag.mac,
                        [f"0x{mac:04X}" for mac in dest_macs],
                    )
                else:
                    log.info(
                        "Started tag (port=%s mac=0x%04X dest=0x%04X)",
                        self.cfg.tag.port,
                        self.cfg.tag.mac,
                        first_anchor.mac,
                    )

    def _configure_anchor_devices_as_responders(self) -> None:
        fira = self.cfg.fira
        session_id = fira.session_id

        for runner, anchor in zip(self.anchor_devices, self.cfg.anchors, strict=True):
            runner.connect()
            runner.init_session(session_id)
            app_cfgs = build_app_configs(
                fira=fira,
                device_type="controlee",
                device_role=0,
                mac=anchor.mac,
                dest_macs=[self.cfg.tag.mac],
                multi_node_mode=fira.multi_node_mode,
                n_controlees=1,
            )
            runner.set_app_config(app_cfgs)
            self._log_dest_config(
                device_label=f"anchor:{anchor.id}",
                device_type="controlee",
                mac=anchor.mac,
                dest_macs=[self.cfg.tag.mac],
                multi_node_mode=fira.multi_node_mode,
                n_controlees=1,
            )
            readback = self._readback_phy_bundle(runner, f"anchor:{anchor.id}")
            self._verify_prfset_parity(f"anchor:{anchor.id}", readback)
            self._verify_static_sts_config(runner, f"anchor:{anchor.id}")
            self._verify_phy_config(runner, f"anchor:{anchor.id}")
            self._log_static_sts_fields(f"anchor:{anchor.id}")
            runner.start_ranging()
            log.info(
                "Started anchor responder %s (port=%s mac=0x%04X)",
                anchor.id,
                anchor.port,
                anchor.mac,
            )

    def _drain_measurements(self, timeout_s: float) -> None:
        try:
            measurement = self.measurements.get(timeout=timeout_s)
        except Empty:
            return
        if self.cfg.dedup.enabled and not self._should_emit(measurement):
            return
        self.publisher.publish(measurement)

    def _should_emit(self, measurement: Measurement) -> bool:
        now = time.time()
        key = (
            measurement.session_handle,
            measurement.idx,
            measurement.peer_short_address,
            measurement.status,
            measurement.distance_m,
        )
        prev = self._last_seen.get(measurement.source_id)
        if prev is not None:
            prev_t, prev_key = prev
            if key == prev_key and (now - prev_t) <= self.cfg.dedup.window_s:
                return False
        self._last_seen[measurement.source_id] = (now, key)
        return True

    def run(self, duration_s: float = -1.0) -> None:
        start_t = time.time()

        while not self.stop_event.is_set():
            if duration_s >= 0 and (time.time() - start_t) >= duration_s:
                break
            self._drain_measurements(0.1)

    def stop(self) -> None:
        if self.tag_device:
            try:
                self.tag_device.stop_ranging()
                self.tag_device.deinit_session()
                self.tag_device.close()
            except Exception:
                pass

        for runner in self.anchor_devices:
            try:
                runner.stop_ranging()
                runner.deinit_session()
                runner.close()
            except Exception:
                pass

        self.publisher.close()
