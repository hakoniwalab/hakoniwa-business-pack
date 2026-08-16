"""Hakoniwa-Core-free Python PDU Endpoint transport for JSON messages."""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path
from typing import Any, Callable

from .protocol import decode_message, encode_message


PDU_ROBOT = "hako_remote_operation"
PDU_CHANNEL_ID = 1


class TransportError(RuntimeError):
    pass


def write_tcp_endpoint_config(
    directory: Path,
    *,
    role: str,
    address: str,
    port: int,
) -> Path:
    """Write the small Endpoint/cache/comm config set for one local host."""

    if role not in {"server", "client"}:
        raise TransportError("role must be server or client")
    if not isinstance(address, str) or not address:
        raise TransportError("address must be a non-empty string")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise TransportError("port must be an integer from 1 through 65535")

    directory.mkdir(parents=True, exist_ok=True)
    endpoint_path = directory / "endpoint.json"
    cache_path = directory / "cache.json"
    comm_path = directory / "comm.json"
    endpoint = {
        "name": f"remote_operation_{role}",
        "cache": cache_path.name,
        "comm": comm_path.name,
    }
    cache = {
        "type": "buffer",
        "name": "remote_operation_queue",
        "store": {"mode": "queue", "depth": 32},
    }
    comm: dict[str, Any] = {
        "protocol": "tcp",
        "name": f"remote_operation_tcp_{role}",
        "direction": "inout",
        "role": role,
        "comm_raw_version": "v2",
        "options": {
            "connect_timeout_ms": 2000,
            "read_timeout_ms": 0,
            "write_timeout_ms": 2000,
            "keepalive": True,
            "no_delay": True,
        },
    }
    if role == "server":
        comm["local"] = {"address": address, "port": port}
    else:
        comm["remote"] = {"address": address, "port": port}

    for path, value in (
        (endpoint_path, endpoint),
        (cache_path, cache),
        (comm_path, comm),
    ):
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return endpoint_path


class PduJsonTransport:
    """Bidirectional JSON message transport over a Python PDU Endpoint."""

    def __init__(
        self,
        endpoint_config: Path,
        *,
        endpoint_factory: Callable[[str, str], Any] | None = None,
        key_factory: Callable[..., Any] | None = None,
    ) -> None:
        if endpoint_factory is None or key_factory is None:
            try:
                from hakoniwa_pdu_endpoint.c_endpoint import Endpoint, PduResolvedKey
            except (ImportError, OSError) as exc:
                raise TransportError(
                    "hakoniwa-pdu-endpoint Python/native runtime is unavailable; "
                    "run the command through 'python3 tools/workspace.py run -- ...' "
                    f"({exc})"
                ) from exc
            endpoint_factory = endpoint_factory or Endpoint
            key_factory = key_factory or PduResolvedKey
        self._endpoint = endpoint_factory("remote_operation", "inout")
        self._key = key_factory(robot=PDU_ROBOT, channel_id=PDU_CHANNEL_ID)
        self._endpoint_config = Path(endpoint_config)
        self._received: queue.Queue[dict[str, Any] | Exception] = queue.Queue()
        self._opened = False

    def _on_receive(self, _key: Any, payload: bytes) -> None:
        try:
            self._received.put(decode_message(payload))
        except Exception as exc:  # Preserve validation failure for the owner thread.
            self._received.put(exc)

    def start(self) -> None:
        if self._opened:
            raise TransportError("transport is already started")
        self._endpoint.open(str(self._endpoint_config))
        self._endpoint.subscribe_on_recv_callback(self._key, self._on_receive)
        self._endpoint.start()
        self._opened = True

    def wait_connected(self, timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self._endpoint.is_running():
                return
            time.sleep(0.02)
        raise TransportError(f"PDU Endpoint did not connect within {timeout_sec:g}s")

    def send(self, message: dict[str, Any]) -> None:
        if not self._opened:
            raise TransportError("transport is not started")
        self._endpoint.send(self._key, encode_message(message))

    def receive(self, timeout_sec: float) -> dict[str, Any]:
        if not self._opened:
            raise TransportError("transport is not started")
        try:
            item = self._received.get(timeout=timeout_sec)
        except queue.Empty as exc:
            raise TransportError(
                f"no remote-operation message within {timeout_sec:g}s"
            ) from exc
        if isinstance(item, Exception):
            raise TransportError(f"invalid remote-operation payload: {item}") from item
        return item

    def close(self) -> None:
        if not self._opened:
            return
        try:
            self._endpoint.stop()
        finally:
            self._endpoint.close()
            self._opened = False

    def __enter__(self) -> "PduJsonTransport":
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()
