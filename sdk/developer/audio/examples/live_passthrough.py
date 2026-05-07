"""Stream mic to speaker live (acoustic feedback risk if mic is near speaker)."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_sdk import AudioSDK


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=15.0, help="duration to run")
    parser.add_argument("--chunk", type=int, default=512, help="frames per buffer")
    args = parser.parse_args()

    with AudioSDK() as sdk:
        sdk.set_volume(40)  # keep moderate to reduce feedback risk
        print(f"Live passthrough for {args.seconds}s. Speak now.")
        sdk.stream_passthrough(duration=args.seconds, chunk=args.chunk)


if __name__ == "__main__":
    main()
