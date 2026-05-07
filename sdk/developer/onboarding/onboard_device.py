"""
onboard_device.py — provision a Lobster device with the full developer SDK.

Runs from a workstation. Uses ssh_sdk to:
  1. SCP the audio SDK + examples to ~/<user>/sdk/audio on the device.
  2. Install python3-pyaudio via apt (idempotent).
  3. Add the device user to the audio group.
  4. Install the audio skill at /root/.openclaw/workspace/skills/audio.
  5. Run health_check() and print the verdict.

Usage:
    python sdk/developer/onboarding/onboard_device.py <host> <user> --password <pw>

    # specific role (currently only "developer" wires up audio):
    python sdk/developer/onboarding/onboard_device.py <host> <user> --password <pw> --role developer

The script is idempotent — re-running on an already-onboarded device is safe.
"""

import argparse
import json
import os
import sys
import time

# Resolve sibling SDK path so this script can be run from anywhere.
HERE = os.path.dirname(os.path.abspath(__file__))
DEVELOPER_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(DEVELOPER_DIR))
sys.path.insert(0, os.path.join(DEVELOPER_DIR, "ssh"))

from ssh_sdk import LobsterSSH  # noqa: E402

AUDIO_LOCAL_DIR = os.path.join(DEVELOPER_DIR, "audio")
AUDIO_SKILL_LOCAL = os.path.join(REPO_ROOT, "skills", "developer", "audio", "SKILL.md")
DEVICE_SKILL_PATH = "/root/.openclaw/workspace/skills/audio/SKILL.md"


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    parser.add_argument("host")
    parser.add_argument("user")
    parser.add_argument("--password", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--role", default="developer", help="role to onboard (only 'developer' supported today)")
    parser.add_argument("--skip-skill", action="store_true", help="skip installing the audio skill into openclaw workspace")
    parser.add_argument("--skip-health", action="store_true", help="skip running health_check at the end")
    args = parser.parse_args()

    if args.role != "developer":
        print(f"role '{args.role}' not yet supported by this onboarding script")
        return 2

    started = time.time()

    with LobsterSSH(args.host, args.user, password=args.password, port=args.port) as ssh:
        banner("device info")
        info = ssh.run("uname -a && id").stdout.strip()
        print(info)
        home = ssh.run("echo $HOME").stdout.strip()
        remote_audio_dir = f"{home}/sdk/audio"
        remote_audio_examples = f"{remote_audio_dir}/examples"

        banner("step 1/5 — copy audio SDK")
        ssh.run(f"mkdir -p {remote_audio_examples}", check=True)
        ssh.put(os.path.join(AUDIO_LOCAL_DIR, "audio_sdk.py"), f"{remote_audio_dir}/audio_sdk.py")
        for fname in ("record_play.py", "live_passthrough.py", "diagnostic.py"):
            local = os.path.join(AUDIO_LOCAL_DIR, "examples", fname)
            if os.path.exists(local):
                ssh.put(local, f"{remote_audio_examples}/{fname}")
        print(f"copied audio SDK + examples -> {args.host}:{remote_audio_dir}")

        banner("step 2/5 — install python3-pyaudio (idempotent)")
        res = ssh.run(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pyaudio",
            sudo=True, timeout=180,
        )
        if res.exit_code != 0:
            print(f"[warning] apt failed (continuing): {res.stderr.strip()[:200]}")
        else:
            print(res.stdout.strip().splitlines()[-1])

        banner("step 3/5 — add user to audio group")
        ssh.run(f"usermod -a -G audio {args.user}", sudo=True)
        print(f"{args.user} added to audio group (effective on next login)")

        if not args.skip_skill:
            banner("step 4/5 — install audio skill into openclaw workspace")
            if os.path.exists(AUDIO_SKILL_LOCAL):
                ssh.put(AUDIO_SKILL_LOCAL, "/tmp/_audio_SKILL.md")
                ssh.run(
                    "mkdir -p /root/.openclaw/workspace/skills/audio && "
                    f"cp /tmp/_audio_SKILL.md {DEVICE_SKILL_PATH} && "
                    "rm -f /tmp/_audio_SKILL.md",
                    sudo=True, check=True,
                )
                print(f"installed -> {DEVICE_SKILL_PATH}")
            else:
                print(f"[warning] skill not found locally at {AUDIO_SKILL_LOCAL} — skipping")
        else:
            banner("step 4/5 — skipping skill install (--skip-skill)")

        if not args.skip_health:
            banner("step 5/5 — health_check")
            script = (
                "import sys, json\n"
                f"sys.path.insert(0, '{remote_audio_dir}')\n"
                "from audio_sdk import AudioSDK\n"
                "print(json.dumps(AudioSDK().health_check(), default=str, indent=2))\n"
            )
            ssh.write_text("/tmp/_onboard_health.py", script)
            res = ssh.run("python3 /tmp/_onboard_health.py", sudo=True, timeout=30)
            print(res.stdout, end="")
            if res.exit_code != 0:
                print(f"[stderr] {res.stderr[:400]}")
            ssh.run("rm -f /tmp/_onboard_health.py")
            try:
                report = json.loads(res.stdout)
                verdict = report.get("verdict")
                if verdict == "ok":
                    print("\n✓ device ready")
                else:
                    print(f"\n! verdict={verdict} — see report above")
            except json.JSONDecodeError:
                pass
        else:
            banner("step 5/5 — skipping health_check (--skip-health)")

    print(f"\nfinished in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
