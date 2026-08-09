#!/usr/bin/env python3
"""Materialize and run the ROS 2 AddTwoInts host/Docker Recipe."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import ipaddress
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path


RECIPE_ID = "ros2-service-add-two-ints-host-docker"
IMAGE = "hakoniwa-business-pack/add-two-ints-ros2:local"
CONTAINER = "hakoniwa-add-two-ints-ros2"
DEFAULT_PORT = 54010
DEFAULT_BIND = "0.0.0.0"
REQUIRED_OFFSETS = (
    "AddTwoIntsRequest.offset",
    "AddTwoIntsRequestPacket.offset",
    "AddTwoIntsResponse.offset",
    "AddTwoIntsResponsePacket.offset",
)
LEGACY_GENERATED_FILES = (
    "container-client-endpoint.json",
    "container-rpc-comm.json",
    "container-rpc-endpoints.json",
    "host-rpc-comm.json",
    "host-rpc-endpoints.json",
    "rpc-pdudef.json",
    "rpc-queue.json",
)


class RecipeError(RuntimeError):
    pass


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def sibling(name: str) -> Path:
    return root().parent / name


def workspace() -> Path:
    return root() / "work" / "recipes" / RECIPE_ID


def foundation_prefix() -> Path:
    return root() / "work" / "foundation" / "install"


def foundation_python() -> Path:
    if sys.platform == "win32":
        return foundation_prefix() / "python" / "Scripts" / "python.exe"
    return foundation_prefix() / "python" / "bin" / "python"


def paths() -> dict[str, Path]:
    base = workspace()
    config = base / "config" / "service"
    runtime = base / "runtime"
    return {
        "base": base,
        "config": config,
        "offset": config / "offset",
        "docker": base / "docker",
        "runtime": runtime,
        "logs": base / "logs",
        "validation": base / "validation",
        "binding": config / "service-binding.json",
        "transport": config / "service-transport.json",
        "runtime_config": config / "runtime.json",
        "session": runtime / "session.json",
        "host_session": runtime / "host-session.json",
        "stop_request": runtime / "host-stop-request.json",
        "host_log": base / "logs" / "host-rpc.log",
        "container_log": base / "logs" / "container.log",
        "evidence": base / "validation" / "evidence.json",
    }


def atomic_json(path: Path, data: object) -> None:
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


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecipeError(f"cannot read {path}: {error}") from error
    if not isinstance(data, dict):
        raise RecipeError(f"expected JSON object: {path}")
    return data


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print(">", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
        env=env,
    )


def ensure_layout() -> dict[str, Path]:
    resolved = paths()
    for key in ("config", "offset", "docker", "runtime", "logs", "validation"):
        resolved[key].mkdir(parents=True, exist_ok=True)
    return resolved


def exposure(address: str) -> str:
    if address in {"0.0.0.0", "::"}:
        return "wildcard"
    try:
        return "loopback" if ipaddress.ip_address(address).is_loopback else "non-loopback"
    except ValueError as error:
        raise RecipeError(f"bind address must be an IP literal: {address}") from error


def write_user_configs(config_dir: Path, bind: str, port: int) -> tuple[Path, Path]:
    transport = config_dir / "service-transport.json"
    binding = config_dir / "service-binding.json"
    atomic_json(
        transport,
        {
            "protocol": "tcp",
            "packetVersion": "v2",
            "queueDepth": 16,
            "endpoints": {
                "hakoniwa-pdu-ros-service": {
                    "role": "client",
                    "remote": {
                        "address": "host.docker.internal",
                        "port": port,
                    },
                    "options": {
                        "connect_timeout_ms": 2000,
                        "read_timeout_ms": 1000,
                        "write_timeout_ms": 1000,
                    },
                },
                "server_node": {
                    "role": "server",
                    "local": {"address": bind, "port": port},
                    "options": {
                        "read_timeout_ms": 1000,
                        "write_timeout_ms": 1000,
                    },
                },
            },
        },
    )
    atomic_json(
        binding,
        {
            "$schema": str(
                sibling("hakoniwa-pdu-ros")
                / "schema"
                / "service-binding.schema.json"
            ),
            "version": 1,
            "service": {
                "transport_config": transport.name,
                "delta_time_usec": 1000,
                "time_source_type": "real",
            },
            "bindings": [
                {
                    "ros_name": "/add_two_ints",
                    "ros_type": "example_interfaces/srv/AddTwoInts",
                    "hakoniwa_service": "Service/Add",
                    "pdu_service_type": "hako_srv_msgs/AddTwoInts",
                    "client_endpoint": {
                        "node_id": "hakoniwa-pdu-ros-service"
                    },
                    "server_endpoint": {"node_id": "server_node"},
                    "max_clients": 4,
                    "timeout_msec": 3000,
                }
            ],
        },
    )
    return binding, transport


def copy_offsets(destination: Path) -> None:
    source = sibling("hakoniwa-pdu-python") / "tests" / "config" / "offset" / "hako_srv_msgs"
    target = destination / "hako_srv_msgs"
    target.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_OFFSETS:
        origin = source / name
        if not origin.is_file():
            raise RecipeError(f"required offset is missing: {origin}")
        shutil.copy2(origin, target / name)


def generate_service_configs(binding: Path, offset: Path, output: Path) -> None:
    ros_repo = sibling("hakoniwa-pdu-ros")
    sys.path.insert(0, str(ros_repo))
    try:
        module = importlib.import_module("hakoniwa_pdu_ros.service_config_generator")
        module.generate_service_configs(
            binding,
            output_dir=output,
            offset_dir=offset,
            ros_interface_resolver=lambda _value: None,
            pdu_type_resolver=lambda _ros, explicit: explicit or "hako_srv_msgs/AddTwoInts",
        )
    finally:
        try:
            sys.path.remove(str(ros_repo))
        except ValueError:
            pass


def remove_legacy_generated_files(config_dir: Path) -> None:
    """Remove files emitted by the pre-generator Recipe implementation."""
    for name in LEGACY_GENERATED_FILES:
        candidate = config_dir / name
        if candidate.is_file():
            candidate.unlink()


def dockerfile_text() -> str:
    return """FROM ros:jazzy-ros-base-noble

ENV DEBIAN_FRONTEND=noninteractive
ENV CMAKE_BUILD_PARALLEL_LEVEL=2
RUN apt-get update && apt-get install -y --no-install-recommends \\
    cmake g++ git libboost-dev python3-venv ros-jazzy-example-interfaces \\
    ros-jazzy-rmw-cyclonedds-cpp && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/hakoniwa/python && \\
    /opt/hakoniwa/python/bin/python -m pip install --upgrade \\
      pip wheel cffi "setuptools<80"

COPY --from=endpoint . /opt/src/hakoniwa-pdu-endpoint
COPY endpoint-build.yaml /opt/config/endpoint-build.yaml
RUN /opt/hakoniwa/python/bin/python /opt/src/hakoniwa-pdu-endpoint/tools/hako.py configure \\
      --config /opt/config/endpoint-build.yaml --install-dir /opt/hakoniwa && \\
    /opt/hakoniwa/python/bin/python /opt/src/hakoniwa-pdu-endpoint/tools/hako.py build \\
      --config /opt/config/endpoint-build.yaml --install-dir /opt/hakoniwa && \\
    /opt/hakoniwa/python/bin/python /opt/src/hakoniwa-pdu-endpoint/tools/hako.py install \\
      --config /opt/config/endpoint-build.yaml --install-dir /opt/hakoniwa

COPY --from=rpc . /opt/src/hakoniwa-pdu-rpc
COPY rpc-build.yaml /opt/config/rpc-build.yaml
RUN /opt/hakoniwa/python/bin/python /opt/src/hakoniwa-pdu-rpc/tools/hako.py build \\
      --config /opt/config/rpc-build.yaml --endpoint-root /opt/hakoniwa && \\
    /opt/hakoniwa/python/bin/python /opt/src/hakoniwa-pdu-rpc/tools/hako.py install \\
      --config /opt/config/rpc-build.yaml --endpoint-root /opt/hakoniwa \\
      --install-dir /opt/hakoniwa --python-venv /opt/hakoniwa/python

COPY --from=pdu-python . /opt/src/hakoniwa-pdu-python
COPY --from=pdu-ros . /opt/src/hakoniwa-pdu-ros
RUN /opt/hakoniwa/python/bin/python -m pip install --no-build-isolation \\
      /opt/src/hakoniwa-pdu-python /opt/src/hakoniwa-pdu-ros

ENV PATH=/opt/hakoniwa/python/bin:${PATH} \\
    HAKO_PDU_RPC_LIBRARY=/opt/hakoniwa/lib/libhakoniwa_pdu_rpc.so \\
    LD_LIBRARY_PATH=/opt/hakoniwa/lib \\
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \\
    ROS_DOMAIN_ID=73 \\
    PYTHONUNBUFFERED=1
WORKDIR /recipe
CMD ["bash", "-lc", "source /opt/ros/jazzy/setup.bash && exec service-server --config /recipe/config/service-binding.json --offset-dir /recipe/config/offset --output-dir /recipe/config --rpc-library /opt/hakoniwa/lib/libhakoniwa_pdu_rpc.so"]
"""


def configure(args: argparse.Namespace) -> None:
    p = ensure_layout()
    bind = args.bind_address or DEFAULT_BIND
    port = args.port or DEFAULT_PORT
    scope = exposure(bind)
    approved = bool(args.approve_non_loopback_bind)
    atomic_json(
        p["runtime_config"],
        {
            "schema_version": 1,
            "bind_address": bind,
            "port": port,
            "exposure": scope,
            "approve_non_loopback_bind": approved,
            "container_alias": "host.docker.internal",
            "container_name": CONTAINER,
            "image": IMAGE,
        },
    )
    write_user_configs(p["config"], bind, port)
    copy_offsets(p["offset"])
    remove_legacy_generated_files(p["config"])
    generate_service_configs(p["binding"], p["offset"], p["config"])
    docker = p["docker"]
    (docker / "Dockerfile").write_text(dockerfile_text(), encoding="utf-8")
    (docker / "endpoint-build.yaml").write_text(
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
    (docker / "rpc-build.yaml").write_text(
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
    print(f"Configured Recipe workspace: {p['base']}")
    print(f"Host bind candidate: {bind}:{port} ({scope})")
    if scope != "loopback" and not approved:
        print("Approval required before start: --approve-non-loopback-bind")


def rpc_library(prefix: Path) -> Path:
    candidates = (
        prefix / "lib" / "libhakoniwa_pdu_rpc.dylib",
        prefix / "lib" / "libhakoniwa_pdu_rpc.so",
        prefix / "bin" / "hakoniwa_pdu_rpc.dll",
        prefix / "bin" / "libhakoniwa_pdu_rpc.dll",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RecipeError(f"PDU-RPC shared library is missing under {prefix}")


def python_env() -> dict[str, str]:
    env = os.environ.copy()
    library_dir = str(foundation_prefix() / ("bin" if sys.platform == "win32" else "lib"))
    for name in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        env[name] = library_dir + os.pathsep + env.get(name, "")
    env["HAKO_PDU_RPC_LIBRARY"] = str(rpc_library(foundation_prefix()))
    return env


def install_host_python() -> None:
    interpreter = foundation_python()
    if not interpreter.is_file():
        raise RecipeError("Foundation Python is missing; build the host Foundation first")
    project = sibling("hakoniwa-pdu-python")
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    required_version = str(metadata["project"]["version"])
    check = subprocess.run(
        [
            str(interpreter),
            "-c",
            "from importlib.metadata import version; "
            "from hakoniwa_pdu.pdu_msgs.sample_action_msgs "
            "import pdu_pytype_FibonacciActionRequest; "
            "print(version('hakoniwa-pdu'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode == 0 and check.stdout.strip() == required_version:
        print(f"hakoniwa-pdu {required_version} is already installed; reusing it")
        return
    run(
        [
            str(interpreter),
            "-m",
            "pip",
            "install",
            "--upgrade",
            str(project),
        ]
    )


def build(args: argparse.Namespace) -> None:
    p = paths()
    if not p["runtime_config"].is_file():
        configure(args)
    driver = foundation_python() if foundation_python().is_file() else Path(sys.executable)
    run(
        [
            str(driver),
            str(root() / "tools" / "foundation.py"),
            "build",
            "--recipe",
            str(root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"),
        ],
        cwd=root(),
    )
    install_host_python()
    command = [
        "docker",
        "build",
        "--file",
        str(p["docker"] / "Dockerfile"),
        "--tag",
        IMAGE,
        "--build-context",
        f"endpoint={sibling('hakoniwa-pdu-endpoint')}",
        "--build-context",
        f"rpc={sibling('hakoniwa-pdu-rpc')}",
        "--build-context",
        f"pdu-python={sibling('hakoniwa-pdu-python')}",
        "--build-context",
        f"pdu-ros={sibling('hakoniwa-pdu-ros')}",
        str(p["docker"]),
    ]
    run(command)
    print("Build completed. Run doctor before start.")


def command_ok(command: list[str], *, env: dict[str, str] | None = None) -> tuple[bool, str]:
    result = run(command, capture=True, check=False, env=env)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repository_evidence(path: Path) -> dict[str, object]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    return {"revision": revision, "dirty": dirty}


def tcp_listener_reachable(bind_address: str, port: int) -> bool:
    target = "127.0.0.1" if bind_address == "0.0.0.0" else bind_address
    target = "::1" if target == "::" else target
    try:
        with socket.create_connection((target, port), timeout=0.25):
            return True
    except OSError:
        return False


def tcp_port_available(bind_address: str, port: int) -> tuple[bool, str]:
    family = socket.AF_INET6 if ":" in bind_address else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((bind_address, port))
    except OSError as error:
        return False, str(error)
    return True, f"{bind_address}:{port} is available"


def doctor(_args: argparse.Namespace) -> int:
    p = paths()
    checks: list[tuple[str, bool, str]] = []
    recipe = root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"
    if foundation_python().is_file():
        ok, output = command_ok(
            [
                str(foundation_python()),
                str(root() / "tools" / "foundation.py"),
                "doctor",
                "--recipe",
                str(recipe),
            ]
        )
        checks.append(("host-foundation", ok, output))
        if ok:
            ok, output = command_ok(
                [
                    str(foundation_python()),
                    "-c",
                    "import hakoniwa_pdu, hakoniwa_pdu_rpc; "
                    "from hakoniwa_pdu_rpc import RpcMuxServer; "
                    "from hakoniwa_pdu.pdu_msgs.hako_srv_msgs.pdu_pytype_AddTwoIntsRequestPacket import AddTwoIntsRequestPacket; "
                    "print('core-free Python imports OK')",
                ],
                env=python_env(),
            )
            checks.append(("host-python", ok, output))
    else:
        checks.append(("host-foundation", False, "Foundation Python is missing"))

    required = [p["runtime_config"], p["binding"], p["transport"]]
    required.extend(p["offset"] / "hako_srv_msgs" / name for name in REQUIRED_OFFSETS)
    missing = [str(path) for path in required if not path.is_file()]
    checks.append(("recipe-config", not missing, "missing: " + ", ".join(missing) if missing else "generated files present"))
    if p["runtime_config"].is_file():
        runtime = load_json(p["runtime_config"])
        available, detail = tcp_port_available(
            runtime["bind_address"], int(runtime["port"])
        )
        checks.append(("host-rpc-port", available, detail))
    ok, output = command_ok(["docker", "image", "inspect", IMAGE])
    checks.append(("container-image", ok, output if not ok else IMAGE))
    ok, output = command_ok(["docker", "run", "--rm", "--add-host", "host.docker.internal:host-gateway", IMAGE, "getent", "hosts", "host.docker.internal"]) if ok else (False, "image is missing")
    checks.append(("container-host-alias", ok, output))

    failed = False
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def managed_host_alive(session: dict, host_session_path: Path) -> bool:
    if not host_session_path.is_file():
        return False
    try:
        host_session = load_json(host_session_path)
    except RecipeError:
        return False
    return (
        host_session.get("state") == "RUNNING"
        and host_session.get("token") == session.get("host_token")
        and host_session.get("pid") == session.get("host_pid")
        and pid_alive(session.get("host_pid"))
    )


def background_process_options() -> dict[str, object]:
    """Detach the host worker from the short-lived start command."""
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def wait_json_state(path: Path, states: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                data = load_json(path)
            except RecipeError:
                time.sleep(0.1)
                continue
            if data.get("state") in states:
                return data
        time.sleep(0.1)
    raise RecipeError(f"timed out waiting for {states} in {path}")


def start(args: argparse.Namespace) -> None:
    p = paths()
    config = load_json(p["runtime_config"])
    scope = config["exposure"]
    approved = bool(config.get("approve_non_loopback_bind")) or bool(
        args.approve_non_loopback_bind
    )
    if scope != "loopback" and not approved:
        raise RecipeError(
            f"start refused: bind {config['bind_address']} is {scope}; "
            "repeat with --approve-non-loopback-bind"
        )
    if p["session"].is_file():
        old = load_json(p["session"])
        if old.get("state") == "RUNNING" and managed_host_alive(
            old, p["host_session"]
        ):
            raise RecipeError("Recipe session is already RUNNING")

    for stale in (p["host_session"], p["stop_request"]):
        if stale.exists():
            stale.unlink()
    token = secrets.token_hex(16)
    host_log = p["host_log"].open("w", encoding="utf-8")
    host_command = [
        str(foundation_python()),
        str(root() / "tools" / "recipe" / "ros2_service_add_two_ints_host.py"),
        "--service-config",
        str(p["config"] / "rpc-server-services.json"),
        "--endpoint-config",
        str(p["config"] / "endpoints.json"),
        "--rpc-library",
        str(rpc_library(foundation_prefix())),
        "--session",
        str(p["host_session"]),
        "--stop-request",
        str(p["stop_request"]),
        "--token",
        token,
    ]
    process = subprocess.Popen(
        host_command,
        cwd=root(),
        stdout=host_log,
        stderr=subprocess.STDOUT,
        env=python_env(),
        text=True,
        **background_process_options(),
    )
    host_log.close()
    try:
        state = wait_json_state(p["host_session"], {"RUNNING", "FAILED"}, 15)
        if state["state"] != "RUNNING":
            raise RecipeError(f"host RPC startup failed: {state}")
        run(
            [
                "docker",
                "run",
                "--rm",
                "--init",
                "--detach",
                "--name",
                CONTAINER,
                "--add-host",
                "host.docker.internal:host-gateway",
                "--mount",
                # The Service Node deterministically regenerates its RPC
                # configs at startup, so this Recipe-owned work directory must
                # be writable from the container.
                f"type=bind,source={p['config']},target=/recipe/config",
                IMAGE,
            ]
        )
        deadline = time.monotonic() + 30
        logs = ""
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "logs", CONTAINER],
                capture_output=True,
                text=True,
                check=False,
            )
            logs = result.stdout + result.stderr
            if "service ready: ros_name=/add_two_ints" in logs:
                break
            if result.returncode != 0:
                time.sleep(0.5)
                continue
            time.sleep(0.5)
        else:
            p["container_log"].write_text(logs, encoding="utf-8")
            raise RecipeError("ROS Service Bridge did not become ready")
        atomic_json(
            p["session"],
            {
                "schema_version": 1,
                "state": "RUNNING",
                "host_pid": process.pid,
                "host_token": token,
                "container": CONTAINER,
                "bind_address": config["bind_address"],
                "port": config["port"],
                "approval": approved,
            },
        )
    except BaseException:
        atomic_json(p["stop_request"], {"command": "stop", "token": token})
        subprocess.run(["docker", "stop", CONTAINER], check=False, capture_output=True)
        raise
    print("Start command returned; the Recipe remains running in the background.")
    print(f"Session: {p['session']}")
    print("Next: smoke, status, then stop")


def status(_args: argparse.Namespace) -> int:
    p = paths()
    if not p["session"].is_file():
        print("Recipe session: MISSING")
        return 1
    session = load_json(p["session"])
    host = managed_host_alive(session, p["host_session"])
    container, detail = command_ok(["docker", "inspect", "--format", "{{.State.Running}}", CONTAINER])
    container = container and detail.strip() == "true"
    print(f"Recipe session: {session.get('state')}")
    print(f"Host RPC: {'RUNNING' if host else 'STOPPED'}")
    print(f"ROS container: {'RUNNING' if container else 'STOPPED'}")
    return 0 if session.get("state") == "RUNNING" and host and container else 1


def smoke(args: argparse.Namespace) -> int:
    p = paths()
    result = run(
        [
            "docker",
            "exec",
            CONTAINER,
            "bash",
            "-lc",
            "source /opt/ros/jazzy/setup.bash && "
            "ros2 service list && "
            "ros2 service type /add_two_ints && "
            "ros2 service call /add_two_ints example_interfaces/srv/AddTwoInts "
            f"'{{a: {args.a}, b: {args.b}}}'",
        ],
        capture=True,
        check=False,
    )
    output = result.stdout + result.stderr
    print(output, end="" if output.endswith("\n") else "\n")
    expected = args.a + args.b
    normalized = output.replace(" ", "")
    ok = result.returncode == 0 and (
        f"sum={expected}" in normalized or f"sum:{expected}" in normalized
    )
    runtime_config = load_json(p["runtime_config"])
    runtime_session = load_json(p["session"])
    receipt_dir = foundation_prefix() / "share" / "hakoniwa" / "receipts"
    receipt_paths = [
        receipt_dir / "hakoniwa-pdu-endpoint.yaml",
        receipt_dir / "hakoniwa-pdu-rpc.yaml",
    ]
    config_paths = sorted(
        path for path in p["config"].glob("*.json") if path.is_file()
    )
    image_ok, image_id = command_ok(
        ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]
    )
    repositories = {
        "hakoniwa-business-pack": root(),
        "hakoniwa-pdu-endpoint": sibling("hakoniwa-pdu-endpoint"),
        "hakoniwa-pdu-rpc": sibling("hakoniwa-pdu-rpc"),
        "hakoniwa-pdu-python": sibling("hakoniwa-pdu-python"),
        "hakoniwa-pdu-ros": sibling("hakoniwa-pdu-ros"),
    }
    evidence = {
        "schema_version": 1,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request": {"a": args.a, "b": args.b},
        "response": {"sum": expected} if ok else None,
        "result": "PASS" if ok else "FAIL",
        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "host": {"os": platform.system(), "architecture": platform.machine()},
        "network": {
            "bind_address": runtime_config["bind_address"],
            "port": runtime_config["port"],
            "exposure": runtime_config["exposure"],
            "approval": bool(runtime_session.get("approval")),
            "container_alias": runtime_config["container_alias"],
            "extra_host": "host.docker.internal:host-gateway",
        },
        "repositories": {
            name: repository_evidence(path) for name, path in repositories.items()
        },
        "host_receipts": [
            {
                "path": str(path.relative_to(root())),
                "sha256": sha256_file(path),
            }
            for path in receipt_paths
            if path.is_file()
        ],
        "config_sha256": {
            str(path.relative_to(p["base"])): sha256_file(path)
            for path in config_paths
        },
        "container": {
            "image": IMAGE,
            "image_id": image_id if image_ok else None,
            "ros_distro": "jazzy",
            "platform": "linux",
        },
        "cleanup": {"status": "PENDING"},
    }
    atomic_json(p["evidence"], evidence)
    return 0 if ok else 1


def stop(_args: argparse.Namespace) -> int:
    p = paths()
    if not p["session"].is_file():
        print("Recipe session is already absent.")
        return 0
    session = load_json(p["session"])
    subprocess.run(["docker", "stop", CONTAINER], check=False)
    token = session.get("host_token")
    if isinstance(token, str):
        atomic_json(p["stop_request"], {"command": "stop", "token": token})
    deadline = time.monotonic() + 10
    while managed_host_alive(session, p["host_session"]) and time.monotonic() < deadline:
        time.sleep(0.1)
    host_stopped = not managed_host_alive(session, p["host_session"])
    container_running, _ = command_ok(
        ["docker", "inspect", "--format", "{{.State.Running}}", CONTAINER]
    )
    config = load_json(p["runtime_config"])
    listener_stopped = not tcp_listener_reachable(
        config["bind_address"], int(config["port"])
    )
    session["state"] = (
        "TERMINATED"
        if host_stopped and not container_running and listener_stopped
        else "STOP_FAILED"
    )
    session["terminated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_json(p["session"], session)
    if p["evidence"].is_file():
        evidence = load_json(p["evidence"])
        evidence["cleanup"] = {
            "status": "PASS" if session["state"] == "TERMINATED" else "FAIL",
            "host_process_stopped": host_stopped,
            "container_stopped": not container_running,
            "listener_stopped": listener_stopped,
            "session_state": session["state"],
            "timestamp": session["terminated_at"],
        }
        atomic_json(p["evidence"], evidence)
    print(f"Recipe session: {session['state']}")
    return 0 if session["state"] == "TERMINATED" else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("configure", "build", "start"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--bind-address", default=None)
        subparser.add_argument("--port", type=int, default=None)
        subparser.add_argument("--approve-non-loopback-bind", action="store_true")
    subparsers.add_parser("doctor")
    subparsers.add_parser("status")
    subparsers.add_parser("stop")
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--a", type=int, default=20)
    smoke_parser.add_argument("--b", type=int, default=22)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handlers = {
        "configure": configure,
        "build": build,
        "doctor": doctor,
        "start": start,
        "smoke": smoke,
        "status": status,
        "stop": stop,
    }
    try:
        result = handlers[args.command](args)
        return int(result or 0)
    except RecipeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
