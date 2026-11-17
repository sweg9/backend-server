"""`backend <command>`: the one entry point of both modes (wiki: Modules)."""

import argparse
import getpass
import sys


def _hub(args) -> int:
    print("hub mode is not built yet", file=sys.stderr)
    return 1


def _server(args) -> int:
    import uvicorn

    from backend.common import logs

    logs.setup("server")
    # One worker: SQLite, the scheduler and the hub's WebSocket live in one process.
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


def _backup(args) -> int:
    from backend.common import logs
    from backend.common.settings import ServerSettings, load
    from backend.server.backup import run_backup

    logs.setup("server")
    s = load(ServerSettings, "server.yaml")
    run_backup(s.database_url, s.crop_dir, s.backup_dir, s.backup_keep_days)
    return 0


def _hash_password(args) -> int:
    from argon2 import PasswordHasher

    pw = getpass.getpass("New app password: ")
    if pw != getpass.getpass("Again: "):
        print("The two passwords differ.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(pw))
    print("Put it in the k8s Secret as APP_PASSWORD_HASH. Never commit it (C-10).", file=sys.stderr)
    return 0


def _not_built(name: str):
    def run(args) -> int:
        print(f"{name} is not built yet", file=sys.stderr)
        return 1

    return run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backend")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("hub", help="run on the laptop at home").set_defaults(func=_hub)
    c = sub.add_parser("server", help="run the server (a pod on k8s)")
    c.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside the pod")
    c.add_argument("--port", type=int, default=8000)
    c.set_defaults(func=_server)
    sub.add_parser("backup", help="back up the database and the crops (the k8s CronJob)").set_defaults(func=_backup)
    sub.add_parser("hash-password", help="print the hash for APP_PASSWORD_HASH").set_defaults(func=_hash_password)
    sub.add_parser("export-runs", help="write the run log as JSON Lines (L-08)").set_defaults(
        func=_not_built("export-runs")
    )
    sub.add_parser("seed-demo", help="add demo data, such as seed uses").set_defaults(func=_not_built("seed-demo"))
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
