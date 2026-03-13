from __future__ import annotations

import json
import math

import pytest

from uwb_app.localize import _solve_2d_position


def test_solve_2d_position_estimates_known_point() -> None:
    anchors = {
        "ANCHOR:A": (0.0, 0.0),
        "ANCHOR:B": (4.0, 0.0),
        "ANCHOR:C": (0.0, 3.0),
        "ANCHOR:D": (4.0, 3.0),
    }
    true_point = (1.2, 1.4)
    distances = {
        source_id: math.hypot(true_point[0] - ax, true_point[1] - ay)
        for source_id, (ax, ay) in anchors.items()
    }

    solved = _solve_2d_position(anchors, distances)
    assert solved is not None
    x_m, y_m = solved
    assert x_m == pytest.approx(true_point[0], abs=1e-3)
    assert y_m == pytest.approx(true_point[1], abs=1e-3)


def test_solve_2d_position_requires_three_anchors() -> None:
    anchors = {
        "ANCHOR:A": (0.0, 0.0),
        "ANCHOR:B": (4.0, 0.0),
    }
    distances = {"ANCHOR:A": 1.0, "ANCHOR:B": 2.0}
    assert _solve_2d_position(anchors, distances) is None


def test_expire_rounds_handles_emit_pop_without_runtime_error() -> None:
    from uwb_app.local_apps_config import LocalizerCfg
    from uwb_app.localize import Localizer, RoundState

    localizer = Localizer.__new__(Localizer)
    localizer.cfg = LocalizerCfg(batch_timeout_s=0.01, max_round_age_s=1.0, min_anchors=3)
    key = (1, "peer")
    localizer._rounds = {
        key: [RoundState(
            first_seen_mono=0.0,
            last_seen_mono=0.0,
            measurements={"ANCHOR:A": 1.0, "ANCHOR:B": 1.1, "ANCHOR:C": 1.2},
        )]
    }
    localizer._dropped_incomplete = 0
    localizer._round_seq = {}

    def emit_and_pop(round_key, _state, _event):
        localizer._remove_state(round_key, _state)

    localizer._emit_round = emit_and_pop  # type: ignore[method-assign]

    localizer._expire_rounds(now_mono=0.5)

    assert localizer._rounds == {}


def test_process_message_emits_immediately_when_total_anchors_reached() -> None:
    from uwb_app.local_apps_config import LocalizerCfg
    from uwb_app.localize import Localizer, RoundState

    localizer = Localizer.__new__(Localizer)
    localizer.cfg = LocalizerCfg(min_anchors=3, total_anchors=4)
    localizer.layout = type("Layout", (), {"anchors": {"ANCHOR:A": (0.0, 0.0), "ANCHOR:B": (4.0, 0.0), "ANCHOR:C": (0.0, 3.0), "ANCHOR:D": (4.0, 3.0)}})()
    localizer._rounds = {}
    localizer._round_seq = {}
    emitted: list[tuple[int, str]] = []

    def capture_emit(key, _state: RoundState, _event: dict[str, object]) -> None:
        emitted.append(key)
        localizer._remove_state(key, _state)

    localizer._emit_round = capture_emit  # type: ignore[method-assign]

    for source_id in ("ANCHOR:A", "ANCHOR:B", "ANCHOR:C", "ANCHOR:D"):
        event = {
            "status": "Ok",
            "source_id": source_id,
            "peer_id": "tag-1",
            "distance_m": 2.0,
            "session_handle": 7,
            "idx": 11,
            "timestamp": 123.0,
        }
        localizer._process_message(json.dumps(event).encode("utf-8"), now_mono=0.0)

    assert emitted == [(7, "tag-1")]


def test_expire_rounds_emits_at_timeout_with_min_anchors_when_total_not_reached() -> None:
    from uwb_app.local_apps_config import LocalizerCfg
    from uwb_app.localize import Localizer, RoundState

    localizer = Localizer.__new__(Localizer)
    localizer.cfg = LocalizerCfg(batch_timeout_s=0.01, max_round_age_s=1.0, min_anchors=3, total_anchors=4)
    key = (1, "peer")
    localizer._rounds = {
        key: [RoundState(
            first_seen_mono=0.0,
            last_seen_mono=0.0,
            measurements={"ANCHOR:A": 1.0, "ANCHOR:B": 1.1, "ANCHOR:C": 1.2},
        )]
    }
    emitted: list[tuple[int, str]] = []
    localizer._dropped_incomplete = 0

    def capture_emit(round_key, _state, _event):
        emitted.append(round_key)
        localizer._remove_state(round_key, _state)

    localizer._emit_round = capture_emit  # type: ignore[method-assign]

    localizer._expire_rounds(now_mono=0.5)

    assert emitted == [key]
    assert localizer._rounds == {}


def test_process_message_joins_round_with_misaligned_source_idx_values() -> None:
    from uwb_app.local_apps_config import LocalizerCfg
    from uwb_app.localize import Localizer, RoundState

    localizer = Localizer.__new__(Localizer)
    localizer.cfg = LocalizerCfg(min_anchors=3, total_anchors=3, round_join_window_s=0.05)
    localizer.layout = type(
        "Layout",
        (),
        {
            "anchors": {
                "ANCHOR:A": (0.0, 0.0),
                "ANCHOR:B": (4.0, 0.0),
                "ANCHOR:C": (0.0, 3.0),
            }
        },
    )()
    localizer._rounds = {}
    localizer._round_seq = {}

    emitted: list[tuple[tuple[int, str], dict[str, int]]] = []

    def capture_emit(key: tuple[int, str], state: RoundState, _event: dict[str, object]) -> None:
        emitted.append((key, dict(state.source_idxs)))
        localizer._remove_state(key, state)

    localizer._emit_round = capture_emit  # type: ignore[method-assign]

    base_event = {
        "status": "Ok",
        "peer_id": "tag-2",
        "distance_m": 2.0,
        "session_handle": 8,
        "timestamp": 500.0,
    }

    events = [
        {**base_event, "source_id": "ANCHOR:A", "idx": 101},
        {**base_event, "source_id": "ANCHOR:B", "idx": 17},
        {**base_event, "source_id": "ANCHOR:C", "idx": 88},
    ]

    for idx, event in enumerate(events):
        localizer._process_message(json.dumps(event).encode("utf-8"), now_mono=10.0 + (idx * 0.01))

    assert emitted == [
        ((8, "tag-2"), {"ANCHOR:A": 101, "ANCHOR:B": 17, "ANCHOR:C": 88})
    ]
