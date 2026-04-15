from __future__ import annotations

from pathlib import Path

from uwb_app.local_apps_config import load_hub_cfg, load_localizer_cfg, parse_layout_cfg


def test_load_hub_cfg_from_section() -> None:
    cfg = load_hub_cfg(Path("config/uwb_hub.yaml"))
    assert cfg.downstream_endpoint == "tcp://127.0.0.1:5560"
    assert cfg.upstream_topic == "meas"


def test_load_localizer_cfg_and_embedded_layout() -> None:
    cfg = load_localizer_cfg(Path("config/uwb_localizer.yaml"))
    assert cfg.subscribe_topic == "meas"
    assert cfg.min_anchors == 4
    assert cfg.total_anchors == 5

    layout = parse_layout_cfg(
        {
            "layout": {
                "anchors": {
                    "ANCHOR:A": [0.0, 0.0],
                    "ANCHOR:B": [4.0, 0.0],
                    "ANCHOR:C": [0.0, 3.0],
                }
            }
        }
    )
    assert len(layout.anchors) == 3
