"""Greeter resume reading: configured .pdf/.docx must convert, not crash.

Regression for the resume-format fix: _get_resume_text used to read the
configured resume_path as UTF-8 text, so a .pdf/.docx path raised
UnicodeDecodeError inside the greeting flow. It now dispatches through
_read_base_resume_text and returns "" when the file cannot be read.
"""

from pathlib import Path

from bosshunter.ai.greeter import _get_resume_text

_SAMPLES = Path(__file__).parent / "fixtures" / "resume_samples"


def _config(path):
    return {"profile": {"resume_path": str(path)}}


def test_docx_with_content_controls_reads_full_text():
    text = _get_resume_text(_config(_SAMPLES / "sdt.docx"))
    assert "张三" in text
    assert "工作经历" in text
    assert "负责后端开发与接口设计" in text


def test_pdf_reads_text():
    text = _get_resume_text(_config(_SAMPLES / "reportlab.pdf"))
    assert "Zhang San" in text


def test_missing_resume_returns_empty():
    assert _get_resume_text(_config(_SAMPLES / "no-such-file.md")) == ""


def test_unreadable_resume_returns_empty_instead_of_crashing():
    # garbage.pdf is not a valid PDF: greeting flow must not crash on it.
    assert _get_resume_text(_config(_SAMPLES / "garbage.pdf")) == ""
