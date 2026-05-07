"""Record 5 seconds and immediately play it back."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_sdk import AudioSDK


def main() -> None:
    with AudioSDK() as sdk:
        print("Speak for 5 seconds...")
        sdk.record_and_play(duration=5)


if __name__ == "__main__":
    main()
