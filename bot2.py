"""
Bot 2 — a second, independent momentum-rotation sleeve (engine: rotation.py,
evidence: rotation_backtest.py). Separate process, log, state file, kill
switch and IBKR client id from bot.py; shares DRY_RUN / host+port / live flag.

Its universe must not overlap bot.py's when both run (each bot reads the
account's real shares for its tickers, so a shared ticker would be
double-counted). bot2.py refuses to start if it does.

Run:  python bot2.py           (loop every config.LOOP_SECONDS)
      python bot2.py --once    (one pass, then exit — scheduled task)
Stop: Ctrl+C, or create KILL_SWITCH_BOT2 (or KILL_SWITCH) in this folder.
"""

import sys
import logging

import config
from rotation import Rotation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("bot2.log"), logging.StreamHandler()],
)
log = logging.getLogger("bot2")


def main(once=False):
    if not config.BOT2_ENABLED:
        log.info("config.BOT2_ENABLED is False — bot2 disabled. Exiting.")
        return
    if config.STRATEGY == "rotation":
        overlap = set(config.BOT2_UNIVERSE) & set(config.ROTATION_UNIVERSE)
        if overlap:
            log.error(f"BOT2_UNIVERSE overlaps bot.py's ROTATION_UNIVERSE on {sorted(overlap)}; "
                      "the two bots would double-count those shares. Fix config.py. Exiting.")
            return
    Rotation(
        name="bot2",
        universe=config.BOT2_UNIVERSE,
        top_n=config.BOT2_TOP_N,
        capital=config.BOT2_CAPITAL_DOLLARS,
        client_id=config.BOT2_CLIENT_ID,
        state_file=config.BOT2_STATE_FILE,
        kill_files=[config.BOT2_KILL_SWITCH_FILE, config.KILL_SWITCH_FILE],
        log=log,
    ).run(once=once)


if __name__ == "__main__":
    try:
        main(once="--once" in sys.argv[1:])
    except KeyboardInterrupt:
        log.info("Stopped by user. Clean exit.")
