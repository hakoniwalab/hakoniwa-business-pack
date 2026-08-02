#!/usr/bin/env python3
"""Host-native AddTwoInts RpcMuxServer owned by the Business Pack Recipe."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from hakoniwa_pdu_rpc import RpcMuxServer, ServerEvent, load_service_wire


SERVICE_NAME = "Service/Add"
SERVICE_TYPE = "AddTwoInts"
STATUS_DONE = 3
RESULT_OK = 0
RESULT_CANCELED = 2


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def stop_requested(path: Path, token: str) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("token") == token and data.get("command") == "stop"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-config", required=True)
    parser.add_argument("--endpoint-config", required=True)
    parser.add_argument("--rpc-library", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--stop-request", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    session = Path(args.session).resolve()
    stop_request = Path(args.stop_request).resolve()
    token = args.token
    wire = load_service_wire(SERVICE_TYPE)
    state = {
        "schema_version": 1,
        "state": "STARTING",
        "pid": os.getpid(),
        "token": token,
        "service": SERVICE_NAME,
    }
    atomic_json(session, state)

    try:
        with RpcMuxServer(
            Path(args.rpc_library),
            "server_node",
            Path(args.service_config),
            Path(args.endpoint_config),
        ) as server:
            server.start()
            state["state"] = "RUNNING"
            atomic_json(session, state)
            print("AddTwoInts host RPC mux server is RUNNING", flush=True)
            while not stop_requested(stop_request, token):
                incoming = server.poll()
                if incoming.event == ServerEvent.NONE:
                    time.sleep(0.001)
                    continue
                if incoming.service_name != SERVICE_NAME:
                    raise RuntimeError(
                        f"unexpected RPC service: {incoming.service_name!r}"
                    )
                if incoming.event == ServerEvent.REQUEST_CANCEL:
                    reply = server.create_reply_buffer(
                        incoming.request_token,
                        status=STATUS_DONE,
                        result_code=RESULT_CANCELED,
                    )
                    server.send_cancel_reply(incoming.request_token, reply)
                    continue
                if incoming.event != ServerEvent.REQUEST_IN:
                    raise RuntimeError(f"unexpected RPC event: {incoming.event}")

                request = wire.request_decode(incoming.pdu)
                left = int(request.body.a)
                right = int(request.body.b)
                reply = server.create_reply_buffer(
                    incoming.request_token,
                    status=STATUS_DONE,
                    result_code=RESULT_OK,
                )
                response = wire.response_decode(reply)
                response.body.sum = left + right
                server.send_reply(
                    incoming.request_token,
                    wire.response_encode(response),
                )
                print(
                    f"call: client={incoming.client_name} "
                    f"{left} + {right} = {left + right}",
                    flush=True,
                )
        state["state"] = "TERMINATED"
        state["exit_code"] = 0
        atomic_json(session, state)
        return 0
    except BaseException as error:
        state["state"] = "FAILED"
        state["error"] = str(error)
        state["exit_code"] = 1
        atomic_json(session, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
