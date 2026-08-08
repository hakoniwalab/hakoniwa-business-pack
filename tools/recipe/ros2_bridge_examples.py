#!/usr/bin/env python3
"""Run the three ROS Service/Action bridge directions missing from the catalog."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import ros2_service_add_two_ints as common


IMAGE = "hakoniwa-business-pack/ros2-bridge-examples:local"


@dataclass(frozen=True)
class Profile:
    recipe_id: str
    kind: str
    direction: str
    port: int
    container: str


PROFILES = {
    "service-client": Profile(
        "ros2-service-add-two-ints-client-host-docker",
        "service", "client", 54012, "hakoniwa-ros2-service-client",
    ),
    "action-server": Profile(
        "ros2-action-fibonacci-server-host-docker",
        "action", "server", 54013, "hakoniwa-ros2-action-server",
    ),
    "action-client": Profile(
        "ros2-action-fibonacci-client-host-docker",
        "action", "client", 54014, "hakoniwa-ros2-action-client",
    ),
}


class RecipeError(RuntimeError):
    pass


def profile(name: str) -> Profile:
    return PROFILES[name]


def paths(item: Profile) -> dict[str, Path]:
    base = common.root() / "work" / "recipes" / item.recipe_id
    config = base / "config" / item.kind
    runtime = base / "runtime"
    return {
        "base": base,
        "config": config,
        "offset": config / "offset",
        "docker": base / "docker",
        "runtime": runtime,
        "logs": base / "logs",
        "validation": base / "validation",
        "binding": config / f"{item.kind}-binding.json",
        "transport": config / f"{item.kind}-transport.json",
        "session": runtime / "session.json",
        "host_session": runtime / "host-session.json",
        "stop_request": runtime / "host-stop-request.json",
        "host_log": base / "logs" / "host.log",
    }


def ensure_layout(item: Profile) -> dict[str, Path]:
    result = paths(item)
    for name in ("config", "offset", "docker", "runtime", "logs", "validation"):
        result[name].mkdir(parents=True, exist_ok=True)
    return result


def write_transport(item: Profile, target: Path) -> None:
    if item.direction == "server":
        client = {
            "role": "client",
            "remote": {"address": "host.docker.internal", "port": item.port},
        }
        server = {
            "role": "server",
            "local": {"address": "0.0.0.0", "port": item.port},
        }
    else:
        client = {
            "role": "client",
            "remote": {"address": "127.0.0.1", "port": item.port},
        }
        server = {
            "role": "server",
            "local": {"address": "0.0.0.0", "port": item.port},
        }
    client["options"] = {"connect_timeout_ms": 3000, "read_timeout_ms": 1000, "write_timeout_ms": 1000}
    server["options"] = {"read_timeout_ms": 1000, "write_timeout_ms": 1000}
    endpoints = (
        {"hakoniwa-pdu-ros-service": client, "server_node": server}
        if item.kind == "service"
        else {"fibonacci-client": client, "fibonacci-server": server}
    )
    common.atomic_json(target, {
        "protocol": "tcp", "packetVersion": "v2", "queueDepth": 64,
        "endpoints": endpoints,
    })


def write_binding(item: Profile, p: dict[str, Path]) -> None:
    write_transport(item, p["transport"])
    if item.kind == "service":
        common.atomic_json(p["binding"], {
            "$schema": str(common.sibling("hakoniwa-pdu-ros") / "schema" / "service-binding.schema.json"),
            "version": 1,
            "service": {"transport_config": p["transport"].name, "delta_time_usec": 1000, "time_source_type": "real"},
            "bindings": [{
                "ros_name": "/add_two_ints",
                "ros_type": "example_interfaces/srv/AddTwoInts",
                "hakoniwa_service": "Service/Add",
                "pdu_service_type": "hako_srv_msgs/AddTwoInts",
                "client_endpoint": {"node_id": "hakoniwa-pdu-ros-service"},
                "server_endpoint": {"node_id": "server_node"},
                "max_clients": 4,
                "timeout_msec": 3000,
            }],
        })
        common.copy_offsets(p["offset"])
        common.generate_service_configs(p["binding"], p["offset"], p["config"])
    else:
        common.atomic_json(p["binding"], {
            "$schema": str(common.sibling("hakoniwa-pdu-ros") / "schema" / "action-binding.schema.json"),
            "version": 1,
            "action": {"transport_config": p["transport"].name, "delta_time_usec": 1000, "time_source_type": "real"},
            "bindings": [{
                "ros_name": "/fibonacci",
                "ros_type": "action_tutorials_interfaces/action/Fibonacci",
                "hakoniwa_action": "fibonacci",
                "pdu_action_type": "sample_action_msgs/Fibonacci",
                "client_endpoint": {"node_id": "fibonacci-client"},
                "server_endpoint": {"node_id": "fibonacci-server"},
                "slot_count": 4,
                "goal_response_timeout_msec": 3000,
                "heap": {"goal_bytes": 4096, "result_bytes": 65536, "feedback_bytes": 65536},
            }],
        })
        ros_repo = common.sibling("hakoniwa-pdu-ros")
        sys.path.insert(0, str(ros_repo))
        try:
            generator = importlib.import_module("hakoniwa_pdu_ros.action_config_generator")
            generator.generate_action_configs(
                p["binding"], output_dir=p["config"],
                ros_interface_resolver=lambda _value: None,
            )
        finally:
            sys.path.remove(str(ros_repo))


def dockerfile_text() -> str:
    return common.dockerfile_text().replace(
        "ros-jazzy-rmw-cyclonedds-cpp",
        "ros-jazzy-rmw-cyclonedds-cpp ros-jazzy-demo-nodes-py "
        "ros-jazzy-action-tutorials-py ros-jazzy-action-tutorials-interfaces",
    ).replace(
        'CMD ["bash", "-lc", "source /opt/ros/jazzy/setup.bash && exec service-server --config /recipe/config/service-binding.json --offset-dir /recipe/config/offset --output-dir /recipe/config --rpc-library /opt/hakoniwa/lib/libhakoniwa_pdu_rpc.so"]',
        'CMD ["bash"]',
    )


def configure(args: argparse.Namespace) -> None:
    item = profile(args.profile)
    p = ensure_layout(item)
    write_binding(item, p)
    (p["docker"] / "Dockerfile").write_text(dockerfile_text(), encoding="utf-8")
    (p["docker"] / "endpoint-build.yaml").write_text(
        """version: 1
build:
  type: Release
  dir: /opt/build/hakoniwa-pdu-endpoint
  shared: true
  parallel: 0
bindings:
  python: false
features:
  hakoniwa_core: false
  zenoh: false
  mqtt: false
validation:
  tests: false
  examples: false
  tools: false
  benchmarks: false
  python_import: false
paths:
  hakoniwa_core_root: ""
  vcpkg_root: ""
""",
        encoding="utf-8",
    )
    (p["docker"] / "rpc-build.yaml").write_text(
        """version: 1
build:
  type: Release
  dir: /opt/build/hakoniwa-pdu-rpc
  install_dir: /opt/hakoniwa
paths:
  pdu_endpoint_root: /opt/hakoniwa
  vcpkg_root: ""
""",
        encoding="utf-8",
    )
    print(f"Configured {item.recipe_id}: {p['base']}")


def build(args: argparse.Namespace) -> None:
    item = profile(args.profile)
    p = paths(item)
    if not p["binding"].is_file():
        configure(args)
    common.run([
        str(common.foundation_python()), str(common.root() / "tools" / "foundation.py"),
        "build", "--recipe", str(common.root() / "recipes" / "examples" / f"{item.recipe_id}.yaml"),
    ], cwd=common.root())
    common.install_host_python()
    common.run([
        "docker", "build", "--file", str(p["docker"] / "Dockerfile"),
        "--tag", IMAGE,
        "--build-context", f"endpoint={common.sibling('hakoniwa-pdu-endpoint')}",
        "--build-context", f"rpc={common.sibling('hakoniwa-pdu-rpc')}",
        "--build-context", f"pdu-python={common.sibling('hakoniwa-pdu-python')}",
        "--build-context", f"pdu-ros={common.sibling('hakoniwa-pdu-ros')}",
        str(p["docker"]),
    ])


def runtime_env(item: Profile, p: dict[str, Path]) -> dict[str, str]:
    return common.python_env()


def container_command(item: Profile) -> str:
    base = "source /opt/ros/jazzy/setup.bash; "
    if item.kind == "service":
        return base + "ros2 run demo_nodes_py add_two_ints_server & sleep 1; exec service-client --config /recipe/config/service-binding.json --offset-dir /recipe/config/offset --output-dir /recipe/config --rpc-library /opt/hakoniwa/lib/libhakoniwa_pdu_rpc.so"
    command = "action-server" if item.direction == "server" else "action-client"
    prefix = "" if item.direction == "server" else "ros2 run action_tutorials_py fibonacci_action_server & sleep 1; "
    return base + prefix + f"exec {command} --config /recipe/config/action-binding.json --output-dir /recipe/config --rpc-library /opt/hakoniwa/lib/libhakoniwa_pdu_rpc.so"


def start(args: argparse.Namespace) -> None:
    item = profile(args.profile)
    p = paths(item)
    if p["session"].is_file() and common.load_json(p["session"]).get("state") == "RUNNING":
        raise RecipeError("Recipe session is already RUNNING")
    token = secrets.token_hex(16)
    host_pid = None
    if item.kind == "action" and item.direction == "server":
        for stale in (p["host_session"], p["stop_request"]):
            if stale.exists():
                stale.unlink()
        stream = p["host_log"].open("w", encoding="utf-8")
        process = subprocess.Popen([
            str(common.foundation_python()), str(common.root() / "tools" / "recipe" / "ros2_action_fibonacci_host.py"),
            "--action-config", str(p["config"] / "resolved-action.json"),
            "--endpoint-config", str(p["config"] / "endpoints.json"),
            "--rpc-library", str(common.rpc_library(common.foundation_prefix())),
            "--session", str(p["host_session"]), "--stop-request", str(p["stop_request"]), "--token", token,
        ], cwd=common.root(), stdout=stream, stderr=subprocess.STDOUT, env=runtime_env(item, p), text=True, **common.background_process_options())
        stream.close()
        state = common.wait_json_state(p["host_session"], {"RUNNING", "FAILED"}, 15)
        if state["state"] != "RUNNING":
            raise RecipeError(f"host Action startup failed: {state}")
        host_pid = process.pid

    command = [
        "docker", "run", "--rm", "--init", "--detach", "--name", item.container,
        "--add-host", "host.docker.internal:host-gateway",
        "--mount", f"type=bind,source={p['config']},target=/recipe/config",
    ]
    if item.direction == "client":
        command.extend(["--publish", f"127.0.0.1:{item.port}:{item.port}"])
    command.extend([IMAGE, "bash", "-lc", container_command(item)])
    try:
        common.run(command)
        wait_container_ready(item)
    except BaseException:
        subprocess.run(["docker", "stop", item.container], check=False, capture_output=True)
        if host_pid is not None:
            common.atomic_json(p["stop_request"], {"command": "stop", "token": token})
        raise
    common.atomic_json(p["session"], {
        "schema_version": 1, "state": "RUNNING", "profile": args.profile,
        "container": item.container, "host_pid": host_pid, "host_token": token if host_pid else None,
    })
    print("Start returned; the Recipe remains RUNNING. Next: smoke, status, stop")


def wait_container_ready(item: Profile) -> None:
    expected = "service client ready:" if item.kind == "service" else ("action ready:" if item.direction == "server" else "action client ready:")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = subprocess.run(["docker", "logs", item.container], capture_output=True, text=True, check=False)
        if expected in result.stdout + result.stderr:
            return
        time.sleep(0.25)
    raise RecipeError(f"container did not become ready: expected {expected!r}")


def smoke(args: argparse.Namespace) -> int:
    item = profile(args.profile)
    p = paths(item)
    os.environ.update(runtime_env(item, p))
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(common.foundation_prefix() / "bin"))
    if item.kind == "service":
        result = common.run([
            str(common.foundation_python()),
            str(common.root() / "tools" / "recipe" / "ros2_bridge_probe.py"),
            "service-client", "--config-dir", str(p["config"]),
            "--rpc-library", str(common.rpc_library(common.foundation_prefix())),
            "--a", str(args.a), "--b", str(args.b),
        ], check=False, env=runtime_env(item, p))
        return result.returncode
    if item.direction == "server":
        result = common.run(["docker", "exec", item.container, "bash", "-lc", "source /opt/ros/jazzy/setup.bash && ros2 action send_goal --feedback /fibonacci action_tutorials_interfaces/action/Fibonacci '{order: 10}'"], capture=True, check=False)
        output = result.stdout + result.stderr; print(output)
        return 0 if result.returncode == 0 and "sequence" in output else 1

    result = common.run([
        str(common.foundation_python()),
        str(common.root() / "tools" / "recipe" / "ros2_bridge_probe.py"),
        "action-client", "--config-dir", str(p["config"]),
        "--rpc-library", str(common.rpc_library(common.foundation_prefix())),
        "--order", "10",
    ], check=False, env=runtime_env(item, p))
    return result.returncode


def status(args: argparse.Namespace) -> int:
    item = profile(args.profile); p = paths(item)
    if not p["session"].is_file():
        print("Recipe session: MISSING"); return 1
    session = common.load_json(p["session"])
    running, output = common.command_ok(["docker", "inspect", "--format", "{{.State.Running}}", item.container])
    running = running and output.strip() == "true"
    host_ok = True
    if session.get("host_pid"):
        host_ok = common.managed_host_alive(session, p["host_session"])
    print(f"Recipe session: {session.get('state')}")
    print(f"ROS container: {'RUNNING' if running else 'STOPPED'}")
    print(f"Host runtime: {'RUNNING' if host_ok else 'STOPPED'}")
    return 0 if session.get("state") == "RUNNING" and running and host_ok else 1


def stop(args: argparse.Namespace) -> int:
    item = profile(args.profile); p = paths(item)
    session = common.load_json(p["session"]) if p["session"].is_file() else {}
    subprocess.run(["docker", "stop", item.container], check=False)
    token = session.get("host_token")
    if token:
        common.atomic_json(p["stop_request"], {"command": "stop", "token": token})
        deadline = time.monotonic() + 10
        while common.managed_host_alive(session, p["host_session"]) and time.monotonic() < deadline:
            time.sleep(0.1)
    session.update(state="TERMINATED", terminated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    common.atomic_json(p["session"], session)
    print("Recipe session: TERMINATED")
    return 0


def doctor(args: argparse.Namespace) -> int:
    item = profile(args.profile); p = paths(item)
    required = [p["binding"], p["transport"], p["config"] / "endpoints.json"]
    if item.kind == "action":
        required.append(p["config"] / "resolved-action.json")
    else:
        required.extend([p["config"] / "rpc-client-services.json", p["config"] / "rpc-server-services.json"])
    checks = [("config", all(path.is_file() for path in required), ", ".join(str(path) for path in required if not path.is_file()) or "generated files present")]
    image_ok, output = common.command_ok(["docker", "image", "inspect", IMAGE]); checks.append(("image", image_ok, IMAGE if image_ok else output))
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("profile", choices=PROFILES)
    result.add_argument("command", choices=("configure", "build", "doctor", "start", "smoke", "status", "stop"))
    result.add_argument("--a", type=int, default=20); result.add_argument("--b", type=int, default=22)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int({"configure": configure, "build": build, "doctor": doctor, "start": start, "smoke": smoke, "status": status, "stop": stop}[args.command](args) or 0)
    except (RecipeError, common.RecipeError) as error:
        print(f"error: {error}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
