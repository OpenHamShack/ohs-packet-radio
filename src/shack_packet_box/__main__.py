"""`python3 -m shack_packet_box`: the BBS, served on SHACK_PORT (8080).

Stops cleanly on SIGTERM inside the module's stop timeout: unkey, close
the radio, end the broker claim, then exit (GUIDE §5 "A stop that overruns
stop_timeout gets SIGKILLed, and the radio is never told")."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from .server import serve
from .settings import BoxSettings
from .shack import PROXY_SECRET_ENV, SHACK_TOKEN_ENV, SHACK_URL_ENV, BrokerClient, shack_http
from .station import BoxStation


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    log = logging.getLogger("shack_packet_box")
    settings = BoxSettings.from_env(os.environ)
    url, token = os.environ.get(SHACK_URL_ENV, ""), os.environ.get(SHACK_TOKEN_ENV, "")
    broker = BrokerClient(shack_http(url, token)) if url and token else None
    if broker is None:
        log.warning("no controller broker in the environment: only the simulator can be used")
    box = BoxStation(settings, broker=broker)
    try:
        box.start()
    except ValueError as e:
        # No callsign: serve the page so the sysop can see why, with no radio.
        log.error("%s", e)
        box.state, box.error = "failed", str(e)
    secret = os.environ.get(PROXY_SECRET_ENV, "")
    srv = serve(box, secret, port=int(os.environ.get("SHACK_PORT", "8080")))
    done = threading.Event()

    def stop(signum, frame):
        done.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    done.wait()
    log.info("stopping: unkeying, closing the radio, ending the claim")
    try:
        box.stop()
    finally:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
