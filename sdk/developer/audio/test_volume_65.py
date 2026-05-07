"""
test_volume_65.py - Set volume to 65% and run a record+playback round-trip.
Run on the Pi/Allwinner device that has audio_sdk.py next to it.
"""

from audio_sdk import AudioSDK


def main():
    with AudioSDK() as sdk:
        print(f"[test] device index: {sdk.device_index}")

        print("[test] setting volume to 65%")
        sdk.set_volume(65)

        cur = sdk.get_volume()
        print(f"[test] current volume: {cur}%")

        print("[test] recording 5s, then playing back -- speak into the mic now")
        sdk.record_and_play(duration=5, filepath="/tmp/sdk_volume_test.wav")

        print("[test] done")


if __name__ == "__main__":
    main()
