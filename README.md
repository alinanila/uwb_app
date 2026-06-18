# UWB App

Technical documentation for the UWB real-time localization subsystem implemented in the Access Technology for Performer Tracking and Communication in the Theatre MEng Final Year Project at Imperial College London (Alina Roche, 2026).

This repository configures Qorvo's DWM3001CDK UWB boards, collects range measurements from anchor Raspberry Pis, fuses the ranges into a 2-D tag position, and serves a small web dashboard/API for monitoring pose, anchor layout, filter settings, and wearable IMU data.

## Role In The Overall System

`uwb_app` is the localization layer of the stage guidance system. It is responsible for:

- running one UWB anchor agent on each anchor Raspberry Pi;
- collecting all anchor measurements on the Anchor D Raspberry Pi;
- solving the performer tag position from the fused anchor distances;
- exposing pose and sensor data to other project components.

The wearable IMU publisher lives in `uwb_wearable_pi`; the haptic actuator backend lives in `haptic-hearing`. In the current overall system, the endpoint of this repository's live pose and sensor data is HaptiStage in the `stage-support` repository. HaptiStage polls the `uwb-server` HTTP API, maps the data into the authored stage model, generates safety/navigation cues, and passes haptic output to the haptic backend.

## Current Implementation Status

| Area | Status |
|---|---|
| Anchor UWB ranging | Implemented. Each `uwb-agent` configures a connected DWM3001CDK anchor over UCI and listens for measurements from a standalone tag initiator. |
| Measurement transport | Implemented with ZeroMQ PUB/SUB JSON messages on topic `meas`. |
| Measurement hub | Implemented. `uwb-hub` collects anchor PUB streams and republishes them locally. |
| Localization | Implemented as 2-D Gauss-Newton trilateration using anchor's `x` and `y` coordinates. |
| 3D localization | Partially implemented but inactive. 3D layout parsing and a 3D solver exist, but `localize.py` currently calls the 2-D solver and publishes only `x_m` and `y_m`. |
| Pose dashboard/API | Implemented with FastAPI/Uvicorn on port `8000`. |
| Wearable IMU bridge | Implemented in `pose_server.py`. It receives `uwb.sensors` messages from `uwb_wearable_pi` on ZMQ port `5572` and republishes them on port `5571`. |
| Calibration | Implemented as an interactive script `calibrate.py`. It estimates 2-D positions and writes `z=0.0` for solved anchors. |
| Simulation | Implemented for synthetic measurement events without UWB hardware. |
| Inherited Qorvo/UQT tools | Present under `src/uci`, `src/uqt_utils`, and `scripts`; not all inherited scripts are used by the current implementation. |

## System Architecture And Data Flow

The system uses five anchor Pis and one battery-powered UWB tag. Anchor D is the central host for the hub, localizer, and web server.

| Host | Hardware | Services | Main responsibility |
|---|---|---|---|
| Anchor A Pi | DWM3001CDK anchor A | `uwb-agent` | Configure/listen to local anchor and publish measurements. |
| Anchor B Pi | DWM3001CDK anchor B | `uwb-agent` | Configure/listen to local anchor and publish measurements. |
| Anchor C Pi | DWM3001CDK anchor C | `uwb-agent` | Configure/listen to local anchor and publish measurements. |
| Anchor D Pi | DWM3001CDK anchor D | `uwb-agent`, `uwb-hub`, `uwb-localize`, `uwb-server` | Configure/listen to local anchor and publish measurements, collect all measurements, solve pose, host dashboard/API. |
| Anchor E Pi | DWM3001CDK anchor E | `uwb-agent` | Configure/listen to local anchor and publish measurements. |
| Performer | DWM3001CDK tag | No service in this repo when `tag.connect: false` | Standalone tag initiates one-to-many ranging. |

Data flow:

```text
DWM3001CDK anchors
  -> uwb-agent on each anchor Pi
  -> ZMQ PUB topic meas on tcp://<anchor-host>:5556
  -> uwb-hub on Anchor D Pi
  -> ZMQ PUB topic meas on tcp://127.0.0.1:5560
  -> uwb-localize on Anchor D Pi
  -> ZMQ PUB topic pose on tcp://0.0.0.0:5561
  -> uwb-server dashboard/API on http://<anchor-d-ip>:8000
```

Wearable IMU data uses a parallel bridge in the web server:

```text
uwb_wearable_pi
  -> ZMQ PUB topic sensors to tcp://<anchor-d-ip>:5572
  -> uwb-server sensor listener
  -> ZMQ PUB topic sensors on tcp://0.0.0.0:5571
  -> /api/sensors on the dashboard/API
```

## Hardware Requirements

| Component | Quantity | Role |
|---|---:|---|
| DWM3001CDK or compatible Qorvo UWB board | 6 | Five anchors plus one performer tag. |
| Raspberry Pi Zero 2 W or similar Linux host | 5 | One per anchor. |
| GL.iNet travel router or equivalent local network | 1 | Provides a private network and static IPs for the Pis. |
| USB cables/power supplies | As required | Power and serial/UCI link for each anchor board. |
| Power bank or battery | 6 | Power for five anchors plus one tag. |
| Wearable IMU Pi | Optional | Runs `uwb_wearable_pi` and publishes `uwb.sensors`. |
| Haptic PCB/audio hardware | Optional | Driven by the separate `haptic-hearing` repository. |

The active hub configuration binds to hard-coded anchor addresses to receive measurement events. These are specified in `config/uwb_hub.yaml`.

## Software Requirements

| Requirement | Source |
|---|---|
| Python `>=3.12` | Declared in `pyproject.toml`. |
| Hatch | Used to create and run the Python environment. |
| Python packages | `pyserial`, `colorama`, `pyyaml`, `pyzmq`, `fastapi`, `uvicorn[standard]`, `pydantic>=2,<3`. |
| Linux/systemd | Required for the provided service files. |
| Serial access to `/dev/ttyACM0` | Required for each anchor board unless the local config uses a different port. |

Developer tooling:

| Tool | Notes |
|---|---|
| `basedpyright` | Declared in the Hatch default environment. `pyrightconfig.json` ignores inherited `src/uci`, `src/uqt_utils`, and `scripts`. |
| `pytest` | Tests are pytest-style, but `pytest` is not declared in `pyproject.toml`; install it into the Hatch environment if you want to run tests. |

## Repository Structure

| Path | Purpose |
|---|---|
| `src/uwb_app/main.py` | `uwb-run` entry point for anchor/tag UCI coordination. |
| `src/uwb_app/coordinator.py` | Builds UCI devices, configures FiRa sessions, starts ranging, publishes measurements. |
| `src/uwb_app/fira_session.py` | Converts YAML FiRa options into UCI application config values. |
| `src/uwb_app/measurements.py` | Converts UCI ranging payloads into internal measurement objects. |
| `src/uwb_app/sinks.py` | Console and ZMQ measurement publishers. |
| `src/uwb_app/hub.py` | `uwb-hub` ZMQ measurement collector/forwarder. |
| `src/uwb_app/localize.py` | `uwb-localize` measurement batching, filtering, and 2-D pose solving. |
| `src/uwb_app/pose_server.py` | `uwb-server` FastAPI dashboard/API and wearable sensor bridge. |
| `src/uwb_app/calibrate.py` | `uwb-calibrate` interactive anchor calibration script. |
| `src/uwb_app/simulate_measurements.py` | `uwb-sim-run` synthetic measurement publisher. |
| `config/uwb_agent.yaml` | Active per-anchor UWB agent configuration. Device-specific values should be kept local to each Pi. |
| `config/uwb_hub.yaml` | Anchor D hub configuration. |
| `config/uwb_localizer.yaml` | Anchor D localizer/filter/layout configuration. Device-specific values should be kept local to Anchor D. |
| `systemd/*.service` | Service units for the Anchor D deployment and agent nodes. |
| `tools/zmq_subscriber.py` | Manual ZMQ subscriber/debug printer. |
| `tests/` | Unit tests for config parsing, measurement sinks, hub forwarding, STS endianness, simulation, and localizer logic. |
| `src/uci`, `src/uqt_utils`, `scripts` | Inherited Qorvo UCI tooling and examples. |

Although `.gitignore` lists `config/uwb_agent.yaml` and `config/uwb_localizer.yaml`, these files are active runtime inputs. The intention is to prevent anchor-specific parameters being overwritten on deployed Pis; do not treat those paths as unused.

## Installation

Run these commands on each anchor Raspberry Pi. The provided systemd units assume the Linux user is `localuwb`, Hatch is installed at `/home/localuwb/.local/bin/hatch`, and the repository path is `/home/localuwb/uwb_app`.

```bash
# Run on each anchor Pi
sudo apt update
sudo apt install -y git pipx
pipx ensurepath
pipx install hatch
```

Log out and back in if `hatch` is not on `PATH`, then clone and create the environment:

```bash
# Run on each anchor Pi as localuwb
cd /home/localuwb
git clone https://github.com/alinanila/uwb_app.git
cd /home/localuwb/uwb_app
hatch env create
```

If you deploy under another username or path, edit all files in `systemd/` before installing services.

## Configuration

### Agent Configuration

`config/uwb_agent.yaml` is read by `uwb-run` on every anchor Pi.

| Key | Current value | Meaning |
|---|---|---|
| `mode` | `tag_initiates_anchors_respond` | The tag is expected to initiate ranging; anchors are configured as responders. |
| `listen` | `anchors` | The agent listens for ranging notifications from anchor devices only. |
| `fira.session_id` | `42` | FiRa/UCI session ID. Must match the tag. |
| `fira.channel` | `9` | UWB channel. Must match all devices. |
| `fira.round` | `ds-deferred` | Double-sided deferred ranging. |
| `fira.sts` | `static` | Uses static STS values. |
| `fira.vendor_id` | `0x0708` | Static STS vendor ID. Written little-endian to UCI. |
| `fira.static_sts_iv` | `0x060504030201` | Static STS IV. Written little-endian to UCI. |
| `fira.multi_node_mode` | `onetomany` | One tag ranges with multiple anchors. |
| `fira.slot_duration` | `2400` | Slot duration passed to the UCI config. |
| `fira.ranging_interval` | `30` | Ranging interval in ms. |
| `fira.slots_per_rr` | `14` | Slots per ranging round. |
| `tag.mac` | `0x0000` | Tag short address. |
| `tag.connect` | `false` | This runtime does not open a serial link to the tag. |
| `anchors` | One uncommented anchor by default | Each deployed Pi should keep only its local anchor entry uncommented. |
| `sinks.zmq.endpoint` | `tcp://127.0.0.1:5556` in the provided file | Use `tcp://0.0.0.0:5556` on remote anchors A/B/C/E so Anchor D can connect; `tcp://127.0.0.1:5556` is suitable for the local Anchor D agent. |
| `sinks.zmq.topic` | `meas` | Measurement topic. |

For each non-D anchor Pi, edit only the local anchor entry and expose the agent PUB socket:

```yaml
anchors:
  - id: "A"
    port: "/dev/ttyACM0"
    mac: 0x0001

sinks:
  console: false
  zmq:
    enabled: true
    endpoint: "tcp://0.0.0.0:5556"
    bind: true
    topic: "meas"
```

On Anchor D, the local agent can keep `endpoint: "tcp://127.0.0.1:5556"` because the hub runs on the same host.

### Hub Configuration

`config/uwb_hub.yaml` is read only on Anchor D by `uwb-hub`.

| Key | Current value |
|---|---|
| `hub.enabled` | `true` |
| `hub.upstream_endpoints` | A/B/C/E remote anchor endpoints and local D endpoint. |
| `hub.upstream_topic` | `meas` |
| `hub.downstream_endpoint` | `tcp://127.0.0.1:5560` |
| `hub.downstream_bind` | `true` |
| `hub.rcvhwm`, `hub.sndhwm` | `128`, `128` |

Update this file on Anchor D whenever anchor Pi IP addresses change.

### Localizer Configuration

`config/uwb_localizer.yaml` is read only on Anchor D by `uwb-localize` and `uwb-server`.

| Key | Current value | Meaning |
|---|---|---|
| `localizer.subscribe_endpoint` | `tcp://127.0.0.1:5560` | Hub PUB endpoint. |
| `localizer.subscribe_topic` | `meas` | Hub topic. |
| `localizer.batch_timeout_s` | `0.25` | Emit with `min_anchors` if full round has not arrived by this timeout. |
| `localizer.max_round_age_s` | `1.0` | Drop very stale incomplete rounds. |
| `localizer.min_anchors` | `4` | Minimum anchors required for timeout-based solve. The loader enforces at least 4. |
| `localizer.total_anchors` | `5` | Emit immediately when all five anchors have contributed. |
| `localizer.pose_zmq.endpoint` | `tcp://0.0.0.0:5561` | Pose PUB endpoint. |
| `localizer.pose_zmq.topic` | `pose` | Pose topic. |
| `localizer.filter_type` | `ema` | Runtime supports `sma` or `ema`. |
| `localizer.filter_window` | `5` | SMA window size. |
| `localizer.filter_alpha` | `0.1` | EMA alpha. |
| `layout.anchors` | Five `ANCHOR:<ID>` coordinates | Coordinates are parsed as `[x, y]` or `[x, y, z]`; current solver uses `x` and `y`. |

The file currently contains 3-D coordinate lists, but the live solver ignores `z` and publishes a 2-D pose. Three coordinates are maintained for compatibility with 3-D localization should it be implemented.

## Running Manually

Run commands from the repository root on the relevant Pi.

Stop systemd services first if they are already running:

```bash
# Run on the relevant anchor Pi
sudo systemctl stop uwb-*
```

Run an anchor agent:

```bash
# Run on any anchor Pi after editing config/uwb_agent.yaml for that Pi
hatch run uwb-run --config config/uwb_agent.yaml -t -1
```

Run the Anchor D services manually:

```bash
# Run on Anchor D Pi
hatch run uwb-hub --config config/uwb_hub.yaml
```

```bash
# Run on Anchor D Pi
hatch run uwb-localize --config config/uwb_localizer.yaml
```

```bash
# Run on Anchor D Pi
hatch run uwb-server
```

Run the simulator instead of hardware:

```bash
# Run from this repo on a development host or Anchor D Pi
hatch run uwb-sim-run --config config/uwb_agent.yaml --hz 5
```

Subscribe to ZMQ messages:

```bash
# Raw/fused measurements from the hub on Anchor D
hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5560 --topic meas
```

```bash
# Solved poses from the localizer on Anchor D
hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5561 --topic pose
```

```bash
# Wearable sensor stream republished by uwb-server on Anchor D
hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5571 --topic sensors
```

## Running As Systemd Services

Run service installation commands from `/home/localuwb/uwb_app` on the target Pi.

| Service | Host | Unit file | ExecStart |
|---|---|---|---|
| `uwb-agent` | Every anchor Pi | `systemd/uwb-agent.service` | `/home/localuwb/.local/bin/hatch run uwb-run --config config/uwb_agent.yaml -t -1` |
| `uwb-hub` | Anchor D only | `systemd/uwb-hub.service` | `/home/localuwb/.local/bin/hatch run uwb-hub --config config/uwb_hub.yaml` |
| `uwb-localize` | Anchor D only | `systemd/uwb-localize.service` | `/home/localuwb/.local/bin/hatch run uwb-localize --config config/uwb_localizer.yaml` |
| `uwb-server` | Anchor D only | `systemd/uwb-server.service` | `/home/localuwb/.local/bin/hatch run uwb-server` |

Install only the agent on Anchor A, B, C, and E:

```bash
# Run on Anchor A/B/C/E Pi
cd /home/localuwb/uwb_app
chmod +x ./systemd/install_services.sh
./systemd/install_services.sh agent
```

Install all services on Anchor D:

```bash
# Run on Anchor D Pi
cd /home/localuwb/uwb_app
chmod +x ./systemd/install_services.sh
./systemd/install_services.sh all
```

Manage services:

```bash
systemctl status uwb-*
```

```bash
journalctl -u uwb-* -f
```

```bash
sudo systemctl restart uwb-*
```

## Runtime Interfaces

| Interface | Host | Direction | Endpoint | Topic/path | Payload |
|---|---|---|---|---|---|
| Agent measurement PUB | Each anchor Pi | PUB | `tcp://<anchor-ip>:5556` or local `127.0.0.1:5556` | `meas` | `uwb.measurement` JSON |
| Hub measurement SUB | Anchor D | SUB | Configured upstream endpoints | `meas` | `uwb.measurement` JSON |
| Hub measurement PUB | Anchor D | PUB | `tcp://127.0.0.1:5560` | `meas` | Unchanged measurement JSON |
| Localizer pose PUB | Anchor D | PUB | `tcp://0.0.0.0:5561` | `pose` | `uwb.pose` JSON |
| Wearable sensor SUB | Anchor D, inside `uwb-server` | SUB bind | `tcp://0.0.0.0:5572` | `sensors` | `uwb.sensors` JSON from `uwb_wearable_pi` |
| Wearable sensor republisher | Anchor D, inside `uwb-server` | PUB bind | `tcp://0.0.0.0:5571` | `sensors` | Unchanged sensor JSON |
| Dashboard | Anchor D | HTTP | `http://<anchor-d-ip>:8000/` | `/` | HTML dashboard |
| Layout API | Anchor D | HTTP | `http://<anchor-d-ip>:8000` | `GET/POST /api/layout` | Anchor layout JSON |
| Pose API | Anchor D | HTTP | `http://<anchor-d-ip>:8000` | `GET /api/pose` | Latest pose JSON |
| Sensor API | Anchor D | HTTP | `http://<anchor-d-ip>:8000` | `GET /api/sensors` | Latest IMU JSON |
| Filter API | Anchor D | HTTP | `http://<anchor-d-ip>:8000` | `GET/POST /api/filter` | Localizer filter settings |

Posting `/api/layout` or `/api/filter` writes `config/uwb_localizer.yaml` and runs `sudo systemctl restart uwb-localize`. The service user must be allowed to perform that restart for the dashboard save action to take effect.

## Example Input And Output Messages

Measurement event on topic `meas`:

```json
{
  "schema": "uwb.measurement",
  "schema_version": 1,
  "source_id": "ANCHOR:A",
  "source_role": "anchor",
  "source_mac": "0x0001",
  "session_handle": 42,
  "idx": 17,
  "status": "Ok",
  "distance_m": 2.73,
  "peer_id": "TAG",
  "peer_mac": "0x0000"
}
```

Pose event on topic `pose`:

```json
{
  "schema": "uwb.pose",
  "schema_version": 1,
  "session_handle": 42,
  "round_seq": 12,
  "peer_id": "TAG",
  "x_m": 1.42,
  "y_m": 2.18,
  "x_raw": 1.51,
  "y_raw": 2.03,
  "anchors_used": ["ANCHOR:A", "ANCHOR:B", "ANCHOR:C", "ANCHOR:D", "ANCHOR:E"],
  "n_anchors": 5
}
```

Wearable sensor event accepted by `uwb-server` on topic `sensors`:

```json
{
  "schema": "uwb.sensors",
  "schema_version": 1,
  "timestamp": 1710000000.0,
  "device_id": "PERFORMER",
  "bno085": {
    "linear_acceleration": {"x": 0.01, "y": 0.02, "z": 0.0},
    "gyroscope": {"x": 0.0, "y": 0.0, "z": 0.01},
    "is_moving": false
  }
}
```

## Calibration

Run calibration on Anchor D after all agents and the hub are publishing valid measurements.

Required assumptions in the current calibration script:

| Assumption | Current implementation |
|---|---|
| Anchor A | Fixed at `(0.0, 0.0, 0.0)`. |
| Anchor D | Placed on the positive x-axis at the measured A-D distance. |
| Anchor C | Solved from distances to A and D with positive y. |
| Anchor B | Solved from distances to A, C, and D. |
| Anchor E | Solved from distances to A, B, C, and D. |
| Z coordinate | Written as `0.0` for solved anchors. 3-D calibration is not currently implemented. |

Run:

```bash
# Run on Anchor D Pi from /home/localuwb/uwb_app
hatch run uwb-calibrate --config config/uwb_localizer.yaml
```

Follow the prompts:

1. Place the tag on anchor A and press Enter.
2. Place the tag on anchor D and press Enter.
3. Place the tag on anchor C and press Enter.
4. Place the tag on anchor B and press Enter.
5. Place the tag on anchor E and press Enter.

The script waits for recent readings to settle, averages distances for each prompted position, writes the resulting `layout.anchors`, and then tells you to restart the localizer:

```bash
sudo systemctl restart uwb-localize
```

If calibrating the layout manually, edit `layout.anchors` in `config/uwb_localizer.yaml` and restart `uwb-localize`, or edit the anchor layout table via the API dashboard.

## Testing And Debugging

Run tests from the repository root on a development host:

```bash
hatch run pip install pytest
hatch run pytest
```

Run static checking:

```bash
hatch run basedpyright
```

Useful debugging commands:

```bash
# Check USB serial devices on an anchor Pi
ls -l /dev/ttyACM*
```

```bash
# Watch an anchor agent
journalctl -u uwb-agent -f
```

```bash
# Watch localizer output on Anchor D
journalctl -u uwb-localize -f
```

```bash
# Check listening TCP ports on Anchor D
ss -ltn | grep -E ':5556|:5560|:5561|:5571|:5572|:8000'
```

```bash
# Print hub measurement JSON on Anchor D
hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5560 --topic meas
```

### Troubleshooting

| Symptom | Likely cause | Check/fix |
|---|---|---|
| `uwb-agent` cannot open the board | Wrong serial port or permissions. | Check `/dev/ttyACM*`; ensure the service user can access the serial device; update `anchors[].port`. |
| Hub receives no remote measurements | Remote agents are bound to `127.0.0.1` or IPs changed. | On remote anchors, use `sinks.zmq.endpoint: "tcp://0.0.0.0:5556"`; update `config/uwb_hub.yaml` on Anchor D. |
| Measurements arrive but no poses | Not enough `status: "Ok"` distances in one batch, source IDs do not match layout, or session handles are inconsistent. | Watch `journalctl -u uwb-localize -f`; verify `source_id` values are `ANCHOR:A` through `ANCHOR:E`; check `min_anchors` and `total_anchors`. |
| Dashboard shows no pose | `uwb-localize` is not publishing or `uwb-server` cannot subscribe to `tcp://127.0.0.1:5561`. | Subscribe manually to topic `pose`; restart `uwb-localize` and `uwb-server`. |
| Dashboard save does not apply layout/filter | `uwb-server` cannot run `sudo systemctl restart uwb-localize`. | Check `journalctl -u uwb-server -f` and sudoers/service permissions. |
| Wearable sensors missing | `uwb_wearable_pi` endpoint does not point at Anchor D or `uwb-server` is not running. | Set wearable `imu_sink.endpoint` to `tcp://<anchor-d-ip>:5572`; check `/api/sensors`. |

## Known Limitations

- Live localization is 2-D even though layout parsing accepts `[x, y, z]` and inactive 3-D solver code exists.
- The tag is not controlled by this runtime when `tag.connect: false`; it must already be configured to initiate the matching FiRa session.
- The provided systemd units are hard-coded for `localuwb` and `/home/localuwb/uwb_app`.
- `config/uwb_agent.yaml` and `config/uwb_localizer.yaml` are deployment-specific. Pulling repository changes onto anchor Pis can overwrite local calibration if those files are tracked or not protected.
- Calibration estimates 2-D anchor positions from noisy UWB ranges and can produce metre-scale error if the tag, anchors, or range data are unstable.

## Inherited And Project-Specific Work

This repository is a project fork of `b4shful/uwb_app`, which is based on Qorvo UCI tooling for DW3xxx/QM3xxx devices. Permission to use the upstream work was obtained from the author.

Project-specific additions for the submitted stage system include:

- five-anchor hub/localizer deployment;
- ZMQ measurement and pose pipeline;
- Anchor D FastAPI dashboard;
- interactive five-anchor calibration;
- wearable IMU sensor bridge;
- systemd units for the anchor deployment.

Inherited or partially inherited areas include:

- `src/uci` UCI bindings and message helpers;
- `src/uqt_utils`;
- `scripts/` Qorvo device, FiRa, and utility scripts.

## Related Repositories

| Repository | Role |
|---|---|
| `https://github.com/alinanila/uwb_wearable_pi` | Wearable Pi IMU publisher that sends `uwb.sensors` to `uwb-server`. |
| `https://github.com/alinanila/haptic-hearing` | Haptic actuator backend. |
| `https://github.com/Dell-S/stage-support` | HaptiStage application that retrieves UWB pose/sensor APIs, runs stage cue logic, and outputs haptic cue commands. |
| `https://github.com/b4shful/uwb_app` | Upstream UWB app fork source. |
