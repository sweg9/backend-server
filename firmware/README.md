# Firmware

Arduino sketches for the hand. The hub on the home laptop talks to the board over USB serial (`serial_port` in `settings/hub.yaml`, with `hand: linear_actuators`). Not part of the server image: the `Dockerfile` copies only `src/` and `settings/server.yaml`.

| Sketch | What it does |
|---|---|
| `actuator/actuator.ino` | The hand: reads the serial commands below from the hub (`hand: linear_actuators`) |
| `bench/bench.ino` | Bench test: moves one linear actuator 20 mm out and back, with a 1 s rest at each end (about 7 s per round trip), forever. Reads no commands |

## Board

- An AVR board of the Arduino Uno class (the `arduino:avr` core). Pins 5 and 6 must be PWM pins.
- A BTS7960 motor driver between the board and the linear actuator, with its own motor supply. Join the grounds of the board and the driver.
- Upload: open `actuator/actuator.ino` (or `bench/bench.ino`) in the Arduino IDE (the sketch must stay in a folder of the same name), pick the board and port, and upload.

## Pin wiring

| Arduino pin | BTS7960 | Role |
|---|---|---|
| 5 (PWM) | `RPWM` | A1 out (forward) at full duty |
| 6 (PWM) | `LPWM` | A1 back at full duty |
| 7 | `R_EN` | A1 enable, held high |
| 8 | `L_EN` | A1 enable, held high |
| 9, 10, 11, 12 | a second BTS7960 | A2, the same roles in the same order. Not wired on the bench yet |
| `LED_BUILTIN` | none | On while the actuator moves |

- Travel is timed, not measured. In `bench.ino`, `moveMm` runs the motor at full duty for `mm / SPEED_MM_S` seconds; `SPEED_MM_S` (8 mm/s) is an estimate. In `actuator.ino` the hub gives the push time in ms (`PUSH_MS` in `src/backend/hub/orchestrator.py`, 400 ms, about 3 mm at 8 mm/s: tune it on the panel). Back runs 1.2 times the push plus 100 ms, so the actuator always reaches its end switch and does not drift.
- Only one direction pin is driven at a time; the other is set to 0 first.

## Serial command format

Built on both sides: `actuator/actuator.ino` and `src/backend/hub/ral/linear_actuators.py` (the RAL: `press`, `release`, `stop`, `src/backend/hub/ral/base.py`). Tests: `tests/hub/test_linear_actuators.py`, with a fake board that answers like the sketch.

- 115200 baud, 8N1. One command per line, ASCII, ending in `\n`. The board answers each command with one line. It prints `READY` when it starts (an Uno restarts when the port opens, so the hub waits 2 s).
- Answers name the command and the actuator, so `STOP` can get through while a `PRESS` still waits for its answer: `OK <command> [<actuator>]` or `ERR <command> [<actuator>] <reason>`.

| Command | RAL call | Board does | Answer |
|---|---|---|---|
| `PRESS <actuator> <push_ms>` | `press(actuator, push_ms)` | Out for `push_ms` (1 to 2000), then holds | `OK PRESS A1` when the push is done |
| `RELEASE <actuator>` | `release(actuator)` | Back to the start | `OK RELEASE A1` when it is back |
| `STOP` | `stop()` | Every motor off at once, also in the middle of a move (FR-10) | `ERR PRESS A1 stopped` for a move it cut, then `OK STOP` |
| `RESET` | `reset()`, then the next `press` | Pulls every actuator all the way back, then accepts `PRESS` again | `OK RESET` when they are back |
| `PING` | none | Nothing | `OK PING`, or `OK PING stopped` after a `STOP` |

| `ERR` reason | When |
|---|---|
| `stopped` | `PRESS` or `RELEASE` after `STOP`, before `RESET` (C-04) |
| `busy` | That actuator is still moving, or `RESET` is pulling everything back |
| `bad_ms` | `push_ms` missing or outside 1 to 2000 |
| `not_back` | `PRESS` while the actuator is still out: `RELEASE` first |
| `unknown_actuator`, `unknown_command` | The actuator is written `?` when it is missing, the command is written `?` when it is unknown |

Safety in the board itself, so the hand stops even if the hub or the USB link dies (C-04, wiki Stack):

- A push is capped at 2000 ms (`MAX_PUSH_MS`).
- An actuator held out for 5 s (`HOLD_MAX_MS`) without a `RELEASE` goes back by itself and prints `EVT RELEASE A1 hold_timeout`.
- After `STOP` nothing moves until `RESET`. The hub sends `RESET` only when the resident starts something new after the stop (the orchestrator calls `reset()`), right before a press that passed the safety gate. Pressing the Arduino's reset button also clears the stop, without moving.

## By hand

`backend hand` talks to the board without the hub (the hub holds the port, so stop it first). It reads `serial_port` and `actuators` from `settings/hub.yaml`, or `--port`.

```sh
uv run backend hand ping
uv run backend hand press A1 --ms 400      # press, then release
uv run backend hand press A1 --hold        # press and stay out (it goes back by itself after 5 s)
uv run backend hand release A1
uv run backend hand stop
uv run backend hand reset                  # after a stop: pull everything back, accept PRESS again
```

On the hub: `hand: linear_actuators` and `serial_port` (on Windows `COM3` or the like, as the Arduino IDE or Device Manager shows; on the Mac `/dev/cu.usbmodem…` for an Uno, `ls /dev/cu.*`) in `settings/hub.yaml`. `go_to_spot` only logs: the team member holds or pushes the actuators, and the orchestrator shows the move for 2 s. Waiting for the presenter's confirmation in the app (wiki Modules) is not built.
