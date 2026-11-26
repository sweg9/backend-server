# Firmware

Arduino sketches for the hand. The hub on the home laptop talks to the board over USB serial (`serial_port` in `settings/hub.yaml`, with `hand: linear_actuators`). Not part of the server image: the `Dockerfile` copies only `src/` and `settings/server.yaml`.

| Sketch | What it does now |
|---|---|
| `actuator/actuator.ino` | Bench test: moves one linear actuator 20 mm out and back, with a 1 s rest at each end (about 7 s per round trip), forever |

## Board

- An AVR board of the Arduino Uno class (the `arduino:avr` core). Pins 5 and 6 must be PWM pins.
- A BTS7960 motor driver between the board and the linear actuator, with its own motor supply. Join the grounds of the board and the driver.
- Upload: open `actuator/actuator.ino` in the Arduino IDE (the sketch must stay in a folder of the same name), pick the board and port, and upload.

## Pin wiring

| Arduino pin | BTS7960 | Role |
|---|---|---|
| 5 (PWM) | `RPWM` | Out (forward) at full duty |
| 6 (PWM) | `LPWM` | Back at full duty |
| 7 | `R_EN` | Enable, held high |
| 8 | `L_EN` | Enable, held high |
| `LED_BUILTIN` | none | On while the actuator moves |

- Travel is timed, not measured: `moveMm` runs the motor at full duty for `mm / SPEED_MM_S` seconds. `SPEED_MM_S` (8 mm/s) is an estimate: measure the real travel once and set it. Out and back can differ a little, so the position can drift over many round trips.
- Only one direction pin is driven at a time; the other is set to 0 first.

## Serial command format

The bench sketch reads no serial commands yet. The format below is **proposed** for `linear_actuators` in the RAL (`src/backend/hub/ral/base.py`: `press`, `release`, `stop`) and is not built on either side.

- 115200 baud, 8N1. One command per line, ASCII, ending in `\n`. The board answers each line with one line.

| Command | RAL call | Board does | Answer |
|---|---|---|---|
| `PRESS <actuator> <push_ms>` | `press(actuator, push_ms)` | Out for `push_ms`, then holds | `OK PRESS <actuator>` |
| `RELEASE <actuator>` | `release(actuator)` | Back to the start | `OK RELEASE <actuator>` |
| `STOP` | `stop()` | Every actuator off at once (FR-10) | `OK STOP` |
| `PING` | none | Nothing | `OK PING` |

- An unknown or malformed line answers `ERR <reason>` and moves nothing.
- After `STOP` the board ignores `PRESS` until it is reset or sent `RESET` (the real hand needs a person, C-04).
- `<actuator>` is an id from `actuators` in `settings/hub.yaml` (`A1`, `A2`).
