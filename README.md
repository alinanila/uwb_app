# Wearable Device for Non-Sighted Stage Navigation

A prototype system to aid visually impaired stage performers navigate a theatre environment using a UWB Real-Time Localisation System (RTLS).

## To Do
* Integration with [Dell](https://github.com/Dell-S/stage-support/tree/main)
* Moving average for smoothing tag position
* Add 3D capability
    - `localize.py`: choose 2D or 3D position filter, comment lines as appropriate in `Localizer._emit_round`
    - `calibrate.py`: use either bilateration (2D) or trilateration (3D) for anchor B
* Try powering the tag via LiPo
    - Might fix the tag time-out error, will need to solder a JST header on J1 to test
* IMU integration for orientation and possible Kalman filtering for a better fix than moving average
* Bluetooth TTS audio, either from Pi D, or from an MCU worn on the performer, depends which will be easier and more reliable
* Buttons
    - Right now the systemd services handle ranging, hub and localiser, these start automatically
    - Calibration still being run manually over SSH, would be good to get this activated by a button interrupt and guided with TTS audio
    - Physical button for reloading localiser after updating the web server, rather than letting it run sudo?
* Integration with Haptics
* CAD for packaging
    - Think about how to design this so that all the stupid wires are contained well
    - Make sure the packaging for the anchors and the tag (if designing something for it) are conducive to calibration - some kind of notch that the tag can fit to so that the antennas are aligned and the tag doesn't move about
* Diagram for system overview/data flow (and calibration layout? should be the same)


## Hardware

List will update as project progresses.

| Component | Quantity |
|---|:---:|
| DWM3001CDK | 5 (4 anchors + 1 tag) |
| Raspberry Pi Zero 2 W | 4 |
| GL.iNet Travel Router | 1 |

## System Overview

The UWB localisation system is based on a fork of [b4shful/uwb_app](https://github.com/b4shful/uwb_app). Full permission was obtained from the author before use. Please refer to the original repo for more detailed usage instructions and other sink configurations.

For the purpose of stage navigation, four anchors are placed at the corners of the stage. Each anchor is a DWM3001CDK board connected via USB to a Raspberry Pi Zero 2 W. The tag is a standalone DWM3001CDK powered by a power bank. All Pis connect to a shared GL.inet travel router with static IP addresses. Each anchor performs Double-Sided Two-Way Ranging (DS-TWR) with the tag to find the distance between them. 

The original repo is itself based on the **DW3xxx & QM3xxx SDK v1.1.1**, which also contains the **DWM3001CDK Developer Manual**. This is available for download from [Qorvo's website](https://www.qorvo.com/products/p/DWM3001CDK#documents).

**Node responsibilities:**

| Node | Services |
|---|---|
| Anchor A Pi | `uwb-agent` |
| Anchor B Pi | `uwb-agent` |
| Anchor C Pi | `uwb-agent` |
| Anchor D Pi | `uwb-agent`, `uwb-hub`, `uwb-localize`, `uwb-server` |

**Data flow:**

    DWM3001CDK -> uwb-agent (each Pi)
                      | ZMQ PUB tcp://<pi-ip>:5556
                 uwb-hub (Anchor D Pi)
                      | ZMQ PUB tcp://127.0.0.1:5560
                uwb-localize (Anchor D Pi)
                      | ZMQ PUB tcp://0.0.0.0:5561
                 uwb-server (Anchor D Pi, port 8000)

---

## Prerequisites

- [Hatch](https://hatch.pypa.io/latest/install/) installed on all Pis
- Python 3.12
- All Pis connected to the same network with static IPs
- It is recommended to perform calibration of the UWB antennae using the Qorvo UWB Explorer to minimise ranging error

---

## Installation

On each Pi, clone the repo and install services:

    git clone https://github.com/alinanila/uwb_app.git
    cd uwb_app

**On Anchor A, B, C Pis** (agent only): `./systemd/install_services.sh agent`

**On Anchor D Pi** (all services): `./systemd/install_services.sh all`

The install script copies the relevant `.service` files to `/etc/systemd/system`, enables them, and starts them. Services restart automatically on boot and on failure.

---

## Configuration

### `config/uwb_agent.yaml` - run on every anchor Pi

Set the anchor ID, MAC address, and serial port for the DWM3001CDK connected to that Pi. Comment out all anchors except the one local to that Pi:

    anchors:
      - id: "A"          # change to B, C, or D on respective Pis
        port: "/dev/ttyACM0"
        mac: 0x0001       # must be unique per anchor

The tag MAC address and FiRa session parameters must match across all devices:

    tag:
      mac: 0x0000
      connect: false          # tag runs standalone, no PC link needed

    fira:
      session_id: 42
      ...
      multi_node_mode: onetomany    # multiple anchors, one tag
      slot_duration: 2400           # duration of one twr message (2400 -> 2 ms)
      ranging_interval: 30          # duration of one complete twr ranging sequence in ms
      slots_per_rr: 12              # number of twr messages per block

For best performance, `slot_duration` can be 2 ms at minimum, and for 4 anchors a minimum of 12 messages are required. The minimum ranging interval from this would be 24 ms, but 30 ms is chosen to ensure messages are not lost.

### `config/uwb_hub.yaml` - run on Anchor D Pi only

Set the IP addresses of all four anchor Pis:

    hub:
      upstream_endpoints:
        - "tcp://192.168.x.x:5556"   # Anchor A
        - "tcp://192.168.x.x:5556"   # Anchor B
        - "tcp://192.168.x.x:5556"   # Anchor C
        - "tcp://127.0.0.1:5556"     # Anchor D (local)

### `config/uwb_localizer.yaml` - run on Anchor D Pi only

Anchor positions are set here under `layout.anchors`. These are updated automatically by the calibration script. Manually set them if skipping calibration (can be done from the dashboard):

    layout:
      anchors:
        ANCHOR:A: [0.0, 0.0]
        ANCHOR:B: [0.0, 3.0]
        ANCHOR:C: [4.0, 3.0]
        ANCHOR:D: [4.0, 0.0]

### Tag configuration

Establish a serial connection (e.g. via PuTTy) at 115200 baud rate to the DWM3001CDK assigned as the tag. Set the ranging interval in ms, the number of slots per block, one-to-many mode, the initiator (tag) address and the responder (anchor) addresses using CLI commands specified in the DWM3001CDK Developer Manual:

    INITF -BLOCK=30 -ROUND=12 -MULTI -ADDR=0 -PADDR=[1,2,3,4]
    SAVE
    SETAPP INITF
    SAVE 
`SETAPP INITF` ensures that the tag begins TWR automatically on startup independent of the anchors.      

---

## Calibration

Calibration measures the real physical positions of the anchors automatically using the tag. Run this on the Anchor D Pi after all services are running.

The anchor layout is defined as follows, with A at the origin and D on the x-axis:

    C -------- B
    |          |
    A -------- D  ->  x

Run the following on the Anchor D Pi:

    hatch run uwb-calibrate --config config/uwb_localizer.yaml

Follow the prompts - place the tag on each anchor in order (A -> D -> C -> B) and press Enter. The script waits for readings to stabilise before averaging, then writes the computed positions back to `uwb_localizer.yaml` automatically.

Restart the localizer to apply:

    sudo systemctl restart uwb-localize

---

## Dashboard

A web dashboard for viewing the tag position and editing anchor positions is served on Anchor D Pi at:

    http://<anchor-d-ip>:8000

Anchor positions can be edited directly in the table and saved. Saving automatically restarts `uwb-localize` with the new layout.

---

## Manual Operation

To run any service manually (e.g. for testing), stop the systemd service first then run:

    # Agent
    hatch run uwb-run --config config/uwb_agent.yaml

    # Hub (Anchor D only)
    hatch run uwb-hub --config config/uwb_hub.yaml

    # Localizer (Anchor D only)
    hatch run uwb-localize --config config/uwb_localizer.yaml

    # Monitor raw measurements
    hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5560 --topic meas

---

## Service Management

    # Check status
    sudo systemctl status uwb-agent
    sudo systemctl status uwb-hub
    sudo systemctl status uwb-localize
    sudo systemctl status uwb-server

    # View logs
    sudo journalctl -u uwb-agent -f
    sudo journalctl -u uwb-localize -f

    # Restart
    sudo systemctl restart uwb-localize
---

## Known Issues

- After ~5 minutes, the tag will time-out, and the `uwb-agent` logs will show: `status=RangingRxTimeout dist=NA`. This appears to be an issue with the CLI firmware provided by Qorvo, although the exact problem is unlcear. This could possibly be circumvented by using the UCI firmware, which appears to be much more robust. However, for this system that would require hardcoding an autonomous mode, which was the reason for using the CLI firmware in the first place. Power cycle the tag for a temporary fix, but this is a deeper issue with the chosen hardware.
    - Issue was potentially just the cable?? Would be fixed by LiPo anyway.
- When all the anchors are powered on, they run fine, but if an anchor is then turned off and powered on again, while other anchors remain on, another instance of `uwb-agent` will begin to run alongside the original one on that anchor. This is annoying, but can be fixed by power cycling all the anchors, so that they are all 'starting fresh'. Not a robust fix, but works. Cause is as of yet undetermined.
- The tag draws a very small current (~0.05 A maximum spike when polling with anchors first begins). If powering it with a regular power bank via USB, the power bank may automatically turn off after a short period of time. Restart the power bank/power cycle the tag for a temporary fix.
- Errors in anchor calibration can be on the scale of 1 m. This is likely due to the current localisation process, which performs no filtering on the tag position.
- Tag position jumps a large amount between readings, see above.




