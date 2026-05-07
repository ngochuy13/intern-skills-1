"""
Deploy the audio SDK to a Lobster device:
1. SCP audio_sdk.py to ~/sdk/audio/ on the device.
2. apt install python3-pyaudio (idempotent).
3. Add user to audio group (effective on next login).
4. Run a smoke test via the device's python3.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssh_sdk import LobsterSSH

AUDIO_SDK_LOCAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "audio",
    "audio_sdk.py",
)
REMOTE_DIR = "~/sdk/audio"
REMOTE_FILE = f"{REMOTE_DIR}/audio_sdk.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("user")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    with LobsterSSH(args.host, args.user, password=args.password) as ssh:
        home = ssh.run("echo $HOME").stdout.strip()
        remote_dir = REMOTE_DIR.replace("~", home)
        remote_file = REMOTE_FILE.replace("~", home)

        ssh.run(f"mkdir -p {remote_dir}", check=True)
        ssh.put(AUDIO_SDK_LOCAL, remote_file)
        print(f"copied {AUDIO_SDK_LOCAL} -> {args.host}:{remote_file}")

        print("installing python3-pyaudio (idempotent)...")
        res = ssh.run(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pyaudio",
            sudo=True,
            timeout=180,
        )
        if res.exit_code != 0:
            print(f"[warning] apt failed: {res.stderr.strip()}")

        print(f"adding {args.user} to audio group...")
        ssh.run(f"usermod -a -G audio {args.user}", sudo=True)

        print("smoke test:")
        smoke = ssh.run(
            f"cd {remote_dir} && sudo -n python3 -c "
            "'from audio_sdk import AudioSDK; "
            "sdk=AudioSDK(); "
            "print(\"device_index=\", sdk.device_index); "
            "sdk.close()'"
        )
        print(smoke.stdout, end="")
        if smoke.exit_code != 0:
            print(f"[stderr] {smoke.stderr}", end="")
            raise SystemExit(smoke.exit_code)


if __name__ == "__main__":
    main()
