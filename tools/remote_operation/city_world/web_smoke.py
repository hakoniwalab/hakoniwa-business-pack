"""Serve only the City World smoke UI and hakoniwa-pdu-javascript modules."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .protocol import validate_request, validate_result


WEB_ROOT = Path(__file__).resolve().parent / "web"
BUSINESS_PACK_ROOT = Path(__file__).resolve().parents[3]
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def resolve_pdu_javascript_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.resolve()
    elif os.environ.get("HAKONIWA_PDU_JAVASCRIPT_ROOT"):
        root = Path(os.environ["HAKONIWA_PDU_JAVASCRIPT_ROOT"]).resolve()
    else:
        root = (Path(__file__).resolve().parents[4] / "hakoniwa-pdu-javascript").resolve()
    source = root / "src"
    if not (source / "index.js").is_file():
        raise RuntimeError(f"hakoniwa-pdu-javascript source not found: {source}")
    return source


def resolve_worker_runtime_root(explicit: Path | None = None) -> Path:
    candidate = explicit or Path("work/remote-operation/city-world-worker")
    if not candidate.is_absolute():
        candidate = BUSINESS_PACK_ROOT / candidate
    return candidate.resolve()


def list_generated_jobs(worker_runtime_root: Path) -> list[dict]:
    output = []
    for manifest_path in (worker_runtime_root / "jobs").glob("*/artifacts/result-manifest.json"):
        try:
            result = validate_result(json.loads(manifest_path.read_text(encoding="utf-8")))
            job_root = manifest_path.parents[1]
            artifact = manifest_path.parent / result["artifact_name"]
            visual = job_root / "viewer" / "city-world.glb"
            colliders = job_root / "viewer" / "city-world-colliders.glb"
            if not artifact.is_file() or not visual.is_file():
                continue
            selection = None
            try:
                job_record = json.loads((job_root / "job.json").read_text(encoding="utf-8"))
                request = validate_request(job_record["request"])
                selection = request["selection"]
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                # Keep older generated artifacts downloadable even when they
                # predate selection metadata in the generated index.
                pass
            try:
                relative = job_root.resolve().relative_to(BUSINESS_PACK_ROOT).as_posix()
            except ValueError:
                relative = f"jobs/{result['job_id']}"
            output.append({
                "job_id": result["job_id"],
                "artifact_name": result["artifact_name"],
                "size_bytes": result["size_bytes"],
                "sha256": result["sha256"],
                "server_relative_path": relative,
                "collider_available": colliders.is_file(),
                "building_physics_level": result.get("building_physics_level"),
                "colliders": result.get("colliders"),
                "selection": selection,
                "updated_at_msec": int(manifest_path.stat().st_mtime * 1000),
            })
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(output, key=lambda item: (-item["updated_at_msec"], item["job_id"]))


def shared_cache_summary(worker_runtime_root: Path) -> dict:
    cache_root = (worker_runtime_root / "cache" / "plateau-citygml").resolve()
    try:
        relative = cache_root.relative_to(BUSINESS_PACK_ROOT).as_posix()
    except ValueError:
        relative = str(cache_root)
    objects = [
        path for path in (cache_root / "objects").glob("*/*")
        if path.is_file() and not path.name.endswith(".cache.json")
    ]
    return {
        "server_relative_path": relative,
        "object_count": len(objects),
        "size_bytes": sum(path.stat().st_size for path in objects),
    }


def resolve_generated_asset(worker_runtime_root: Path, job_id: str, kind: str) -> Path | None:
    if JOB_ID_RE.fullmatch(job_id) is None:
        return None
    job_root = (worker_runtime_root / "jobs" / job_id).resolve()
    jobs_root = (worker_runtime_root / "jobs").resolve()
    if jobs_root not in job_root.parents:
        return None
    manifest_path = job_root / "artifacts" / "result-manifest.json"
    try:
        result = validate_result(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if result["job_id"] != job_id:
        return None
    path = (
        job_root / "viewer" / "city-world.glb"
        if kind == "glb"
        else job_root / "viewer" / "city-world-colliders.glb"
        if kind == "collider"
        else job_root / "artifacts" / result["artifact_name"]
        if kind == "zip"
        else None
    )
    return path if path is not None and path.is_file() else None


def delete_generated_job(worker_runtime_root: Path, job_id: str) -> bool:
    """Delete one completed job while preserving the shared source cache."""
    if JOB_ID_RE.fullmatch(job_id) is None:
        return False
    jobs_root = (worker_runtime_root / "jobs").resolve()
    job_root = (jobs_root / job_id).resolve()
    if jobs_root not in job_root.parents:
        return False
    manifest_path = job_root / "artifacts" / "result-manifest.json"
    try:
        result = validate_result(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if result["job_id"] != job_id:
        return False
    shutil.rmtree(job_root)
    return True


def handler_factory(pdu_js_root: Path, worker_runtime_root: Path):
    class Handler(SimpleHTTPRequestHandler):
        def _send_bytes(self, payload: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            route = unquote(urlsplit(self.path).path)
            if route == "/generated/index.json":
                payload = json.dumps(
                    {
                        "jobs": list_generated_jobs(worker_runtime_root),
                        "shared_cache": shared_cache_summary(worker_runtime_root),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self._send_bytes(payload, "application/json; charset=utf-8")
                return
            match = re.fullmatch(
                r"/generated/([^/]+)/(city-world\.glb|city-world-colliders\.glb|artifact\.zip)",
                route,
            )
            if match:
                kind = (
                    "collider" if match.group(2) == "city-world-colliders.glb"
                    else "glb" if match.group(2).endswith(".glb")
                    else "zip"
                )
                path = resolve_generated_asset(worker_runtime_root, match.group(1), kind)
                if path is None:
                    self.send_error(404, "Generated City World not found")
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "model/gltf-binary" if kind in {"glb", "collider"} else "application/zip",
                )
                self.send_header("Content-Length", str(path.stat().st_size))
                if kind == "zip":
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.end_headers()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        self.wfile.write(chunk)
                return
            super().do_GET()

        def do_HEAD(self) -> None:
            route = unquote(urlsplit(self.path).path)
            match = re.fullmatch(
                r"/generated/([^/]+)/(city-world\.glb|city-world-colliders\.glb|artifact\.zip)",
                route,
            )
            if match:
                kind = (
                    "collider" if match.group(2) == "city-world-colliders.glb"
                    else "glb" if match.group(2).endswith(".glb")
                    else "zip"
                )
                path = resolve_generated_asset(worker_runtime_root, match.group(1), kind)
                if path is None:
                    self.send_error(404, "Generated City World not found")
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "model/gltf-binary" if kind in {"glb", "collider"} else "application/zip",
                )
                self.send_header("Content-Length", str(path.stat().st_size))
                if kind == "zip":
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.end_headers()
                return
            super().do_HEAD()

        def do_DELETE(self) -> None:
            route = unquote(urlsplit(self.path).path)
            match = re.fullmatch(r"/generated/([^/]+)", route)
            if match is None:
                self.send_error(404, "Generated City World not found")
                return
            job_id = match.group(1)
            if not delete_generated_job(worker_runtime_root, job_id):
                self.send_error(404, "Generated City World not found")
                return
            payload = json.dumps({
                "deleted": True,
                "job_id": job_id,
                "cache_preserved": True,
            }).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8")

        def translate_path(self, path: str) -> str:
            relative = unquote(urlsplit(path).path).lstrip("/")
            if relative.startswith("pdu-js/"):
                root = pdu_js_root
                relative = relative[len("pdu-js/"):]
            else:
                root = WEB_ROOT
                relative = relative or "index.html"
            candidate = (root / relative).resolve()
            if root != candidate and root not in candidate.parents:
                return str(root / "__invalid__")
            return str(candidate)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--pdu-javascript-root", type=Path)
    parser.add_argument(
        "--worker-runtime-dir", type=Path,
        default=Path("work/remote-operation/city-world-worker"),
    )
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    pdu_js_root = resolve_pdu_javascript_root(args.pdu_javascript_root)
    worker_runtime_root = resolve_worker_runtime_root(args.worker_runtime_dir)
    server = ThreadingHTTPServer(
        (args.listen_address, args.port), handler_factory(pdu_js_root, worker_runtime_root),
    )
    print(f"City World smoke UI: http://{args.listen_address}:{args.port}/")
    if args.ready_file is not None:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.write_text("ready\n", encoding="utf-8")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
