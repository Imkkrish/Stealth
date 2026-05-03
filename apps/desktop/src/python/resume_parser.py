"""Extract plain text from a resume file (.pdf, .docx, .txt, .md)."""

from pathlib import Path

MAX_RESUME_CHARS = 50_000


class ResumeParseError(Exception):
    pass


def parse(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise ResumeParseError(f"File not found: {path}")

    ext = p.suffix.lower()
    if ext == ".pdf":
        text = _parse_pdf(p)
    elif ext == ".docx":
        text = _parse_docx(p)
    elif ext in (".txt", ".md"):
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        raise ResumeParseError(f"Unsupported resume format: {ext}. Use .pdf, .docx, .txt, or .md.")

    text = text.strip()
    if not text:
        raise ResumeParseError("Resume appears to be empty after parsing.")
    if len(text) > MAX_RESUME_CHARS:
        text = text[:MAX_RESUME_CHARS] + "\n\n[...truncated]"
    return text


def _parse_pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ResumeParseError("pypdf is not installed. Run: pip install pypdf") from e

    try:
        reader = PdfReader(str(p))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except Exception as e:
        raise ResumeParseError(f"Failed to read PDF: {e}") from e


def _parse_docx(p: Path) -> str:
    try:
        from docx import Document
    except ImportError as e:
        raise ResumeParseError("python-docx is not installed. Run: pip install python-docx") from e

    try:
        doc = Document(str(p))
        return "\n".join(para.text for para in doc.paragraphs)
    except Exception as e:
        raise ResumeParseError(f"Failed to read DOCX: {e}") from e
