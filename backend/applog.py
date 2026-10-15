"""One log for the whole project: short messages on the screen, every detail in logs/condoreadout.log."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "condoreadout.log"
MAX_BYTES = 5_000_000  # start a new file at 5 MB
OLD_FILES = 5  # keep condoreadout.log.1 ... .5


def get_logger(name):
    """Logger called 'condoreadout.<name>'. The screen gets INFO and above; the file also gets DEBUG."""
    root = logging.getLogger("condoreadout")
    if not root.handlers:  # set up once, however many modules ask
        LOG_DIR.mkdir(exist_ok=True)
        to_file = RotatingFileHandler(
            LOG_FILE, maxBytes=MAX_BYTES, backupCount=OLD_FILES, encoding="utf-8"
        )
        to_file.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        to_screen = logging.StreamHandler()  # stderr: it does not mix with the JSON that core prints
        to_screen.setLevel(logging.INFO)
        to_screen.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.setLevel(logging.DEBUG)
        root.addHandler(to_file)
        root.addHandler(to_screen)
    return root.getChild(name)
