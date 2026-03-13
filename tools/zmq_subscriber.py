from __future__ import annotations

import argparse
import json
import sys

import zmq


def main() -> None:
    parser = argparse.ArgumentParser(description="Subscribe to UWB measurement events")
    parser.add_argument(
        "--endpoint",
        default="tcp://127.0.0.1:5556",
        help="ZMQ PUB endpoint (ipc:// or tcp://)",
    )
    parser.add_argument("--topic", default="meas", help="Subscription topic")
    args = parser.parse_args()

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 128)
    sock.setsockopt(zmq.LINGER, 0)

    if args.endpoint.startswith("ipc://") and sys.platform.startswith("win"):
        print(
            "Warning: IPC endpoints are often unsupported in pyzmq on Windows; "
            "prefer tcp://127.0.0.1:5556"
        )

    sock.connect(args.endpoint)

    sock.setsockopt(zmq.SUBSCRIBE, args.topic.encode("utf-8"))
    print(f"Subscribed to topic={args.topic!r} endpoint={args.endpoint}")

    while True:
        topic, payload = sock.recv_multipart()
        event = json.loads(payload.decode("utf-8"))
        print(f"[{topic.decode('utf-8')}] {json.dumps(event, sort_keys=True)}")


if __name__ == "__main__":
    main()
