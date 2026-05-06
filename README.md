# stellar_pomodoro

A Pomodoro timer for Pimoroni's [Stellar Unicorn](https://shop.pimoroni.com/products/stellar-unicorn) — a 16×16 RGB LED matrix board powered by a Raspberry Pi Pico (RP2040). The firmware runs as **MicroPython** directly on the device.

## Pomodoro cycle

The app runs four full Pomodoro cycles before signalling completion:

| Phase | Duration  | Colour                |
| ----- | --------- | --------------------- |
| Work  | 25 min    | Blue   `(0, 80, 220)` |
| Break | 5 min     | Orange `(220, 80, 0)` |
| Done  | —         | Green  `(0, 255, 0)`  |

When a phase ends, a short tone plays (880 Hz after work, 440 Hz after a break). Once all four cycles have completed, the display turns solid green and a final two-tone chime (660 Hz / 880 Hz) is played.

During the last 10 seconds of any phase the display blinks by toggling between full brightness and the user's chosen brightness — the colour stays the same so the remaining time is always readable.

## Controls

The Stellar Unicorn has six buttons used by this app:

| Button             | Action                                                                  |
| ------------------ | ----------------------------------------------------------------------- |
| **A**              | Start / pause / resume the timer. When in *Done* state, resets to idle. |
| **B** (double-tap) | Reset to idle (two presses within 2 s).                                 |
| **C**              | Toggle the display on/off (audio and timer keep running).               |
| **Brightness +**   | Increase brightness (10 discrete steps, 1–10).                          |
| **Brightness −**   | Decrease brightness.                                                    |

A single B press alone does nothing — it must be confirmed by a second press to avoid accidental resets.

## States

```text
IDLE  ──A──▶  RUNNING  ──A──▶  PAUSED  ──A──▶  RUNNING
                  │                                │
                  └──── timer reaches 0 ───────────┘
                              │
                          (cycle++)
                              │
                  4 cycles ──▶  DONE  ──A──▶  IDLE
```

A double-tap on B returns to IDLE from any state.

## Display layout

The 16×16 matrix shows the remaining time as `MM:SS` arranged in a 2×2 grid of 8×8 glyphs:

```text
┌────────┬────────┐
│ tens   │ ones   │   ← minutes (top row)
│ of min │ of min │
├────────┼────────┤
│ tens   │ ones   │   ← seconds (bottom row)
│ of sec │ of sec │
└────────┴────────┘
```

Glyphs are stored in [digits.py](digits.py) as a flat `bytes` object: 12 glyphs × 8 rows = 96 bytes, MSB = column 0. Indexes 0–9 are digits, 10 is `-`, 11 is `+`.

## Project layout

| File | Purpose |
| ---- | ------- |
| [main.py](main.py) | Entry point; runs the state machine, polls buttons, drives the display, schedules audio. |
| [digits.py](digits.py) | 8×8 bitmap glyphs for digits and a few symbols. |
| `typings/` | MicroPython stubs (`pimoroni-pico-stubs` v1.21.0) for IDE autocompletion. **Not** deployed. |
| `.micropico` | Marker file that the MicroPico VS Code extension uses to recognise the project. |

Only `main.py` and `digits.py` are deployed to the device.

## Architecture notes

- **Non-blocking main loop.** A single `while True` loop polls buttons, advances the timer, services the audio queue, and refreshes the display. There are no blocking sleeps longer than the poll interval (`50 ms` while running, `100 ms` while idle).
- **Tick-based timing.** The remaining time is derived from `time.ticks_ms()` deadlines via `time.ticks_diff`, which is wrap-safe on MicroPython and does not drift like accumulated `sleep` calls.
- **Render cache.** [main.py:56](main.py#L56) keeps the four currently drawn digits and the active foreground pen. Only digits that actually changed are redrawn, and `su.update(graphics)` is skipped when nothing was dirty.
- **Integer brightness.** Brightness is held as an integer 1–10 to avoid float drift across repeated up/down presses; the hardware call divides by 10 only at the boundary ([main.py:173](main.py#L173)).
- **Countdown blink via brightness.** Instead of inverting colours during the last 10 seconds, the app flips the global brightness — fewer pixel writes and the colour code (work blue / break orange) remains visible.
- **Audio state machine.** `service_audio()` drives a small queue of tones through `IDLE → PLAYING → RELEASING → IDLE` so the main loop never has to block while a tone is playing.
- **Debouncing.** Every button uses an edge-detected press (`current && !previous`) plus a `250 ms` debounce window. Button B additionally requires a double-press within `2000 ms` to reset.
- **Display off.** Pressing C calls `su.set_brightness(0.0)` and clears the framebuffer; the timer and audio continue in the background. Pressing C again restores the user-selected brightness and re-renders the current state.

## Hardware API quick reference

```python
from stellar import StellarUnicorn, Channel
from picographics import PicoGraphics, DISPLAY_STELLAR_UNICORN

su = StellarUnicorn()
graphics = PicoGraphics(display=DISPLAY_STELLAR_UNICORN)
su.update(graphics)  # push framebuffer to LEDs
```

- Display: 16×16 px (`StellarUnicorn.WIDTH`, `StellarUnicorn.HEIGHT`)
- Buttons: `SWITCH_A`, `SWITCH_B`, `SWITCH_C`, `SWITCH_D`, `SWITCH_BRIGHTNESS_UP/DOWN`, `SWITCH_VOLUME_UP/DOWN`, `SWITCH_SLEEP`
- Audio: `Channel` class with SINE / SQUARE / SAW / TRIANGLE / NOISE waveforms, ADSR envelope

## Deployment

There is no classical build step — files are uploaded straight to the device.

1. Install the [MicroPico](https://marketplace.visualstudio.com/items?itemName=paulober.pico-w-go) VS Code extension. The `.micropico` marker file makes it recognise this folder as a Pico project.
2. Connect the Stellar Unicorn over USB.
3. Right-click `main.py` (and `digits.py`) → **Upload file to Pico**.
4. Reset the device. `main.py` is executed automatically on boot.

The MicroPico REPL gives you a serial console for interactive debugging.

## Configuration

The most common knobs live at the top of [main.py](main.py):

```python
WORK_SECONDS = 25 * 60       # work phase length
BREAK_SECONDS = 5 * 60       # break phase length
NUM_CYCLES = 4               # work+break cycles before DONE
DEBOUNCE_MS = 250            # button debounce window
DOUBLE_PRESS_MS = 2000       # B double-tap window
COUNTDOWN_BLINK_FROM = 10    # seconds at which blink starts
BRIGHTNESS_MIN = 1           # 1..10 integer brightness range
BRIGHTNESS_MAX = 10
```

Colours are defined as `PEN_WORK`, `PEN_BREAK`, and `PEN_DONE` just below.
