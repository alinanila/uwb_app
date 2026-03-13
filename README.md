## Prerequisites
### Hatch
* https://hatch.pypa.io/latest/install/

## Usage (scripts)
From the repo root, any of the scripts can be run with:
```
hatch run python <script path>
```
For example:
```
hatch run python .\scripts\fira\run_fira_twr\run_fira_twr.py -h
```
will show the help for the run_fira_twr.py script

### Basic two way session:

Open two terminals in the uwb_app directory, in one terminal type:

```
hatch run python .\scripts\fira\run_fira_twr\run_fira_twr.py -p COM18 --controlee -t -1
```

In the other:
```
hatch run python .\scripts\fira\run_fira_twr\run_fira_twr.py -p COM11 -t -1
```


`-t -1` is used to set the ranging session duration to forever, by default it would end after 10 seconds


`--controlee` sets one of the devkits up as the controlee/responder, the default is that controlee is false and so without this flag it will automatically set it up as an initiator

## Usage (full RTLS app)
Open a terminal (no need for multiple windows this time) in the `uwb_app` directory and run:

```
hatch run uwb-run --config config/uwb_agent.yaml -t -1
```

The --config and -t values shown above are the defaults, so if you don't provide --config and -t values it'll use those.

In other words, `hatch run uwb-run` is the same as `hatch run uwb-run --config config/uwb_agent.yaml -t -1` \
If you want something other than that, use the parameters and specify them explicitly.

When testing different configurations, you can create multiple config files inside the config folder and select which one to run with `--config <path_to_config>`



## Config files by app
- `config/uwb_agent.yaml`: device/session/anchor + measurement sink config for `uwb-run` and `uwb-sim-run`.
- `config/uwb_hub.yaml`: hub-only forwarding config for `uwb-hub`.
- `config/uwb_localizer.yaml`: localizer runtime config plus embedded `layout` geometry for `uwb-localize`.

## Measurement sinks (console + ZeroMQ PUB)
The app now emits decoded measurements through a sink interface. By default, console output stays enabled.

### Config
In `config/uwb_agent.yaml`, configure agent sinks like this:

```yaml
sinks:
  console: true
  zmq:
    enabled: true
    endpoint: "tcp://127.0.0.1:5556" # or tcp://0.0.0.0:5556
    bind: true
    topic: "meas"
    sndhwm: 32
    linger_ms: 0
```

- `console`: keep current stdout measurement output.
- `zmq.enabled`: enable PUB sink.
- `endpoint`: supports `tcp://...` and `ipc://...` (but on Windows prefer TCP because IPC support may be unavailable in pyzmq builds).
- `bind`: publisher bind/connect mode.
- `sndhwm` + non-blocking send are used for best-effort behavior (drop under backpressure rather than blocking).

### Single-host mode (USB anchors + local subscriber)
1. Enable ZMQ sink with localhost TCP endpoint: `tcp://127.0.0.1:5556`.
2. Run app:
   ```
   hatch run uwb-run --config config/uwb_agent.yaml -t -1
   ```
3. In another terminal run:
   ```
   hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5556 --topic meas
   ```

### Distributed-ready mode (agent-style publishing over TCP)
Use a TCP endpoint in the same sink config pattern, e.g. `tcp://0.0.0.0:5556` for publisher bind.
Remote subscribers/aggregators connect with:

```
hatch run python tools/zmq_subscriber.py --endpoint tcp://<publisher-host>:5556 --topic meas
```

This keeps transport best-effort and low-latency for stage/demo visualization workloads.

### Windows note
If you use `ipc://...` endpoints on Windows, pyzmq may raise `Protocol not supported` depending on the bundled libzmq build. Use `tcp://127.0.0.1:5556` (or another TCP endpoint) instead.


### Hardware-free sink test mode (simulated 4-anchor stream)
You can validate sink/output wiring without any UWB hardware by running the simulator entry point.
It emits synthetic measurements for each configured anchor at a configurable per-anchor rate.

```
hatch run uwb-sim-run --config config/uwb_agent.yaml --hz 5 --duration 30
```

- Uses the same sink config (`sinks.console`, `sinks.zmq.*`) as the real app.
- Keeps event shape identical to sink boundary output (`schema`, source/peer fields, status, distance).
- Good for validating local TCP or network TCP PUB/SUB consumers before hardware deployment.

Example with local TCP subscriber:

```
hatch run python tools/zmq_subscriber.py --endpoint tcp://127.0.0.1:5556 --topic meas
hatch run uwb-sim-run --config config/uwb_agent.yaml --hz 5 --duration 20
```

## Hub / collector app (`uwb-hub`)
`uwb-hub` subscribes to one or more upstream measurement PUB endpoints and republishes a single unified local measurement stream. Payloads are forwarded unchanged (topic + JSON bytes).

Run:
```
hatch run uwb-hub --config config/uwb_hub.yaml
```

- Upstream list and downstream endpoint are configured in `config/uwb_hub.yaml` (supports either top-level keys or a `hub:` section).
- Socket behavior is best-effort low latency (`RCVHWM`, `SNDHWM`, `linger_ms=0`, non-blocking sends).
- If downstream consumers are slow, hub drops frames and logs drop counters.

## Localizer app (`uwb-localize`)
`uwb-localize` subscribes to measurement events, batches by `(session_handle, idx, peer_id)`, and outputs 2D tag poses when either all expected anchors are received (`total_anchors`) or `batch_timeout_s` elapses with at least `min_anchors` measurements. Incomplete rounds below `min_anchors` are dropped at timeout.

Run:
```
hatch run uwb-localize --config config/uwb_localizer.yaml
```

Anchor geometry is embedded in `config/uwb_localizer.yaml` under `layout.anchors` so the central-node localizer can run without extra files.

Optional explicit layout override:
```
hatch run uwb-localize --config config/uwb_localizer.yaml --layout config/custom_layout.yaml
```

## End-to-end modes
### Single-host mode (no hub required)
1. Agent publishes measurements locally (`sinks.zmq.enabled=true`, endpoint e.g. `tcp://127.0.0.1:5556`).
2. Configure localizer `subscribe_endpoint: tcp://127.0.0.1:5556`.
3. Run:
   ```
   hatch run uwb-run --config config/uwb_agent.yaml -t -1
   hatch run uwb-localize --config config/uwb_localizer.yaml
   ```

### Distributed mode (hub on central node, e.g. Anchor D Pi)
1. Each remote agent Pi publishes measurements over TCP (topic `meas`).
2. On central Pi, configure `config/uwb_hub.yaml` `upstream_endpoints` to all agent endpoints.
3. Run hub:
   ```
   hatch run uwb-hub --config config/uwb_hub.yaml
   ```
4. Configure localizer `subscribe_endpoint` in `config/uwb_localizer.yaml` to the hub downstream endpoint (default `tcp://127.0.0.1:5560`). Set `min_anchors` for minimum solve quality and `total_anchors` to the expected full-anchor count so rounds can emit immediately when complete.
5. Run localizer (and any UI/recorder subscribers) against that single stream.

This keeps consumers stable if agent->hub transport changes later.
