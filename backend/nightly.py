"""Refresh every source, one after another. Meant to run every night; see db/nightly.plist.

A step that fails does not stop the others: the report keeps the data of that source from
the last good night, and the failure stays in ingest_run and in logs/inforealtor.log.
"""

import os
import subprocess
import sys
import tempfile
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from applog import get_logger

HERE = Path(__file__).parent
CONDO_MD = HERE / "data" / "Condo_MD.csv"
CONDO_MD_URL = "https://www2.myfloridalicense.com/sto/file_download/extracts/Condo_MD.csv"
MIN_BYTES = 500_000  # the real file is about 1.4 MB: anything much smaller is an error page

CODES = ["01"]  # jurisdictions that are loaded

log = get_logger("nightly")


def download_condo_md():
    """Replace data/Condo_MD.csv with today's file from DBPR, keeping the date DBPR gives it."""
    r = requests.get(CONDO_MD_URL, timeout=120)
    r.raise_for_status()
    if len(r.content) < MIN_BYTES or b"Project Number" not in r.content[:200]:
        raise RuntimeError(f"unexpected answer from DBPR ({len(r.content)} bytes)")
    with tempfile.NamedTemporaryFile(dir=CONDO_MD.parent, delete=False) as f:
        f.write(r.content)
    if "Last-Modified" in r.headers:
        modified = parsedate_to_datetime(r.headers["Last-Modified"]).timestamp()
        os.utime(f.name, (modified, modified))
    os.chmod(f.name, 0o644)  # temporary files are private by default
    os.replace(f.name, CONDO_MD)  # the old file stays until the new one is complete


def run(script, *args):
    """Run one of the project's scripts; False if it ended with an error."""
    done = subprocess.run(
        [sys.executable, script, *args], cwd=HERE, capture_output=True, text=True
    )
    if done.returncode:
        log.error("%s %s failed:\n%s", script, " ".join(args), done.stderr[-2000:])
    return not done.returncode


def main():
    log.info("nightly refresh started")
    failed = []
    for code in CODES:
        for script in ("load_property.py", "load_location.py", "load_recert.py", "load_permits.py"):
            if not run(script, code):
                failed.append(f"{script} {code}")
    try:
        download_condo_md()
    except (RuntimeError, requests.RequestException) as error:
        log.warning("Condo_MD.csv not downloaded, keeping the old file (%s)", error)
        failed.append("download Condo_MD.csv")
    # the SIRS files are exported by hand from DBPR's panel: these steps reuse the ones in data/
    for script in ("match_condos.py", "match_sirs.py", "load_dbpr.py"):
        if not run(script):
            failed.append(script)
            break  # each one needs the one before
    if failed:
        log.error("nightly refresh finished with %s failed steps: %s", len(failed), ", ".join(failed))
        raise SystemExit(1)
    log.info("nightly refresh finished, every step ok")


if __name__ == "__main__":
    main()
