"""
audio_sdk.py - Mic & Speaker SDK for Autonomous Intern (Lobster) devices.

Records from the microphone, plays through the speaker, streams PCM, and runs
hardware health checks. Targets the ES8389 codec on Allwinner-based intern
boards (default card index 1) but works against any ALSA-visible PyAudio device.

Public surface:
    list_devices()              -- enumerate audio devices (module-level shim)
    get_device_index()          -- find PyAudio index for ALSA hw:card,device

    AudioSDK
        record / play / record_and_play / stream_passthrough
        stream_in / stream_out                  -- raw PCM streaming
        get_volume / set_volume / volume_up / volume_down
        list_devices / list_mixer_controls / get_mixer / set_mixer
        apply_capture_defaults / set_high_gain  -- mic gain calibration
        health_check                            -- end-to-end diagnostic
        close (and context manager)

Errors:
    AudioSDKError, DeviceNotFoundError, MixerError, RecordingTooQuietError
"""

import logging
import math
import os
import struct
import subprocess
import threading
import time
import wave
from typing import Iterable, Iterator, List, Optional, Union

import pyaudio

log = logging.getLogger(__name__)

DEFAULT_CARD = 1
DEFAULT_DEVICE = 0
SAMPLE_RATE = 44100
CHANNELS = 2
FORMAT = pyaudio.paInt16
CHUNK = 1024


class AudioSDKError(Exception):
    """Base class for audio SDK errors."""


class DeviceNotFoundError(AudioSDKError):
    """No PyAudio device matched the configured card."""


class MixerError(AudioSDKError):
    """A get_mixer / set_mixer call failed."""


class RecordingTooQuietError(AudioSDKError):
    """Recording RMS was below the audible threshold."""


def list_devices() -> List[dict]:
    """Enumerate audio devices. Module-level shim that delegates to AudioSDK."""
    sdk = AudioSDK.__new__(AudioSDK)
    sdk._p = pyaudio.PyAudio()
    try:
        return sdk.list_devices()
    finally:
        sdk._p.terminate()


def get_device_index(card: int = DEFAULT_CARD, device: int = DEFAULT_DEVICE) -> Optional[int]:
    """Find PyAudio device index matching ALSA hw:card,device."""
    p = pyaudio.PyAudio()
    target = f"hw:{card},{device}"
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if f"hw:{card}" in info["name"] or target in info["name"]:
                return i
        return None
    finally:
        p.terminate()


class AudioSDK:
    """
    Mic + speaker SDK.

    Examples:
        with AudioSDK() as sdk:
            sdk.record("out.wav", duration=5)
            sdk.play("out.wav")

        # streaming
        with AudioSDK() as sdk:
            for chunk in sdk.stream_in(duration=10):
                process(chunk)

        # diagnostic
        with AudioSDK() as sdk:
            print(sdk.health_check())
    """

    def __init__(
        self,
        card: int = DEFAULT_CARD,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        auto_tune: bool = True,
    ):
        self.card = card
        self.sample_rate = sample_rate
        self.channels = channels
        self.format = FORMAT
        self.chunk = CHUNK
        self.auto_tune = auto_tune
        self._tuned = False
        self._p = pyaudio.PyAudio()
        self.device_index = self._find_device()

    def _find_device(self) -> Optional[int]:
        for i in range(self._p.get_device_count()):
            info = self._p.get_device_info_by_index(i)
            if f"hw:{self.card}" in info["name"]:
                return i
        return None

    def _ensure_tuned(self) -> None:
        """Apply capture defaults the first time a capture-side method runs."""
        if self.auto_tune and not self._tuned:
            try:
                self.apply_capture_defaults()
            except MixerError as exc:
                log.warning("auto_tune skipped: %s", exc)
            self._tuned = True

    # ------------------------------------------------------------------ basics

    def record(self, filepath: str, duration: float) -> str:
        """Record duration seconds to filepath (.wav). Returns filepath."""
        self._ensure_tuned()
        log.info("recording %ss -> %s", duration, filepath)
        frames: List[bytes] = []
        stream = self._p.open(
            format=self.format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.device_index,
            frames_per_buffer=self.chunk,
        )
        total_chunks = int(self.sample_rate / self.chunk * duration)
        for _ in range(total_chunks):
            frames.append(stream.read(self.chunk, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()

        parent = os.path.dirname(filepath)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self._p.get_sample_size(self.format))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b"".join(frames))
        log.info("saved %s", filepath)
        return filepath

    def play(self, filepath: str) -> None:
        """Play a .wav file through the speaker."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)
        log.info("playing %s", filepath)
        with wave.open(filepath, "rb") as wf:
            stream = self._p.open(
                format=self._p.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
                output_device_index=self.device_index,
            )
            data = wf.readframes(self.chunk)
            while data:
                stream.write(data)
                data = wf.readframes(self.chunk)
            stream.stop_stream()
            stream.close()
        log.info("playback done")

    def record_and_play(self, duration: float, filepath: str = "/tmp/sdk_test.wav") -> None:
        """Record then immediately play back."""
        self.record(filepath, duration)
        self.play(filepath)

    def stream_passthrough(
        self,
        duration: Optional[float] = None,
        chunk: Optional[int] = None,
        feedback_safe: bool = True,
        gate_threshold: int = 400,
    ) -> None:
        """
        Live mic-to-speaker monitor.

        With feedback_safe=True (default), the SDK temporarily lowers the
        speaker volume and mic PGA, and runs a hysteresis noise gate that
        suppresses ambient ringing. All mixer changes are restored on exit.

        Args:
            duration: seconds to run, or None for indefinite (Ctrl-C to stop).
            chunk: frames per buffer.
            feedback_safe: enable attenuation + noise gate (default True).
            gate_threshold: RMS above which the gate opens. Lower = more
                permissive (more ringing); higher = more aggressive cut.
        """
        self._ensure_tuned()
        chunk = chunk or self.chunk

        try:
            import audioop  # stdlib up to Python 3.12
        except ImportError:
            audioop = None  # gate degrades to a passthrough on 3.13+

        saved: dict = {}
        if feedback_safe:
            for ctrl in ("DACL", "DACR", "ADCL PGA", "ADCR PGA"):
                try:
                    v = self.get_mixer(ctrl).get("value")
                    if v is not None:
                        saved[ctrl] = v
                except MixerError:
                    pass
            for ctrl, val in (("DACL", 77), ("DACR", 77), ("ADCL PGA", 10), ("ADCR PGA", 10)):
                try:
                    self.set_mixer(ctrl, val)
                except MixerError as exc:
                    log.warning("feedback_safe could not set %s: %s", ctrl, exc)

        log.info(
            "live stream mic->speaker (duration=%s chunk=%s feedback_safe=%s)",
            duration, chunk, feedback_safe,
        )
        silent = b"\x00" * (chunk * self.channels * 2)

        in_stream = self._p.open(
            format=self.format, channels=self.channels, rate=self.sample_rate,
            input=True, input_device_index=self.device_index, frames_per_buffer=chunk,
        )
        out_stream = self._p.open(
            format=self.format, channels=self.channels, rate=self.sample_rate,
            output=True, output_device_index=self.device_index, frames_per_buffer=chunk,
        )

        gate_open = False
        gate_low = gate_threshold * 0.5  # hysteresis to avoid chatter

        def _emit(data: bytes) -> None:
            nonlocal gate_open
            if not feedback_safe or audioop is None:
                out_stream.write(data)
                return
            rms = audioop.rms(data, 2)
            if gate_open and rms < gate_low:
                gate_open = False
            elif not gate_open and rms > gate_threshold:
                gate_open = True
            out_stream.write(data if gate_open else silent)

        try:
            if duration is None:
                while True:
                    _emit(in_stream.read(chunk, exception_on_overflow=False))
            else:
                total = int(self.sample_rate / chunk * duration)
                for _ in range(total):
                    _emit(in_stream.read(chunk, exception_on_overflow=False))
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            in_stream.stop_stream(); in_stream.close()
            out_stream.stop_stream(); out_stream.close()
            for ctrl, val in saved.items():
                try:
                    self.set_mixer(ctrl, val)
                except MixerError:
                    pass

    # ------------------------------------------------------------- PCM streams

    def stream_in(self, duration: Optional[float] = None, chunk: Optional[int] = None) -> Iterator[bytes]:
        """
        Yield raw PCM bytes from the mic.

        duration=None runs until the consumer stops iterating.
        Caller decides what to do with each chunk (file, STT, network, etc.).
        """
        self._ensure_tuned()
        chunk = chunk or self.chunk
        stream = self._p.open(
            format=self.format, channels=self.channels, rate=self.sample_rate,
            input=True, input_device_index=self.device_index, frames_per_buffer=chunk,
        )
        try:
            if duration is None:
                while True:
                    yield stream.read(chunk, exception_on_overflow=False)
            else:
                total = int(self.sample_rate / chunk * duration)
                for _ in range(total):
                    yield stream.read(chunk, exception_on_overflow=False)
        finally:
            stream.stop_stream(); stream.close()

    def stream_out(self, chunks: Iterable[bytes], chunk: Optional[int] = None) -> None:
        """Consume PCM bytes from any iterable and write them to the speaker."""
        chunk = chunk or self.chunk
        stream = self._p.open(
            format=self.format, channels=self.channels, rate=self.sample_rate,
            output=True, output_device_index=self.device_index, frames_per_buffer=chunk,
        )
        try:
            for buf in chunks:
                stream.write(buf)
        finally:
            stream.stop_stream(); stream.close()

    # ----------------------------------------------------------------- volume

    def get_volume(self) -> int:
        """Current playback volume as percent (0-100)."""
        result = subprocess.run(
            ["amixer", "-c", str(self.card), "sget", "DACL"],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if "Mono:" in line and "%" in line:
                return int(line.split("[")[1].split("%")[0])
        return -1

    def set_volume(self, percent: int) -> None:
        """Set playback volume on both DACL and DACR."""
        percent = max(0, min(100, percent))
        value = int(percent / 100 * 255)
        subprocess.run(["amixer", "-c", str(self.card), "sset", "DACL", str(value)], capture_output=True)
        subprocess.run(["amixer", "-c", str(self.card), "sset", "DACR", str(value)], capture_output=True)
        log.info("volume set to %s%%", percent)

    def volume_up(self, step: int = 10) -> None:
        self.set_volume(self.get_volume() + step)

    def volume_down(self, step: int = 10) -> None:
        self.set_volume(self.get_volume() - step)

    # ---------------------------------------------------------------- devices

    def list_devices(self) -> List[dict]:
        """Return a structured list of all PyAudio devices."""
        out = []
        for i in range(self._p.get_device_count()):
            info = self._p.get_device_info_by_index(i)
            out.append({
                "index": i,
                "name": info["name"],
                "max_input_channels": int(info["maxInputChannels"]),
                "max_output_channels": int(info["maxOutputChannels"]),
            })
        return out

    # ----------------------------------------------------------------- mixer

    def list_mixer_controls(self) -> List[str]:
        """Return simple-mixer control names for this card."""
        result = subprocess.run(
            ["amixer", "-c", str(self.card), "scontrols"],
            capture_output=True, text=True,
        )
        names: List[str] = []
        for line in result.stdout.splitlines():
            if "'" in line:
                names.append(line.split("'")[1])
        return names

    def get_mixer(self, name: str) -> dict:
        """Snapshot one mixer control: {name, value, percent, db, items?}."""
        result = subprocess.run(
            ["amixer", "-c", str(self.card), "sget", name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise MixerError(f"control not found: {name}")
        snapshot = {"name": name, "value": None, "percent": None, "db": None}
        items: List[str] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Items:"):
                items = [s.strip("'") for s in stripped[len("Items:"):].split() if s.strip("'")]
            elif stripped.startswith("Item0:"):
                snapshot["value"] = stripped.split(":", 1)[1].strip().strip("'")
            elif "Mono:" in stripped or "Front Left:" in stripped:
                if "[" in stripped and "%" in stripped:
                    parts = stripped.split("[")
                    try:
                        snapshot["percent"] = int(parts[1].split("%")[0])
                    except (IndexError, ValueError):
                        pass
                    if len(parts) >= 3 and "dB" in parts[2]:
                        try:
                            snapshot["db"] = float(parts[2].split("dB")[0])
                        except ValueError:
                            pass
                tokens = stripped.split()
                for tok in tokens:
                    if tok.isdigit():
                        snapshot["value"] = int(tok)
                        break
        if items:
            snapshot["items"] = items
        return snapshot

    def set_mixer(self, name: str, value: Union[int, str]) -> None:
        """Set one mixer control."""
        result = subprocess.run(
            ["amixer", "-c", str(self.card), "sset", name, str(value)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise MixerError(f"set_mixer({name}={value}) failed: {result.stderr.strip()}")

    # ----------------------------------------------------------- mic tuning

    def apply_capture_defaults(self) -> None:
        """
        Apply mic-gain settings that produce hearable speech on the ES8389.

        Empirically verified values:
          ADCL/ADCR digital = 255 (100% / 0 dB)
          ADCL/ADCR PGA      = 14  (+42 dB)
          ADC MUX            = AMIC
          ALC                = ALC OFF
        """
        log.info("applying capture defaults (high-gain AMIC)")
        for ctrl, val in [
            ("ADCL", 255),
            ("ADCR", 255),
            ("ADCL PGA", 14),
            ("ADCR PGA", 14),
        ]:
            try:
                self.set_mixer(ctrl, val)
            except MixerError as exc:
                log.debug("skip %s: %s", ctrl, exc)
        for ctrl, val in [("ADC MUX", "AMIC"), ("ALC", "ALC OFF")]:
            try:
                self.set_mixer(ctrl, val)
            except MixerError as exc:
                log.debug("skip %s: %s", ctrl, exc)

    def set_high_gain(self) -> None:
        """One-liner alias for apply_capture_defaults()."""
        self.apply_capture_defaults()

    # ----------------------------------------------------------- diagnostic

    def health_check(self, loopback: bool = True, sample_seconds: float = 1.0) -> dict:
        """
        End-to-end mic + speaker diagnostic.

        Returns a dict with keys: device_index, speaker_volume, mic_volume,
        mic_pga_db, mic_peak, mic_rms, loopback_rms, verdict, notes.

        Verdicts: "ok", "mic_silent", "speaker_off", "no_device".
        """
        notes: List[str] = []
        out = {
            "device_index": self.device_index,
            "speaker_volume": None,
            "mic_volume": None,
            "mic_pga_db": None,
            "mic_peak": None,
            "mic_rms": None,
            "loopback_rms": None,
            "verdict": None,
            "notes": notes,
        }

        if self.device_index is None:
            out["verdict"] = "no_device"
            notes.append(f"no PyAudio device matches card {self.card}")
            return out

        try:
            speaker = self.get_mixer("DACL")
            mic = self.get_mixer("ADCL")
            pga = self.get_mixer("ADCL PGA")
            out["speaker_volume"] = speaker.get("percent")
            out["mic_volume"] = mic.get("percent")
            out["mic_pga_db"] = pga.get("db")
        except MixerError as exc:
            notes.append(f"mixer read failed: {exc}")

        self._ensure_tuned()
        path = "/tmp/_audio_health.wav"
        self.record(path, sample_seconds)
        peak, rms = _wav_levels(path)
        out["mic_peak"] = peak
        out["mic_rms"] = rms

        if loopback:
            try:
                self.set_mixer("loopback debug", "on")
                lb_path = "/tmp/_audio_health_loopback.wav"
                _, lb_rms = _record_with_tone(self, lb_path, sample_seconds)
                out["loopback_rms"] = lb_rms
            except (MixerError, AudioSDKError) as exc:
                notes.append(f"loopback test skipped: {exc}")
            finally:
                try:
                    self.set_mixer("loopback debug", "off")
                except MixerError:
                    pass

        if (out["speaker_volume"] or 0) == 0:
            out["verdict"] = "speaker_off"
            notes.append("DACL is at 0%; raise speaker volume")
        elif rms < 100:
            out["verdict"] = "mic_silent"
            notes.append(
                "mic RMS below audible threshold; if loopback_rms is healthy, "
                "the codec works and the issue is at the analog mic input"
            )
        else:
            out["verdict"] = "ok"
        return out

    # -------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self._p.terminate()

    def __enter__(self) -> "AudioSDK":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------- internals

def _wav_levels(path: str) -> tuple:
    with wave.open(path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    n = len(raw) // 2
    if n == 0:
        return 0, 0.0
    peak = 0
    sum_sq = 0
    for i in range(n):
        v = struct.unpack_from("<h", raw, i * 2)[0]
        a = abs(v)
        if a > peak:
            peak = a
        sum_sq += v * v
    return peak, math.sqrt(sum_sq / n)


def _record_with_tone(sdk: "AudioSDK", path: str, duration: float, freq: int = 1000) -> tuple:
    """Play a tone while recording (for loopback test). Returns (peak, rms)."""
    samples = bytearray()
    n = int(sdk.sample_rate * duration)
    for i in range(n):
        v = int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / sdk.sample_rate))
        samples += struct.pack("<hh", v, v)
    tone_bytes = bytes(samples)

    def play() -> None:
        sdk.stream_out([tone_bytes])

    def rec() -> None:
        sdk.record(path, duration)

    t1 = threading.Thread(target=play)
    t2 = threading.Thread(target=rec)
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join()
    t2.join()
    return _wav_levels(path)


# ------------------------------------------------------------------- script

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=== Audio SDK self-test ===")
    for d in list_devices():
        print(f"  [{d['index']}] {d['name']} (in={d['max_input_channels']} out={d['max_output_channels']})")
    print()
    with AudioSDK() as sdk:
        print(f"device_index = {sdk.device_index}")
        print("running health_check ...")
        result = sdk.health_check()
        for k, v in result.items():
            print(f"  {k}: {v}")
