from uci import App

from uwb_app.config import FiraCfg
from uwb_app.fira_session import build_app_configs


def test_static_sts_endianness() -> None:
    fira = FiraCfg(
        sts="static",
        vendor_id=0x0708,
        static_sts_iv=0x060504030201,
    )
    app_configs = build_app_configs(
        fira=fira,
        device_type="controller",
        device_role=1,
        mac=0x0000,
        dest_macs=[0x0001],
        multi_node_mode="unicast",
        n_controlees=1,
    )
    config_map = dict(app_configs)
    assert config_map[App.VendorId] == bytes.fromhex("08 07")
    assert config_map[App.StaticStsIv] == bytes.fromhex("01 02 03 04 05 06")
