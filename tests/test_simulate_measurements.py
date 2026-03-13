from __future__ import annotations

import random

from uwb_app.simulate_measurements import _build_simulated_measurement


def test_build_simulated_measurement_shape() -> None:
    meas = _build_simulated_measurement(
        anchor_id="A",
        anchor_mac=0x0001,
        tag_mac=0x0000,
        idx=9,
        session_handle=42,
        rng=random.Random(1),
    )
    assert meas.source_id == "ANCHOR:A"
    assert meas.idx == 9
    assert meas.session_handle == 42
    assert meas.peer_id == "TAG"
    assert meas.peer_short_address == 0x0000
    assert meas.status in {"Ok", "RxTimeout"}
