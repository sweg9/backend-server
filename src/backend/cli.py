"""`backend <command>`: the one entry point of both modes (wiki: Modules)."""

import argparse
import getpass
import sys


def _hub(args) -> int:
    print("hub mode is not built yet", file=sys.stderr)
    return 1


def _cloud(args) -> int:
    import uvicorn

    # One worker: SQLite, the scheduler and the hub's WebSocket live in one process.
    uvicorn.run("backend.cloud.main:create_app", factory=True, host=args.host, port=args.port, workers=1)
    return 0


def _hash_password(args) -> int:
    from argon2 import PasswordHasher

    pw = getpass.getpass("New app password: ")
    if pw != getpass.getpass("Again: "):
        print("The two passwords differ.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(pw))
    print("Set this as APP_PASSWORD_HASH on the cloud. Never commit it (C-10).", file=sys.stderr)
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
    c = sub.add_parser("cloud", help="run on EC2")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8000)
    c.set_defaults(func=_cloud)
    sub.add_parser("hash-password", help="print the hash for APP_PASSWORD_HASH").set_defaults(func=_hash_password)
    sub.add_parser("export-runs", help="write the run log as JSON Lines (L-08)").set_defaults(
        func=_not_built("export-runs")
    )
    sub.add_parser("seed-demo", help="add demo data, such as seed uses").set_defaults(func=_not_built("seed-demo"))
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
