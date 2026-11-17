"""A stand-in for the dbt Cloud API, served over HTTPS on localhost so CI can run the Action's dbt-cloud path.

Runs are read from <root>/runs, laid out as for --source local, and every request is logged to
<root>/requests.log so `check` can say which runs had artifacts downloaded.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PORT = 8443
SYSTEM_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem")
RUNS = re.compile(r"/api/v2/accounts/\d+/runs/")
RUN = re.compile(r"/api/v2/accounts/\d+/runs/(\d+)/")
ARTIFACT = re.compile(r"/api/v2/accounts/\d+/runs/(\d+)/artifacts/(manifest\.json|run_results\.json)")
DOWNLOAD = re.compile(r"200 \S*/runs/(\d+)/artifacts/")
FIXED_STEPS = ("Clone git repository", "Invoke dbt with `dbt deps`")
NOT_FOUND = {"status": {"code": 404, "user_message": "Not found."}}

CA_CONFIG = """\
[req]
distinguished_name = dn
x509_extensions = ca
prompt = no
[dn]
CN = broom-wagon test CA
[ca]
basicConstraints = critical, CA:true
keyUsage = critical, keyCertSign
subjectKeyIdentifier = hash
"""

SERVER_EXTENSIONS = """\
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


def make_certs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ca.cnf").write_text(CA_CONFIG)
    (root / "server.ext").write_text(SERVER_EXTENSIONS)
    for args in (
        ["req", "-x509", "-new", "-nodes", "-newkey", "rsa:2048", "-days", "1", "-config", "ca.cnf"]
        + ["-keyout", "ca.key", "-out", "ca.pem"],
        ["req", "-new", "-nodes", "-newkey", "rsa:2048", "-subj", "/CN=localhost"]
        + ["-keyout", "server.key", "-out", "server.csr"],
        ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-days", "1"]
        + ["-extfile", "server.ext", "-out", "server.pem"],
    ):
        result = subprocess.run(["openssl", *args], cwd=root, capture_output=True, text=True)
        if result.returncode:
            sys.exit(result.stderr)
    # uv and gh read SSL_CERT_FILE too, so the bundle keeps the system roots beside the test CA.
    candidates = (ssl.get_default_verify_paths().cafile, *SYSTEM_BUNDLES)
    system = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
    if system is None:
        sys.exit("no system CA bundle found to add the test CA to")
    (root / "bundle.pem").write_bytes(system.read_bytes() + b"\n" + (root / "ca.pem").read_bytes())


def run_results_files(run_dir: Path) -> list[Path]:
    def order(path: Path) -> int:
        parts = path.name.split(".")
        return int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0

    return sorted(run_dir.glob("run_results*.json"), key=order)


class FakeCloud(ThreadingHTTPServer):
    def __init__(self, root: Path) -> None:
        super().__init__(("localhost", PORT), Handler)
        self.root = root


class Handler(BaseHTTPRequestHandler):
    server: FakeCloud

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if self.headers.get("Authorization", "").startswith("Token "):
            status, body = self.route(url.path, parse_qs(url.query))
        else:
            status, body = 401, {"status": {"code": 401, "user_message": "Invalid token."}}
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        if url.path.startswith("/api/"):
            with open(self.server.root / "requests.log", "a") as log:
                log.write(f"{status} {self.path}\n")

    def route(self, path: str, query: dict[str, list[str]]) -> tuple[int, dict | bytes]:
        runs = self.server.root / "runs"
        if match := ARTIFACT.fullmatch(path):
            run_dir = runs / match[1]
            builds = run_results_files(run_dir)
            build = int(query.get("step", ["0"])[0]) - len(FIXED_STEPS) - 1
            if not 0 <= build < len(builds):
                return 404, NOT_FOUND
            file = builds[build] if match[2] == "run_results.json" else run_dir / "manifest.json"
            return (200, file.read_bytes()) if file.is_file() else (404, NOT_FOUND)
        if match := RUN.fullmatch(path):
            run_dir = runs / match[1]
            if not run_dir.is_dir():
                return 404, NOT_FOUND
            names = [*FIXED_STEPS] + ["Invoke dbt with `dbt build`"] * len(run_results_files(run_dir))
            steps = [{"index": i, "name": name, "status": 10} for i, name in enumerate(names, 1)]
            return 200, {"data": {"id": int(match[1]), "status": 10, "artifacts_saved": True, "run_steps": steps}}
        if RUNS.fullmatch(path):
            ids = sorted((int(p.name) for p in runs.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)
            offset, limit = int(query["offset"][0]), int(query["limit"][0])
            return 200, {"data": [{"id": run_id, "status": 10} for run_id in ids[offset : offset + limit]]}
        return 404, NOT_FOUND

    def log_message(self, format: str, *args: object) -> None:
        pass


def serve(root: Path) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(root / "server.pem", root / "server.key")
    server = FakeCloud(root)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


def check(root: Path, expected: list[int]) -> int:
    """Compare the runs whose artifacts were downloaded with `expected`, then empty the request log."""
    log = root / "requests.log"
    lines = log.read_text().splitlines() if log.exists() else []
    log.write_text("")
    downloaded = sorted({int(match[1]) for line in lines if (match := DOWNLOAD.match(line))})
    print(f"{len(lines)} requests, artifacts downloaded for runs {downloaded}")
    if not lines:
        print("::error::the Action sent no requests to the fake dbt Cloud")
        return 1
    if downloaded != sorted(expected):
        print(f"::error::expected artifact downloads for runs {sorted(expected)}, got {downloaded}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("certs").add_argument("root", type=Path)
    commands.add_parser("serve").add_argument("root", type=Path)
    check_parser = commands.add_parser("check")
    check_parser.add_argument("root", type=Path)
    check_parser.add_argument("runs", type=int, nargs="*")
    args = parser.parse_args()
    if args.command == "certs":
        make_certs(args.root)
    elif args.command == "serve":
        serve(args.root)
    else:
        return check(args.root, args.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
