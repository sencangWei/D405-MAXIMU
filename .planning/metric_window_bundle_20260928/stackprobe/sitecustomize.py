"""Opt-in SIGUSR1 Python stack dump for a stalled MASt3R diagnostic run."""

import faulthandler
import signal


faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
