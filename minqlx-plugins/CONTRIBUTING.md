# Tr1ckHouse minqlx plugins — notes

## Threading rule

**minqlx / game-engine calls must only ever happen on the main game thread.**

Never call `minqlx.console_command()`, touch `self.players()`, mutate player/game state, or call into minqlx's C layer from a background thread, a `threading.Timer`, or an external library's callback thread.

For delayed or periodic execution, use minqlx's own mechanisms instead:

- `@minqlx.delay(seconds)` for a one-off delayed call
- the `frame` hook (self-rate-limited, e.g. via `time.monotonic()`) for periodic checks
- `@minqlx.thread` only for genuinely blocking I/O that does **not** touch minqlx/game state — use `@minqlx.next_frame` to hand results back to the main thread afterward, rather than calling engine functions directly from inside the threaded function

**Why:** an unmanaged background thread calling into minqlx's C layer was the confirmed source of intermittent server crashes (heap corruption) in `autorestart.py` (fixed in v2.0).

## Credits

[tjone720](https://github.com/tjone720) — thanks for finding and fixing game crashes issues.
