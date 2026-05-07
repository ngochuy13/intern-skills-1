"""Upload one file then download another, using LobsterSSH SFTP."""

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
    parser.add_argument("--push", nargs=2, metavar=("LOCAL", "REMOTE"))
    parser.add_argument("--pull", nargs=2, metavar=("REMOTE", "LOCAL"))
    args = parser.parse_args()

    with LobsterSSH(args.host, args.user, password=args.password) as ssh:
        if args.push:
            ssh.put(args.push[0], args.push[1])
            print(f"pushed {args.push[0]} -> {args.push[1]}")
        if args.pull:
            ssh.get(args.pull[0], args.pull[1])
            print(f"pulled {args.pull[0]} -> {args.pull[1]}")


if __name__ == "__main__":
    main()
