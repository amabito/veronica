"""Strict labelled JSON/text/PDF adapter. Unknown layouts abstain, not fabricate."""
from __future__ import annotations

from io import BytesIO
import json
import multiprocessing

from .contracts import Document, ReviewRequired

ALIASES = {"書類番号": "document_id", "種別": "kind", "発行者": "issuer",
           "日付": "document_date", "件名": "title", "金額": "amount", "通貨": "currency"}
FIELDS = {"document_id", "kind", "issuer", "document_date", "title", "amount", "currency"}


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 20:
        raise ValueError("encrypted_or_too_many_pages")
    text = ""
    for page in reader.pages:
        text += (page.extract_text() or "") + "\n"
        if len(text) > 65_536:
            raise ValueError("too_much_text")
    return text


def _pdf_worker(data, connection):
    try:
        # Best-effort POSIX resource bounds; Windows uses the parent timeout.
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        except (ImportError, ValueError, OSError):
            pass
        text = _pdf_text(data)
        connection.send((True, text))
    except Exception:
        connection.send((False, "pdf_extraction_failed"))
    finally:
        connection.close()


def pdf_text(data: bytes, timeout: float = 10.0) -> str:
    """Disposable parser process with a parent deadline; no OCR or external calls."""
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_pdf_worker, args=(data, send), daemon=True)
    try:
        process.start()
        send.close()
        if not receive.poll(timeout):
            raise ReviewRequired("pdf_timeout")
        try:
            ok, result = receive.recv()
        except EOFError:
            raise ReviewRequired("pdf_extraction_failed") from None
        if not ok:
            raise ReviewRequired(result)
        return result
    finally:
        receive.close()
        send.close()
        if process.pid is not None:
            process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()


def extract_document(data: bytes, suffix: str) -> Document:
    try:
        if suffix == ".json":
            return Document.load(data.decode("utf-8-sig"))
        if suffix == ".pdf":
            text = pdf_text(data)
        elif suffix == ".txt":
            text = data.decode("utf-8-sig")
        else:
            raise ReviewRequired("unsupported_format")
        if len(text) > 65_536:
            raise ReviewRequired("text_too_large")
        values = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            key, sep, value = line.replace("：", ":", 1).partition(":")
            key = ALIASES.get(key.strip(), key.strip())
            if not sep or key not in FIELDS or key in values:
                raise ReviewRequired("unknown_or_ambiguous_layout")
            values[key] = value.strip()
        return Document.load(json.dumps(values, ensure_ascii=True))
    except (UnicodeError, ValueError, TypeError) as error:
        if isinstance(error, ReviewRequired):
            raise
        raise ReviewRequired("document_extraction_failed") from None
