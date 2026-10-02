"""`glossthesi` — serve the kernel on a laptop or in a container.

`GLOSSTHESI_ADDR` (default `127.0.0.1:8100`) is where it listens;
`GLOSSTHESI_DEVICE` picks the device (default: cuda, then mps,
then cpu); the bearer keys or the ID-token audience gate the doors
(app.py); `GLOSSTHESI_VERSION` names the build. The model loads
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
    from . import kernels, telemetry

    version = os.environ.get("GLOSSTHESI_VERSION", "") or "dev"
    wired = telemetry.configure(version)
    k = kernels.get()
    if os.environ.get("GLOSSTHESI_PRELOAD", "1") != "0":
        k.load_forecaster()
    auth = "id-token" if os.environ.get("GLOSSTHESI_AUDIENCE", "").strip() else "keys" if os.environ.get("GLOSSTHESI_KEYS", "").strip() else "open"
    telemetry.started(version=version, device=k.device, addr=addr, auth=auth, **wired)
    config = uvicorn.Config("glossthesi.app:app", host=host or "127.0.0.1", port=int(port), log_config=None)
    _Server(config).run()


class _Server(uvicorn.Server):
    """uvicorn's server, which on SIGTERM stops accepting and waits for
    what is in flight; here the doors are told first, so a request on a
    kept connection is sent back to retry rather than served on a
    stopping instance."""

    def handle_exit(self, sig, frame) -> None:
        from . import telemetry
        from .app import DRAINING

        DRAINING.set()
        telemetry.log.info("draining", extra={"signal": int(sig)})
        super().handle_exit(sig, frame)


if __name__ == "__main__":
    main()
