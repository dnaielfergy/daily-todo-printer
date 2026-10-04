from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class PrinterError(RuntimeError):
    pass


def print_raw_windows(data: bytes, printer_name: str | None = None) -> None:
    """Send raw ESC/POS bytes through the Windows print spooler.

    The NETUM printer should first be installed with its Windows driver.
    Set RECEIPT_PRINTER_NAME to the exact Windows printer name, or pass it
    explicitly. This function intentionally uses only the Python standard
    library so the core project has no runtime dependency for printing.
    """
    if os.name != "nt":
        raise PrinterError("Raw spooler printing is only available on Windows")

    printer_name = printer_name or os.getenv("RECEIPT_PRINTER_NAME")
    if not printer_name:
        raise PrinterError("Set RECEIPT_PRINTER_NAME to the Windows printer name")

    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [
            ("pDocName", wintypes.LPWSTR),
            ("pOutputFile", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR),
        ]

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise ctypes.WinError(ctypes.get_last_error())

    doc_started = False
    page_started = False
    try:
        doc = DOC_INFO_1("Receipt Todo", None, "RAW")
        if not winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise ctypes.WinError(ctypes.get_last_error())
        doc_started = True

        if not winspool.StartPagePrinter(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        page_started = True

        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(data)
        if not winspool.WritePrinter(handle, buffer, len(data), ctypes.byref(written)):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(data):
            raise PrinterError(f"Printer accepted {written.value} of {len(data)} bytes")
    finally:
        if page_started:
            winspool.EndPagePrinter(handle)
        if doc_started:
            winspool.EndDocPrinter(handle)
        winspool.ClosePrinter(handle)
