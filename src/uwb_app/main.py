from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path
from typing import Any, Optional

from uci import UciComError

from .config import load_config
from .coordinator import DemoCoordinator

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "uwb_agent.yaml"

log = logging.getLogger(__name__)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="UWB demo: multi-anchor FiRa TWR via Qorvo UCI"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to YAML config file (default: %(default)s)",
    )
    parser.add_argument(
        "-t",
        "--time",
        type=float,
        default=-1.0,
        help="Run duration in seconds. -1 = forever (default: %(default)s)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    cfg = load_config(args.config)
    coord = DemoCoordinator(cfg)

    def _sigint_handler(signum: int, frame: Any) -> None:
        coord.request_stop()

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        coord.start()
        log.info("Running (Ctrl-C to stop)...")
        coord.run(duration_s=args.time)
    except UciComError as exc:
        if exc.n == 1:
            log.critical("UCI communication error: %s", exc)
        else:
            log.critical("UCI communication error: %s", exc)
            raise
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Stopping...")
        coord.stop()
        log.info("Done.")


if __name__ == "__main__":
    main()
