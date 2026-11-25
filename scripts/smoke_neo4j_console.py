"""Exercise the analyst console against a real, dedicated test Neo4j instance."""
import argparse
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import socket
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from urllib.request import urlopen

import uvicorn

from src.api.case_neo4j import Neo4jCaseGraph
from src.api.case_review import create_app


def run(uri, screenshots):
    credentials = {
        name: {"role": role, "token": token_urlsafe(32)}
        for name, role in [("console-admin-qa", "admin"), ("console-viewer-qa", "viewer")]
    }
    node_env = {
        **os.environ,
        "CASE_SMOKE_CREDENTIALS": json.dumps(credentials),
        "CASE_SMOKE_SCREENSHOTS": str(screenshots) if screenshots else "",
    }
    # Browser only needs the case-review tokens.
    for key in list(node_env):
        if key.startswith(("FRAUD_CASE_NEO4J", "FRAUD_TEST_NEO4J")):
            del node_env[key]
    with TemporaryDirectory(prefix="neo4j-console-smoke-") as directory:
        graph = Neo4jCaseGraph(
            uri,
            os.getenv("FRAUD_TEST_NEO4J_USER", "neo4j"),
            os.environ["FRAUD_TEST_NEO4J_PASSWORD"],
            os.getenv("FRAUD_TEST_NEO4J_DATABASE", "neo4j"),
        )
        app = create_app(
            Path(directory) / "case-review.sqlite3", credentials=credentials, graph_backend=graph,
        )
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_level="warning", access_log=False))
        smoke = Path(__file__).resolve().parents[1] / "tests" / "neo4j_console_smoke.cjs"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            worker.start()
            try:
                deadline = monotonic() + 8
                while True:
                    try:
                        with urlopen(base_url + "/health", timeout=0.5) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if monotonic() > deadline:
                            raise
                        sleep(0.1)
                subprocess.run(["node", str(smoke), base_url], env=node_env, timeout=75, check=True)
            finally:
                server.should_exit = True
                worker.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    uri = os.getenv("FRAUD_TEST_NEO4J_URI")
    if not uri:
        parser.error("Set FRAUD_TEST_NEO4J_URI to a dedicated test instance.")
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    run(uri, args.screenshots)


if __name__ == "__main__":
    main()
