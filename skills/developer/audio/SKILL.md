---
name: audio
description: Control the on-device microphone and speaker via the audio SDK. Use when the user types /audio, /stream, /record, /play, /volume, /mic-check, /listen, asks to "record", "play", "stream the mic", "raise the volume", "test the speaker", or generally wants to interact with the device's audio hardware from chat.
argument-hint: "<subcommand> [args]  e.g. stream 10  |  volume 70  |  record 5  |  health"
---

# /audio

Lightweight chat interface to the on-device `audio_sdk` (installed at `~/sdk/audio/audio_sdk.py`). One slash command, several subcommands.

## Subcommands

| Form | Action |
|---|---|
| `/audio stream <seconds>` | Live mic→speaker passthrough for N seconds. Default 10. |
| `/audio record <seconds>` | Record N seconds; reply with the WAV as a Telegram attachment. Default 5. |
| `/audio play <path>` | Play a WAV file already on the device through the speaker. |
| `/audio volume <percent>` | Set speaker volume 0-100 (DACL+DACR). With no arg, report current. |
| `/audio mic-check` | Quick mic capture + RMS readout. Tells you whether the mic is working. |
| `/audio health` | Full `health_check()` report with verdict (`ok` / `mic_silent` / `speaker_off` / `no_device`). |
| `/audio mute` | Speaker volume 0%. |
| `/audio unmute` | Speaker volume 60%. |
| `/audio gain high` | Apply mic high-gain defaults (ADCL/ADCR=255, PGA=+42 dB). |

Top-level shortcuts the user may type without the `/audio` prefix — route them to the equivalent subcommand: `/stream N` → `audio stream N`, `/record N` → `audio record N`, `/volume N` → `audio volume N`, `/mic-check` → `audio mic-check`, `/health` → `audio health`.

## Workflow

1. Parse the subcommand and args from `$ARGUMENTS`. If absent, default to `health`.
2. Invoke the matching SDK call by running Python from bash. Always import via `sys.path.insert(0, "/home/system/sdk/audio")` to find `audio_sdk`.
3. If the call needs sudo (recording / mixer changes / playback), wrap with `sudo -n python3 -c "..."`. The `system` user has passwordless sudo on Lobster devices.
4. Reply concisely: include the verdict / measurement / file. For `record`, attach the WAV file via the Telegram reply tool's `files=[...]` parameter. For long-running ops (stream > 5 s), send a short "starting" reply, run the op, then reply again with the result so the user gets a notification.
5. Never hold the Python process open for an unbounded `/audio stream` — cap it at the requested duration; reject anything over 60 s with a polite message.

## Quick Start (bash command shapes)

```bash
# stream N seconds
sudo -n python3 -c "import sys; sys.path.insert(0, '/home/system/sdk/audio'); from audio_sdk import AudioSDK; \
  sdk=AudioSDK(); sdk.stream_passthrough(duration=N); sdk.close()"

# record N seconds, save to /tmp/chat_record.wav
sudo -n python3 -c "import sys; sys.path.insert(0, '/home/system/sdk/audio'); from audio_sdk import AudioSDK; \
  sdk=AudioSDK(); sdk.record('/tmp/chat_record.wav', N); sdk.close()"

# set speaker volume to N percent
sudo -n python3 -c "import sys; sys.path.insert(0, '/home/system/sdk/audio'); from audio_sdk import AudioSDK; \
  sdk=AudioSDK(); sdk.set_volume(N); sdk.close()"

# health check (returns JSON)
sudo -n python3 -c "import sys, json; sys.path.insert(0, '/home/system/sdk/audio'); from audio_sdk import AudioSDK; \
  print(json.dumps(AudioSDK().health_check(), default=str))"
```

## Examples

**Input:** `/audio stream 10`
**Action:** run `stream_passthrough(duration=10)`. Reply: `Streaming mic -> speaker for 10s ... done. (RMS measured: ...)`.

**Input:** `/audio record 5`
**Action:** record 5 s to `/tmp/chat_record.wav`, attach the file in the Telegram reply, include peak/rms in caption.

**Input:** `/audio volume 70`
**Action:** `set_volume(70)`; reply `Speaker volume now 70% (DACL/DACR -6.5 dB)`.

**Input:** `/audio volume`
**Action:** call `get_volume()`; reply `Current speaker volume: 50%`.

**Input:** `/audio mic-check`
**Action:** record 1 s, compute RMS. Reply `Mic captured. peak=32768 (100%), rms=3690 -> mic OK`. If rms < 100, reply `Mic silent. Run /audio health for full diagnostic.`

**Input:** `/audio health`
**Action:** run `health_check(loopback=True)`, format result as a compact table:
```
verdict        : ok
device_index   : 1
speaker_volume : 70%
mic_volume     : 100%
mic_pga_db     : 42.0
mic_rms        : 3690
loopback_rms   : 9137
```

**Input:** `/stream 15s`
**Action:** treat as `/audio stream 15`. Same as the first example with duration=15.

## Tools

- **Bash** to invoke `python3 -c "..."` against the on-device SDK.
- **Read** to verify `~/sdk/audio/audio_sdk.py` exists if the user reports an import error.
- The Telegram reply tool to send back results and (for `record`) attach WAV files.

## Error Handling

| Scenario | Action |
|---|---|
| Duration argument missing or non-numeric | Default to 5 (record) / 10 (stream); mention the default in the reply. |
| Duration > 60 s | Reject with: "Maximum 60 s per call. Use /audio stream 60 multiple times if needed." |
| `ModuleNotFoundError: audio_sdk` | Reply: "audio_sdk not installed on this device. Run the install per `sdk/developer/README.md` Path A or ask me to install it." |
| `MixerError: control not found` | Reply: "Codec on this device doesn't expose that control. /audio health will show what's available." |
| `verdict == "mic_silent"` after `/audio health` | Reply with the loopback_rms: if healthy, the codec is fine and the issue is the analog mic input (cable / MICBIAS). If also low, the codec itself is misbehaving. |
| `verdict == "no_device"` | Reply: "PyAudio sees no device on card 1. Run `aplay -l` and check whether the codec driver is loaded." |

## Rules

- Never run the SDK without `sudo -n` on Lobster devices unless the user is in the `audio` group AND the current shell predates the group change.
- Never set speaker volume above 80 % without explicit confirmation in the same message — the device may be in a shared room.
- For `/audio record`, always attach the WAV; do not paste raw PCM in chat.
- Cap `stream`, `record`, and `passthrough` at 60 s per invocation.
- After any mixer change, the next message in the conversation should re-confirm the current value via `get_mixer` so the user has visible state.

## Output Template

```
[/audio <subcommand>] <one-line outcome>
<optional 2-5 line detail block>
```

Examples:
```
[/audio volume 70] Speaker now 70% (DACL/DACR = 178/255, -6.5 dB).
```
```
[/audio health] verdict=ok
device_index=1  speaker=70%  mic=100%  pga=+42dB
mic_rms=3690    loopback_rms=9137
```
