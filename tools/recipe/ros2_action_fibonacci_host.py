#!/usr/bin/env python3
"""Host-side Fibonacci Action runtime for Business Pack ROS bridge Recipes."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from hakoniwa_pdu_rpc import (
    ActionServer,
    ActionServerEvent,
    ActionTerminalStatus,
    load_action_wire,
)


ACTION_NAME = "fibonacci"
NODE_ID = "fibonacci-server"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def stop_requested(path: Path, token: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("command") == "stop" and data.get("token") == token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action-config", required=True)
    parser.add_argument("--endpoint-config", required=True)
    parser.add_argument("--rpc-library", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--stop-request", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    session = Path(args.session).resolve()
    stop_request = Path(args.stop_request).resolve()
    state = {
        "schema_version": 1,
        "state": "STARTING",
        "pid": os.getpid(),
        "token": args.token,
        "action": ACTION_NAME,
    }
    atomic_json(session, state)
    wire = load_action_wire("sample_action_msgs/Fibonacci")

    try:
        with ActionServer(
            args.rpc_library,
            NODE_ID,
            args.action_config,
            args.endpoint_config,
        ) as server:
            server.start()
            state["state"] = "RUNNING"
            atomic_json(session, state)
            print("Fibonacci host Action Server is RUNNING", flush=True)
            while not stop_requested(stop_request, args.token):
                event = server.poll()
                if event.event == ActionServerEvent.NONE:
                    time.sleep(0.001)
                    continue
                if event.action_name != ACTION_NAME or event.goal is None:
                    raise RuntimeError(f"unexpected Action event: {event}")
                if event.event == ActionServerEvent.CANCEL_REQUEST:
                    server.accept_cancel(ACTION_NAME, event.goal)
                    result = server.create_result_buffer(ACTION_NAME)
                    server.complete(
                        ACTION_NAME,
                        event.goal,
                        ActionTerminalStatus.CANCELED,
                        result,
                    )
                    continue
                if event.event != ActionServerEvent.GOAL_REQUEST:
                    raise RuntimeError(f"unexpected Action event: {event.event}")

                request = wire.request_decode(event.pdu)
                order = int(request.body.order)
                if order <= 0 or order > 47:
                    server.reject_goal(ACTION_NAME, event.goal)
                    continue
                server.accept_goal(ACTION_NAME, event.goal)
                sequence = [0] if order == 1 else [0, 1]
                while len(sequence) < order:
                    sequence.append(sequence[-1] + sequence[-2])
                    feedback = server.create_feedback_buffer(ACTION_NAME)
                    packet = wire.feedback_decode(feedback)
                    packet.body.partial_sequence = list(sequence)
                    server.send_feedback(
                        ACTION_NAME, event.goal, wire.feedback_encode(packet)
                    )
                    time.sleep(0.02)
                result = server.create_result_buffer(ACTION_NAME)
                packet = wire.response_decode(result)
                packet.body.sequence = sequence[:order]
                server.complete(
                    ACTION_NAME,
                    event.goal,
                    ActionTerminalStatus.SUCCEEDED,
                    wire.response_encode(packet),
                )
        state.update(state="TERMINATED", exit_code=0)
        atomic_json(session, state)
        return 0
    except BaseException as error:
        state.update(state="FAILED", error=str(error), exit_code=1)
        atomic_json(session, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
