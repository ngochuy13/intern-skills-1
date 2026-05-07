# Developer SDKs

Cross-platform Python SDKs for working with Autonomous Intern (Lobster) devices.

| SDK | Purpose | File |
|---|---|---|
| `audio_sdk` | Mic + speaker: record, play, stream PCM, run hardware diagnostics | [`audio/audio_sdk.py`](audio/audio_sdk.py) |
| `ssh_sdk`   | Connect to Lobster devices from any workstation, run commands, transfer files | [`ssh/ssh_sdk.py`](ssh/ssh_sdk.py) |

Both run on macOS, Linux, and Windows. Both are single-file Python modules — drop them anywhere on your `PYTHONPATH`.

---

## 1. Workstation install

```bash
pip install pyaudio paramiko
```

Per-OS notes for `pyaudio` (paramiko works out of the box everywhere):

- **macOS**: `brew install portaudio` first if `pip install pyaudio` fails to compile.
- **Linux (Debian/Ubuntu)**: `sudo apt install -y libportaudio2 portaudio19-dev` first.
- **Windows**: prebuilt `pyaudio` wheels exist on PyPI; `pip install pyaudio` should just work.

If you only need the SSH SDK (e.g. for fleet automation, no audio), `pip install paramiko` is enough.

---

## 2. Install the audio SDK on a Lobster device

### Path A — direct (recommended, verified)

Use the SSH SDK from your workstation:

```python
from ssh_sdk import LobsterSSH

with LobsterSSH("172.168.20.145", "system", password="12345") as ssh:
    ssh.run("mkdir -p ~/sdk/audio", check=True)
    ssh.put("sdk/developer/audio/audio_sdk.py", "/home/system/sdk/audio/audio_sdk.py")
    ssh.run("apt-get install -y python3-pyaudio", sudo=True, timeout=180)
    ssh.run("usermod -a -G audio system", sudo=True)
```

A ready-to-run script that does the same thing plus a smoke test:

```bash
python sdk/developer/ssh/examples/deploy_audio_sdk.py 172.168.20.145 system --password 12345
```

Equivalent shell-only flow if you don't want Python on the workstation:

```bash
ssh system@<host> "mkdir -p ~/sdk/audio"
scp sdk/developer/audio/audio_sdk.py system@<host>:~/sdk/audio/
ssh system@<host> "sudo apt-get install -y python3-pyaudio && sudo usermod -a -G audio system"
```

### Path C — one-shot onboarding script (recommended for fresh devices)

For a brand-new Lobster device, run the onboarding script from your workstation. It does everything in Path A *plus* installs the `/audio` skill into the openclaw workspace and runs `health_check()`:

```bash
python sdk/developer/onboarding/onboard_device.py <host> <user> --password <pw>
```

What it does (idempotent — safe to re-run):

1. SCP `audio_sdk.py` and its examples to `~/<user>/sdk/audio/` on the device.
2. `apt install -y python3-pyaudio`.
3. `usermod -a -G audio <user>` (effective on next login).
4. Install `skills/developer/audio/SKILL.md` to `/root/.openclaw/workspace/skills/audio/SKILL.md` so `/audio` chat commands work.
5. Run `health_check()` and print the verdict. Exits `0` on `verdict=ok`.

Flags:

- `--skip-skill` — copy the SDK only, skip the openclaw skill install.
- `--skip-health` — skip the final `health_check()`.
- `--port N` — non-default SSH port.

Sample run (~7 seconds against a healthy device):

```
=== step 1/5 — copy audio SDK ===
=== step 2/5 — install python3-pyaudio (idempotent) ===
=== step 3/5 — add user to audio group ===
=== step 4/5 — install audio skill into openclaw workspace ===
=== step 5/5 — health_check ===
  verdict=ok  mic_rms=3542  loopback_rms=9141
✓ device ready
```

After the script finishes, the device's Claude will pick up `/audio` skill commands on its next session reload (send `/restart` in Telegram, or wait for the openclaw watcher to pick up the new file).

### Path B — via Telegram chat (alternate)

If the Lobster device runs an openclaw/Claude session reachable over Telegram, paste this prompt into the chat. It walks the device through the same five steps as `onboard_device.py` but uses the device's own Bash tool instead of SSH from a workstation.

> **Set up the developer audio SDK on this device.** Please run these steps in order and reply only when each step succeeds. Use `sudo` on the device — the `system` user has passwordless sudo.
>
> 1. Fetch `audio_sdk.py` from the public repo and place it locally:
>    ```
>    mkdir -p ~/sdk/audio
>    curl -fsSL https://raw.githubusercontent.com/autonomous-ecm/intern-skills/main/sdk/developer/audio/audio_sdk.py -o ~/sdk/audio/audio_sdk.py
>    ```
> 2. Install the Python audio dependency:
>    ```
>    sudo apt-get install -y python3-pyaudio
>    ```
> 3. Add the current user to the `audio` group (effective on next login):
>    ```
>    sudo usermod -a -G audio $USER
>    ```
> 4. Install the `/audio` skill so chat commands work:
>    ```
>    sudo mkdir -p /root/.openclaw/workspace/skills/audio
>    sudo curl -fsSL https://raw.githubusercontent.com/autonomous-ecm/intern-skills/main/skills/developer/audio/SKILL.md -o /root/.openclaw/workspace/skills/audio/SKILL.md
>    ```
> 5. Verify by running the SDK's built-in health check and reply with the JSON output:
>    ```
>    sudo python3 -c "import sys, json; sys.path.insert(0, '$HOME/sdk/audio'); from audio_sdk import AudioSDK; print(json.dumps(AudioSDK().health_check(), default=str, indent=2))"
>    ```
>
> The expected verdict is `ok`. If it's `mic_silent` or `speaker_off`, paste the full output back to me. After step 4, please run `/restart` so the new `/audio` skill is loaded into the active session.

That's a verbatim, copy-paste prompt — works against any Lobster device whose Claude has access to a `Bash` tool, the public network, and `sudo`. The device can read everything it needs from the public `autonomous-ecm/intern-skills` repo on GitHub, so no checked-out clone or MCP file connector is required.

If the device is air-gapped (no public internet), use Path A or Path C instead — those push the files over SSH from your workstation.

### After install

The `audio` group change only takes effect on the next login. Until then run audio commands with `sudo`. After re-login, plain `python3 ...` is enough.

---

## 3. Verify mic and speaker

The SDK ships an end-to-end diagnostic. From the device:

```bash
cd ~/sdk/audio
python3 -m examples.diagnostic     # if examples/ was copied too, otherwise:
python3 -c "from audio_sdk import AudioSDK; print(AudioSDK().health_check())"
```

Example output:

```python
{
  "device_index": 1,
  "speaker_volume": 70,
  "mic_volume": 100,
  "mic_pga_db": 42.0,
  "mic_peak": 32768,
  "mic_rms": 5845.3,
  "loopback_rms": 9121.0,
  "verdict": "ok",
  "notes": []
}
```

### Interpreting the verdict

| Verdict | Meaning | What to do |
|---|---|---|
| `ok` | Mic and speaker both produced healthy signal | done |
| `mic_silent` | Capture RMS below the audible threshold | If `loopback_rms` is healthy (~5000+), the codec is fine — the analog mic input is the issue. Inspect the mic connector / cable / module. If `loopback_rms` is also low, the codec itself is the problem. |
| `speaker_off` | DACL is at 0% | `sdk.set_volume(70)` and re-run |
| `no_device` | No PyAudio device matches the configured card | Run `sdk.list_devices()`, find the right card index, pass it to `AudioSDK(card=N)` |

### What `health_check` actually does

1. Reads current speaker volume, mic digital volume, mic PGA dB.
2. Records 1 second from the mic; computes peak and RMS.
3. (If `loopback=True`) toggles the codec's `loopback debug` switch on, plays a 1 kHz tone while recording, computes the loopback RMS, then restores the switch. This proves the ADC chain works without depending on the physical mic.
4. Restores all mixer state it changed.
5. Returns a dict and never raises — programmatic callers branch on `verdict`.

---

## 4. Integration recipes

### Record to file

```python
from audio_sdk import AudioSDK

with AudioSDK() as sdk:
    sdk.record("voice.wav", duration=5)
```

### Pipe mic into a speech-to-text engine

```python
from audio_sdk import AudioSDK

with AudioSDK() as sdk:
    for chunk in sdk.stream_in(duration=10):
        stt.feed(chunk)            # any STT that accepts PCM bytes
print(stt.transcript())
```

`stream_in()` yields raw 16-bit signed little-endian stereo PCM at 44.1 kHz by default. Call with `duration=None` to iterate forever (consumer breaks the loop).

### Play TTS audio

```python
from audio_sdk import AudioSDK

with AudioSDK() as sdk:
    sdk.set_volume(70)
    sdk.stream_out(tts.synthesize("Hello, I am the intern."))
```

`stream_out` accepts any iterable of PCM `bytes` — generator, list, file chunks.

### Live mic monitor

```python
from audio_sdk import AudioSDK

with AudioSDK() as sdk:
    sdk.set_volume(40)
    sdk.stream_passthrough(duration=30)   # speak; you'll hear yourself
```

Beware acoustic feedback when mic and speaker share an enclosure.

### Batch deploy across devices

```python
from ssh_sdk import LobsterSSH

devices = [("172.168.20.145", "system", "12345"), ...]
for host, user, pw in devices:
    with LobsterSSH(host, user, password=pw) as ssh:
        ssh.put("sdk/developer/audio/audio_sdk.py", "/home/system/sdk/audio/audio_sdk.py")
        ssh.run("apt-get install -y python3-pyaudio", sudo=True, check=True)
```

---

## 5. Reference

### `audio_sdk.AudioSDK`

| Method | Purpose |
|---|---|
| `__init__(card=1, sample_rate=44100, channels=2, auto_tune=True)` | Open PyAudio. With `auto_tune=True` the high-gain mic defaults are applied lazily on first capture. |
| `record(path, duration)` | Record to WAV. Returns `path`. |
| `play(path)` | Play a WAV through the speaker. |
| `record_and_play(duration, path=...)` | Record then play. |
| `stream_passthrough(duration=None, chunk=None)` | Live mic→speaker. |
| `stream_in(duration=None, chunk=None) -> Iterator[bytes]` | Yield raw PCM chunks. |
| `stream_out(chunks, chunk=None)` | Consume PCM chunks and play them. |
| `get_volume() -> int` | Current speaker percent. |
| `set_volume(percent)` | Set DACL+DACR. |
| `volume_up(step=10)` / `volume_down(step=10)` | Relative volume. |
| `list_devices() -> list[dict]` | Enumerate PyAudio devices. |
| `list_mixer_controls() -> list[str]` | All ALSA mixer control names on the codec card. |
| `get_mixer(name) -> dict` | Snapshot one control: `{name, value, percent, db, items?}`. |
| `set_mixer(name, value)` | Set one control. Raises `MixerError` on failure. |
| `apply_capture_defaults()` / `set_high_gain()` | Empirically verified ES8389 mic defaults: ADCL/ADCR=255, PGA=14, AMIC, ALC OFF. |
| `health_check(loopback=True, sample_seconds=1.0) -> dict` | Full diagnostic; see §3. |
| `close()` | Release PyAudio. Use as context manager. |

Errors: `AudioSDKError`, `DeviceNotFoundError`, `MixerError`, `RecordingTooQuietError`.

### `ssh_sdk.LobsterSSH`

| Method | Purpose |
|---|---|
| `__init__(host, user, password=None, key_path=None, port=22, connect_timeout=10.0)` | Provide either `password` or `key_path`. If both, key is tried first. |
| `connect()` / `close()` | Manage the connection (or use as context manager). |
| `run(cmd, sudo=False, timeout=None, check=False) -> CommandResult` | Execute a command. `check=True` raises `CommandError` on non-zero exit. |
| `put(local, remote)` / `get(remote, local)` | Single-file SFTP. |
| `put_dir(local_dir, remote_dir)` / `get_dir(remote_dir, local_dir)` | Recursive SFTP. |
| `exists(path) -> bool` | SFTP stat check. |
| `read_text(path) -> str` / `write_text(path, content)` | Convenience text I/O. |
| `listdir(path) -> list[str]` | Remote directory listing. |

`CommandResult(stdout, stderr, exit_code, duration_s)` is a dataclass.

Module-level shortcuts: `run_once(host, user, *, cmd, password=..., sudo=False)`, `scp_to(host, user, local, remote, password=...)`, `scp_from(host, user, remote, local, password=...)`.

Errors: `SSHError`, `ConnectionError`, `CommandError`, `TransferError`.

---

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Recording is just clicks, RMS near 0 | Mic gain at codec defaults. Call `sdk.set_high_gain()` (or trust `auto_tune=True`). If still silent, run `sdk.health_check(loopback=True)`; healthy `loopback_rms` with `mic_rms<100` means the analog mic input itself is the problem (cable, MICBIAS, mic module). |
| Loud howl during `stream_passthrough` | Acoustic feedback. Lower `set_volume(...)` or move the mic away from the speaker. |
| `sudo` required every audio call | The user isn't in the `audio` group yet, or a previous `usermod -a -G audio` hasn't been re-logged in. Open a fresh SSH session. |
| Volume / mic gain reset across reboot | The Lobster image doesn't run `alsactl store`. Either persist state at the image level or call `sdk.set_high_gain()` and `sdk.set_volume(70)` at startup of your own service. |
| `MixerError: control not found` | The codec on this board doesn't expose that control. List what's available with `sdk.list_mixer_controls()`. |
| `i2cget` returns "Device or resource busy" | Expected — the kernel codec driver owns the i2c address. Use ALSA controls instead. |
| `no_device` verdict from `health_check` | PyAudio doesn't see card 1. Run `sdk.list_devices()`, then pass the right index: `AudioSDK(card=N)`. |
| `paramiko.ssh_exception.AuthenticationException` | Wrong password; or the device only accepts keys; or `look_for_keys=False` blocks an SSH-agent key. Pass `key_path=...` explicitly. |

---

## 7. Layout

```
sdk/developer/
├── README.md                       (this file)
├── audio/
│   ├── audio_sdk.py
│   ├── test_volume_65.py           (legacy demo)
│   └── examples/
│       ├── record_play.py
│       ├── live_passthrough.py
│       └── diagnostic.py
├── ssh/
│   ├── ssh_sdk.py
│   └── examples/
│       ├── run_command.py
│       ├── push_pull_files.py
│       └── deploy_audio_sdk.py
└── onboarding/
    └── onboard_device.py
```
