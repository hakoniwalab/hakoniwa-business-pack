#!/usr/bin/env python3
"""One-shot host probes for ROS Service/Action bridge Recipes."""

from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path


def wait_action_event(
    client,
    expected,
    timeout: float = 5.0,
    ignored=(),
    observed_ignored: list | None = None,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.poll()
        if event.event == expected:
            return event
        if event.event in ignored:
            if observed_ignored is not None:
                observed_ignored.append(event)
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


def wait_action_client_connected(
    raw, settle_sec: float, timeout_sec: float = 10.0
) -> None:
    time.sleep(settle_sec)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if raw.is_running():
            return
        time.sleep(0.01)
    raise TimeoutError("Hakoniwa Action Client TCP connection timed out")


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
        wait_action_client_connected(raw, args.connect_settle_sec)
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


def fibonacci_sequence(order: int) -> list[int]:
    sequence = [0, 1]
    while len(sequence) <= order:
        sequence.append(sequence[-1] + sequence[-2])
    return sequence[: order + 1]


def action_client_cancel_reject(args: argparse.Namespace) -> int:
    """Verify Cancel REJECT, terminal delivery, and slot reuse over one TCP session."""
    from hakoniwa_pdu_rpc import (
        ActionClient,
        ActionClientEvent,
        ActionDecision,
        ActionTerminalStatus,
        make_typed_action_client,
    )

    config_dir = Path(args.config_dir)
    raw = ActionClient(
        args.rpc_library,
        "fibonacci-client",
        "business-pack-fibonacci-cancel-reject-client",
        config_dir / "resolved-action.json",
        config_dir / "endpoints.json",
    )
    try:
        raw.start()
        wait_action_client_connected(raw, args.connect_settle_sec)
        typed = make_typed_action_client(
            raw, config_dir / "resolved-action.json"
        )
        action = typed.action("fibonacci")

        for iteration in range(1, args.iterations + 1):
            goal = action.create_goal()
            goal.order = args.order
            handle = action.send_goal(
                goal, uuid.uuid4().bytes, timeout_usec=5_000_000
            )
            accepted = wait_action_event(
                typed, ActionClientEvent.GOAL_RESPONSE
            )
            if accepted.decision != ActionDecision.ACCEPTED:
                raise RuntimeError(
                    f"iteration {iteration}: Fibonacci Goal was rejected"
                )

            wait_action_event(typed, ActionClientEvent.FEEDBACK)
            action.cancel_goal(handle)
            feedback_after_cancel = []
            cancel_response = wait_action_event(
                typed,
                ActionClientEvent.CANCEL_RESPONSE,
                timeout=args.result_timeout_sec,
                ignored=(ActionClientEvent.FEEDBACK,),
                observed_ignored=feedback_after_cancel,
            )
            if cancel_response.decision != ActionDecision.REJECTED:
                raise RuntimeError(
                    f"iteration {iteration}: expected Cancel REJECT, got "
                    f"{cancel_response.decision}"
                )

            result = wait_action_event(
                typed,
                ActionClientEvent.RESULT,
                timeout=args.result_timeout_sec,
                ignored=(ActionClientEvent.FEEDBACK,),
                observed_ignored=feedback_after_cancel,
            )
            if not feedback_after_cancel:
                raise RuntimeError(
                    f"iteration {iteration}: no Feedback observed after Cancel"
                )
            if result.terminal_status != ActionTerminalStatus.SUCCEEDED:
                raise RuntimeError(
                    f"iteration {iteration}: expected SUCCEEDED, got "
                    f"{result.terminal_status}"
                )
            expected = fibonacci_sequence(args.order)
            sequence = list(result.result.sequence)
            if sequence != expected:
                raise RuntimeError(
                    f"iteration {iteration}: unexpected Result: {sequence}"
                )

            reuse_goal = action.create_goal()
            reuse_goal.order = args.next_order
            action.send_goal(
                reuse_goal, uuid.uuid4().bytes, timeout_usec=5_000_000
            )
            reuse_response = wait_action_event(
                typed, ActionClientEvent.GOAL_RESPONSE
            )
            if reuse_response.decision != ActionDecision.ACCEPTED:
                raise RuntimeError(
                    f"iteration {iteration}: next Goal was rejected"
                )
            reuse_result = wait_action_event(
                typed,
                ActionClientEvent.RESULT,
                timeout=args.result_timeout_sec,
                ignored=(ActionClientEvent.FEEDBACK,),
            )
            reuse_sequence = list(reuse_result.result.sequence)
            expected_reuse = fibonacci_sequence(args.next_order)
            if reuse_result.terminal_status != ActionTerminalStatus.SUCCEEDED:
                raise RuntimeError(
                    f"iteration {iteration}: next Goal did not succeed"
                )
            if reuse_sequence != expected_reuse:
                raise RuntimeError(
                    f"iteration {iteration}: unexpected next Result: "
                    f"{reuse_sequence}"
                )
            if not raw.is_running():
                raise RuntimeError(
                    f"iteration {iteration}: TCP Action endpoint disconnected"
                )
            print(
                f"Cancel REJECT iteration {iteration}/{args.iterations}: "
                "Result and next Goal succeeded"
            )
        return 0
    finally:
        raw.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "service-client",
            "action-client",
            "action-client-cancel-reject",
        ),
    )
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--rpc-library", required=True)
    parser.add_argument("--a", type=int, default=20)
    parser.add_argument("--b", type=int, default=22)
    parser.add_argument("--order", type=int, default=10)
    parser.add_argument("--next-order", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--result-timeout-sec", type=float, default=30.0)
    parser.add_argument("--connect-settle-sec", type=float, default=1.0)
    args = parser.parse_args()
    if args.mode == "service-client":
        return service_client(args)
    if args.mode == "action-client-cancel-reject":
        return action_client_cancel_reject(args)
    return action_client(args)


if __name__ == "__main__":
    raise SystemExit(main())
