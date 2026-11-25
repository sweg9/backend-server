"""`backend <command>`: the one entry point of the hub, the server and the enclave (wiki: Modules)."""

import argparse
import getpass
import os
import sys


def _hub(args) -> int:
    from backend.common import logs
    from backend.common.settings import HubSettings, load
    from backend.hub.main import run

    s = load(HubSettings, "hub.yaml")
    if args.stdout:
        logs.setup("hub")
    else:
        logs.setup("hub", s.log_file, s.log_max_mb, s.log_keep_files)
        print(f"hub logs go to {s.log_file}", file=sys.stderr)
    demo = args.demo_scenarios or os.environ.get("DEMO_SCENARIOS") == "1"
    return run(s, args.server_url, demo)


def _seed_demo(args) -> int:
    from backend.common.settings import HubSettings, load
    from backend.hub.db.session import make_engine, make_sessionmaker
    from backend.hub.main import migrate
    from backend.hub.seed import NotEmpty, seed_demo

    s = load(HubSettings, "hub.yaml")
    migrate(s.database_url)
    with make_sessionmaker(make_engine(s.database_url))() as db:
        try:
            counts = seed_demo(db, s.time_zone)
        except NotEmpty as e:
            print(f"{e}; delete {s.database_url} first to start over", file=sys.stderr)
            return 1
    print("added the demo home: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


def _server(args) -> int:
    import uvicorn

    from backend.common import logs

    logs.setup("server")
    # One worker: the hub's one WebSocket and the requests waiting for its answers live in one process.
    uvicorn.run(
        "backend.server.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        log_config=None,
        proxy_headers=True,
    )
    return 0


def _enclave(args) -> int:
    import asyncio

    from backend.common import logs
    from backend.enclave.main import EnclaveProgram, build_handlers, serve

    logs.setup("enclave")  # stdout: seen only with nitro-cli console in debug mode
    tcp_host = None
    if args.tcp:
        tcp_host, _, port = args.tcp.rpartition(":")
        args.port = int(port)
    try:
        asyncio.run(serve(EnclaveProgram(build_handlers(args.engine_root)), args.port, tcp_host))
    except KeyboardInterrupt:
        pass
    return 0


def _judge(args) -> int:
    import asyncio
    import json

    from backend.tools.judge import JudgeError, aggregate, judge, summary, text

    password = os.environ.get("JUDGE_PASSWORD") or getpass.getpass("App password: ")
    names = ["new_control", "leave_home"] if args.scenario == "all" else [args.scenario]
    try:
        reports = asyncio.run(judge(args.url, password, names, args.speed, args.rounds, args.timeout))
    except JudgeError as e:
        print(f"judge: {e}", file=sys.stderr)
        return 2
    for r in reports:
        print(text(r) + "\n")
    if args.rounds > 1:
        print(json.dumps(aggregate(reports), indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(summary(reports) | {"aggregate": aggregate(reports)}, f, indent=2)
    return 0 if all(r.passed for r in reports) else 1


def _backup(args) -> int:
    from backend.common import logs
    from backend.common.settings import HubSettings, load
    from backend.hub.backup import run_backup

    logs.setup("hub")
    s = load(HubSettings, "hub.yaml")
    run_backup(s.database_url, s.crop_dir, s.backup_dir, s.backup_keep_days)
    return 0


def _hash_password(args) -> int:
    from argon2 import PasswordHasher

    pw = getpass.getpass("New app password: ")
    if pw != getpass.getpass("Again: "):
        print("The two passwords differ.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(pw))
    print("Put it in the hub's environment as APP_PASSWORD_HASH. Never commit it (C-10).", file=sys.stderr)
    return 0


def _not_built(name: str):
    def run(args) -> int:
        print(f"{name} is not built yet", file=sys.stderr)
        return 1

    return run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backend")
    sub = p.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hub", help="run on the laptop at home")
    h.add_argument("--server-url", help="instead of server_url in settings/hub.yaml, e.g. ws://127.0.0.1:8000/ws/hub")
    h.add_argument("--stdout", action="store_true", help="log to stdout instead of log_file (in a container)")
    h.add_argument(
        "--demo-scenarios",
        action="store_true",
        help="allow POST /scenarios/{name}/start (also DEMO_SCENARIOS=1); for development and the demo only",
    )
    h.set_defaults(func=_hub)
    e = sub.add_parser("enclave", help="run inside the Nitro Enclave")
    e.add_argument("--port", type=int, default=5000, help="the vsock port (enclave_port in settings/server.yaml)")
    e.add_argument(
        "--tcp",
        metavar="HOST:PORT",
        help="listen on TCP instead of vsock, for tests and development; sealed photos do not open there",
    )
    e.add_argument("--engine-root", help="where ai-engine's model files are (default: the ai-engine package's repo)")
    e.set_defaults(func=_enclave)
    c = sub.add_parser("server", help="run the server (a pod on k3s on AWS)")
    c.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside the pod")
    c.add_argument("--port", type=int, default=8000)
    c.set_defaults(func=_server)
    j = sub.add_parser("judge", help="play demo scenarios 1 and 3 through the API and judge them")
    j.add_argument("--url", default="http://127.0.0.1:8000", help="the server's API root")
    j.add_argument("--scenario", choices=["all", "new_control", "leave_home"], default="all")
    j.add_argument("--speed", type=float, default=1.0, help="1 for the numbers on the slides; up to 20 to go fast")
    j.add_argument("--rounds", type=int, default=1, help="play each scenario this many times")
    j.add_argument("--timeout", type=float, default=600, help="seconds for one scenario")
    j.add_argument("--json", help="also write the reports to this file")
    j.set_defaults(func=_judge)
    sub.add_parser("backup", help="back up the hub's database and crops").set_defaults(func=_backup)
    sub.add_parser("hash-password", help="print the hash for APP_PASSWORD_HASH").set_defaults(func=_hash_password)
    sub.add_parser("export-runs", help="write the run log as JSON Lines (L-08)").set_defaults(
        func=_not_built("export-runs")
    )
    sub.add_parser("seed-demo", help="fill an empty hub database with the demo home").set_defaults(func=_seed_demo)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
