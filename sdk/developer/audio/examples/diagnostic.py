"""End-to-end mic/speaker diagnostic. Prints the full health_check() report."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_sdk import AudioSDK


def main() -> None:
    with AudioSDK() as sdk:
        report = sdk.health_check(loopback=True)
    width = max(len(k) for k in report) + 2
    for k, v in report.items():
        print(f"{k:<{width}}{v}")
    raise SystemExit(0 if report["verdict"] == "ok" else 1)


if __name__ == "__main__":
    main()
