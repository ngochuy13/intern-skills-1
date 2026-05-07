"""Run a one-off command on a Lobster device and print the output."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssh_sdk import LobsterSSH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("user")
    parser.add_argument("--password", required=True)
    parser.add_argument("--cmd", default="uname -a")
    parser.add_argument("--sudo", action="store_true")
    args = parser.parse_args()

    with LobsterSSH(args.host, args.user, password=args.password) as ssh:
        result = ssh.run(args.cmd, sudo=args.sudo)
    print(result.stdout, end="")
    if result.stderr:
        print(f"[stderr] {result.stderr}", end="")
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
