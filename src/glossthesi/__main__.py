"""`glossthesi` — serve the kernel on a laptop or in a container.

`GLOSSTHESI_ADDR` (default `127.0.0.1:8100`) is where it listens;
`GLOSSTHESI_DEVICE` picks the device (default: cuda, then mps,
then cpu); the bearer keys or the ID-token audience gate the doors
(app.py), and without either the service does not start, unless
`GLOSSTHESI_INSECURE_OPEN=true` says to serve open;
`GLOSSTHESI_VERSION` names the build. The model loads
before the doors open, so the first request pays no load, and the port
opening is the readiness signal; Chronos-2's weights are loaded then
too (`GLOSSTHESI_PRELOAD=0` leaves them to the first forecast, for a
laptop). On SIGTERM what is in flight is finished and new requests are
sent back to retry. Logs are JSON lines on stdout, and OpenTelemetry
when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (telemetry.py).
"""

import os

import uvicorn


def main() -> None:
    addr = os.environ.get("GLOSSTHESI_ADDR", "127.0.0.1:8100")
    host, _, port = addr.rpartition(":")
    from . import app, kernels, telemetry

    auth = app.arrangement()
    if auth is None:
        raise SystemExit(app.UNARRANGED)

    version = os.environ.get("GLOSSTHESI_VERSION", "") or "dev"
    wired = telemetry.configure(version)
    k = kernels.get()
    if os.environ.get("GLOSSTHESI_PRELOAD", "1") != "0":
        k.load_forecaster()
    telemetry.started(version=version, device=k.device, addr=addr, auth=auth, **wired)
    if auth == "insecure-open":
        telemetry.log.warning(
            "insecure_open", extra={"caller": app.INSECURE_DEV_MODE, "says": "GLOSSTHESI_INSECURE_OPEN: the doors are open, nobody is verified"}
        )
    config = uvicorn.Config("glossthesi.app:app", host=host or "127.0.0.1", port=int(port), log_config=None)
    _Server(config, app.DRAINING, telemetry.log).run()


class _Server(uvicorn.Server):
    """uvicorn's server, which on SIGTERM stops accepting and waits for
    what is in flight; here the doors are told first, so a request on a
    kept connection is sent back to retry rather than served on a
    stopping instance. The signal handler only sets the two flags: it
    runs between two bytecodes of the main thread and may be entered
    again before it returns — uv forwards the SIGTERM it was sent, so a
    `pkill -f` delivers two a few microseconds apart — and a lock or a
    stream the outer entry held would stop the main thread, and with it
    the loop that answers and exits (glossval GV-30). The log line is
    written from the loop, as the drain begins."""

    def __init__(self, config: uvicorn.Config, draining, log) -> None:
        super().__init__(config)
        self._draining = draining
        self._log = log

    def handle_exit(self, sig, frame) -> None:
        self._draining.set()
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets=None) -> None:
        self._log.info("draining", extra={"signals": [int(s) for s in self._captured_signals]})
        await super().shutdown(sockets=sockets)


if __name__ == "__main__":
    main()
