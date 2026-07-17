"""Logging setup shared by the API and the console scripts: one
``event=... key=value`` line per event on stdout, INFO level."""

import logging


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
