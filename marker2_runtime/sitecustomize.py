"""Small, quality-neutral fixes for page-at-a-time Marker 2 runs."""

import os
import subprocess
from types import SimpleNamespace

from marker.builders.line import LineBuilder
from surya.inference.backends import spawn as inference_spawn


_original_ocr_error_detection = LineBuilder.ocr_error_detection


def _skip_redundant_empty_page_check(self, pages, provider_page_lines):
    # A page with no PDF text layer always requires OCR. Loading the separate
    # OCR-error classifier cannot change that decision and costs ~700-800 MB.
    if all(not provider_page_lines.get(page.page_id) for page in pages):
        return SimpleNamespace(
            labels=["bad"] * len(pages),
            scores=[1.0] * len(pages),
        )
    return _original_ocr_error_detection(self, pages, provider_page_lines)


LineBuilder.ocr_error_detection = _skip_redundant_empty_page_check


_original_stop_process = inference_spawn._stop_process


def _stop_process_reliably_on_windows(pid, name):
    if os.name != "nt":
        return _original_stop_process(pid, name)
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )


inference_spawn._stop_process = _stop_process_reliably_on_windows
