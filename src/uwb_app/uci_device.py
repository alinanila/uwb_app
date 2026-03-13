from __future__ import annotations

import logging
import time
from typing import Any, Optional

from uci import Client, SessionState, SessionType, Status


log = logging.getLogger(__name__)


class UciDevice:
    """Wraps a single UCI Client + one ranging session lifecycle."""

    def __init__(
        self,
        name: str,
        port: str,
        *,
        notif_handlers: Optional[dict[Any, Any]] = None,
        use_default_handlers: bool = False,
    ) -> None:
        self.name = name
        self.port = port
        self.notif_handlers = notif_handlers or {}
        self.use_default_handlers = use_default_handlers
        self.client: Optional[Any] = None
        self.session_handle: Optional[int] = None

    def connect(self) -> None:
        log.info("Connecting %s on %s", self.name, self.port)
        self.client = Client(
            port=self.port,
            notif_handlers=self.notif_handlers,
            use_default_handlers=self.use_default_handlers,
        )

    def init_session(self, session_id: int) -> None:
        if not self.client:
            raise RuntimeError(f"{self.name}: connect() before init_session()")
        rts, handle = self.client.session_init(session_id, SessionType.Ranging)
        if rts != Status.Ok:
            raise RuntimeError(f"{self.name}: session_init failed: {rts.name} ({rts})")
        self.session_handle = session_id if handle is None else handle
        log.info(
            "%s: session_init session_id=%s session_handle=%s",
            self.name,
            session_id,
            self.session_handle,
        )

    def set_app_config(self, app_configs: list[tuple[int, Any]]) -> None:
        if not self.client or self.session_handle is None:
            raise RuntimeError(f"{self.name}: init_session() before set_app_config()")
        log.debug(
            "%s: session_set_app_config handle=%s", self.name, self.session_handle
        )
        rts, rtv = self.client.session_set_app_config(self.session_handle, app_configs)
        if rts != Status.Ok:
            raise RuntimeError(
                f"{self.name}: session_set_app_config failed: {rts.name} ({rts})\n{rtv}"
            )

    def get_app_config(
        self, app_ids: list[int]
    ) -> tuple[Optional[Status], Optional[dict[int, Any]]]:
        if not self.client or self.session_handle is None:
            raise RuntimeError(f"{self.name}: init_session() before get_app_config()")
        if not hasattr(self.client, "session_get_app_config"):
            return None, None
        log.debug(
            "%s: session_get_app_config handle=%s", self.name, self.session_handle
        )
        rts, values = self.client.session_get_app_config(self.session_handle, app_ids)
        if rts != Status.Ok:
            return rts, None
        parsed: dict[int, Any] = {}
        for app_id, _, value in values:
            parsed[int(app_id)] = value
        return rts, parsed

    def start_ranging(self) -> None:
        if not self.client or self.session_handle is None:
            raise RuntimeError(f"{self.name}: init_session() before start_ranging()")
        log.debug("%s: ranging_start handle=%s", self.name, self.session_handle)
        rts = self.client.ranging_start(self.session_handle)
        if rts != Status.Ok:
            raise RuntimeError(f"{self.name}: ranging_start failed: {rts.name} ({rts})")

    def wait_for_session_state(
        self,
        target_state: SessionState,
        *,
        timeout_s: float = 1.0,
        poll_s: float = 0.05,
    ) -> bool:
        if not self.client or self.session_handle is None:
            return False
        log.debug(
            "%s: session_get_state handle=%s", self.name, self.session_handle
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status, state = self.client.session_get_state(self.session_handle)
            if status == Status.Ok and state == target_state:
                return True
            time.sleep(poll_s)
        return False

    def stop_ranging(self) -> None:
        if not self.client or self.session_handle is None:
            return
        log.debug("%s: ranging_stop handle=%s", self.name, self.session_handle)
        rts = self.client.ranging_stop(self.session_handle)
        if rts != Status.Ok:
            log.warning("%s: ranging_stop failed: %s (%s)", self.name, rts.name, rts)

    def deinit_session(self) -> None:
        if not self.client or self.session_handle is None:
            return
        log.debug("%s: session_deinit handle=%s", self.name, self.session_handle)
        rts = self.client.session_deinit(self.session_handle)
        if rts != Status.Ok:
            log.warning("%s: session_deinit failed: %s (%s)", self.name, rts.name, rts)
        self.session_handle = None

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
