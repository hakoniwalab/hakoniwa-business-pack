#!/usr/bin/env python3
"""One-shot host probes for ROS Service/Action bridge Recipes."""

from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path


def wait_action_event(client, expected, timeout: float = 5.0, ignored=()):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.poll()
        if event.event == expected:
            return event
        if event.event in ignored:
            if event.feedback is not None:
                print(
                    "Fibonacci feedback: "
                    f"{list(event.feedback.partial_sequence)}"
                )
            continue
        if event.event.name != "NONE":
            raise RuntimeError(f"unexpected Action event: {event.event}")
        time.sleep(0.001)
    raise TimeoutError(f"Action event timed out: {expected}")


def service_client(args: argparse.Namespace) -> int:
    from hakoniwa_pdu_rpc import RpcClient, make_typed_client

    client = RpcClient(
        args.rpc_library,
        "hakoniwa-pdu-ros-service",
        "hakoniwa_pdu_ros_add_0",
        Path(args.config_dir) / "rpc-client-services.json",
        Path(args.config_dir) / "endpoints.json",
    )
    try:
        client.start()
        # start() starts the TCP endpoint asynchronously.  Give the remote
        # Mux Server poll loop time to accept this one-shot Recipe probe
        # before sending its first request.
        time.sleep(args.connect_settle_sec)
        typed = make_typed_client(
            client,
            service_name="Service/Add",
            service_type="AddTwoInts",
        )
        request = typed.create_request()
        request.a = args.a
        request.b = args.b
        result = typed.call(request, timeout_usec=5_000_000)
        print(f"AddTwoInts result: {args.a} + {args.b} = {result.sum}")
        return 0 if int(result.sum) == args.a + args.b else 1
    finally:
        client.close()


def action_client(args: argparse.Namespace) -> int:
    from hakoniwa_pdu_rpc import (
        ActionClient,
        ActionClientEvent,
        ActionDecision,
        make_typed_action_client,
    )

    config_dir = Path(args.config_dir)
    raw = ActionClient(
        args.rpc_library,
        "fibonacci-client",
        "business-pack-fibonacci-client",
        config_dir / "resolved-action.json",
        config_dir / "endpoints.json",
    )
    try:
        raw.start()
        # Keep the first Goal behind the same startup boundary as Service RPC.
        time.sleep(args.connect_settle_sec)
        typed = make_typed_action_client(
            raw, config_dir / "resolved-action.json"
        )
        action = typed.action("fibonacci")
        body = action.create_goal()
        body.order = args.order
        action.send_goal(body, uuid.uuid4().bytes, timeout_usec=5_000_000)
        accepted = wait_action_event(typed, ActionClientEvent.GOAL_RESPONSE)
        if accepted.decision != ActionDecision.ACCEPTED:
            raise RuntimeError("Fibonacci Goal was rejected")
        result = wait_action_event(
            typed,
            ActionClientEvent.RESULT,
            timeout=10.0,
            ignored=(ActionClientEvent.FEEDBACK,),
        )
        sequence = list(result.result.sequence)
        print(f"Fibonacci result: {sequence}")
        # The ROS tutorial interprets order=10 as indices 0 through 10.
        return 0 if sequence == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55] else 1
    finally:
        raw.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("service-client", "action-client"))
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--rpc-library", required=True)
    parser.add_argument("--a", type=int, default=20)
    parser.add_argument("--b", type=int, default=22)
    parser.add_argument("--order", type=int, default=10)
    parser.add_argument("--connect-settle-sec", type=float, default=1.0)
    args = parser.parse_args()
    return service_client(args) if args.mode == "service-client" else action_client(args)


if __name__ == "__main__":
    raise SystemExit(main())
