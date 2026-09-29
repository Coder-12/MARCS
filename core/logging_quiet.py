import os
import logging

def silence_all_logs_if_ingest():
    """
    During golden ingest we must suppress ALL logging output.
    This disables all handlers and sets logging to CRITICAL.
    """
    if os.environ.get("MACRS_INGEST") == "1":
        logging.disable(logging.CRITICAL)