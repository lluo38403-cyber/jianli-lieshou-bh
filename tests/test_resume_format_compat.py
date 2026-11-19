"""Resume format compatibility: real .md/.docx/.pdf/.txt samples end to end.

Regression tests for the 2026-11-19 format-compat fix. Fixtures under
tests/fixtures/resume_samples are REAL files (python-docx / LibreOffice /
reportlab / pypdf output), not mocks: normal.docx, sdt.docx (Word content
controls), nested-table.docx, header-only.docx, word-chinese.pdf
(LibreOffice-converted Chinese Word PDF), reportlab.pdf, blank.pdf,
encrypted.pdf, encrypted-empty-pw.pdf, truncated.pdf, garbage.pdf,
zero-page.pdf, old.doc and the broken-docx variants.
"""

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from bosshunter.web.resume_info import ensure_resume_markdown_file, load_resume_info
from bosshunter.web.resume_original import resolve_configured_resume_files
from bosshunter.web.resume_upload import (
    ResumeUploadError,
    docx_to_markdown,
    pdf_to_markdown,
    prepare_resume_content,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "resume_samples"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _sample(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _build_docx(document_xml: str, extra_files: dict[str, bytes] | None = None) -> bytes:
    """Build a minimal .docx from raw document.xml with only the stdlib."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", document_xml)
        for name, data in (extra_files or {}).items():
            archive.writestr(name, data)
    return buf.getvalue()


def _para(text: str) -> str:
    return f'<w:p xmlns:w="{W_NS}"><w:r><w:t>{text}</w:t></w:r></w:p>'


def _doc_xml(*body_children: str) -> str:
    return (
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(body_children)
        + "</w:body></w:document>"
    )


# ── .docx: real fixtures ──────────────────────────────────────────────


def test_docx_normal_keeps_all_text():
    text = docx_to_markdown(_sample("normal.docx"))
    assert "张三" in text
    assert "工作经历" in text
    assert "Python、Django、MySQL" in text
    assert "XX大学" in text


def test_docx_content_controls_keep_body_text():
    # Word 模板常用 w:sdt 内容控件包段落；旧实现只看 body 直接子节点，
    # 会把正文全丢掉（只剩表格 51 字）。回归：正文必须完整保留。
    text = docx_to_markdown(_sample("sdt.docx"))
    assert "张三" in text
    assert "工作经历" in text
    assert "负责后端开发与接口设计" in text
    assert len(text) > 200


def test_docx_nested_table_keeps_inner_text():
    text = docx_to_markdown(_sample("nested-table.docx"))
    assert "内层嵌套表格文字" in text
    assert "熟练使用 Python" in text


def test_docx_header_only_text_is_included():
    # 姓名电话只在页眉的简历以前会被判“未识别到可用文字”。
    text = docx_to_markdown(_sample("header-only.docx"))
    assert "张三" in text
    assert "13800000000" in text


def test_docx_stdlib_built_sdt_and_nested_table():
    sdt_para = (
        f'<w:sdt xmlns:w="{W_NS}"><w:sdtPr><w:id w:val="1"/></w:sdtPr>'
        f"<w:sdtContent>{_para('内容控件里的经历：三年后端开发')}</w:sdtContent></w:sdt>"
    )
    inner_table = (
        f'<w:tbl xmlns:w="{W_NS}"><w:tr><w:tc>{_para("内层文字")}</w:tc></w:tr></w:tbl>'
    )
    outer_table = (
        f'<w:tbl xmlns:w="{W_NS}"><w:tr><w:tc>'
        f"{_para('外层文字')}{inner_table}</w:tc></w:tr></w:tbl>"
    )
    text = docx_to_markdown(_build_docx(_doc_xml(sdt_para, outer_table)))
    assert "内容控件里的经历" in text
    assert "外层文字" in text
    assert "内层文字" in text


def test_docx_broken_variants_have_distinct_reasons():
    with pytest.raises(ResumeUploadError, match="缺少正文内容"):
        docx_to_markdown(_sample("no-document-xml.docx"))
    with pytest.raises(ResumeUploadError, match="正文无法解析"):
        docx_to_markdown(_sample("corrupt-xml.docx"))
    with pytest.raises(ResumeUploadError, match="无效或已损坏"):
        docx_to_markdown(_sample("not-zip.docx"))


# ── .pdf: real fixtures ───────────────────────────────────────────────


def test_pdf_chinese_word_pdf_reads_text():
    text = pdf_to_markdown(_sample("word-chinese.pdf"))
    assert "张三" in text
    assert "工作经历" in text
    assert "Python 工程师" in text


def test_pdf_reportlab_ascii_reads_text():
    assert "Zhang San" in pdf_to_markdown(_sample("reportlab.pdf"))


def test_pdf_empty_password_encrypted_opens():
    # 空密码加密的 PDF 在阅读器里能直接打开，不该判“已加密”。
    assert "Zhang San" in pdf_to_markdown(_sample("encrypted-empty-pw.pdf"))


def test_pdf_password_encrypted_fails_with_clear_reason():
    with pytest.raises(ResumeUploadError, match="已加密"):
        pdf_to_markdown(_sample("encrypted.pdf"))


def test_pdf_zero_pages_is_not_called_scanned():
    with pytest.raises(ResumeUploadError, match="没有页面内容"):
        pdf_to_markdown(_sample("zero-page.pdf"))


def test_pdf_blank_page_keeps_scanned_hint():
    with pytest.raises(ResumeUploadError, match="扫描版或无文字层"):
        pdf_to_markdown(_sample("blank.pdf"))


def test_pdf_truncated_and_garbage_fail_as_damaged():
    with pytest.raises(ResumeUploadError, match="无效或已损坏"):
        pdf_to_markdown(_sample("truncated.pdf"))
    with pytest.raises(ResumeUploadError, match="无效或已损坏"):
        pdf_to_markdown(_sample("garbage.pdf"))


def test_pdf_one_bad_page_keeps_good_pages():
    from unittest.mock import MagicMock

    good = MagicMock()
    good.extract_text.return_value = "Page one good text"
    bad = MagicMock()
    bad.extract_text.side_effect = RuntimeError("bad cmap")
    fake_reader = MagicMock()
    fake_reader.is_encrypted = False
    fake_reader.pages = [good, bad]
    with patch("bosshunter.web.resume_upload.PdfReader", return_value=fake_reader):
        text = pdf_to_markdown(b"fake-pdf-bytes")
    assert "Page one good text" in text


def test_pdf_all_pages_bad_fails_instead_of_500():
    # 以前 extract_text 抛非预期异常会直接穿透，接口返回 500 原文。
    from unittest.mock import MagicMock

    bad = MagicMock()
    bad.extract_text.side_effect = RuntimeError("bad cmap")
    fake_reader = MagicMock()
    fake_reader.is_encrypted = False
    fake_reader.pages = [bad, bad]
    with patch("bosshunter.web.resume_upload.PdfReader", return_value=fake_reader):
        with pytest.raises(ResumeUploadError, match="没读出文字"):
            pdf_to_markdown(b"fake-pdf-bytes")


# ── upload entry: formats, encodings, suffixes ────────────────────────


def test_upload_docx_pdf_md_txt_all_succeed_with_real_samples():
    for name in ("normal.docx", "word-chinese.pdf", "reportlab.pdf", "normal.md"):
        out_name, out = prepare_resume_content(name, _sample(name))
        assert out_name.endswith(".md")
        assert len(out.decode("utf-8").strip()) > 10

    out_name, out = prepare_resume_content("简历.txt", "# 张三\n电话：13800000000\n".encode("utf-8"))
    assert out_name == "简历.md"
    assert "张三" in out.decode("utf-8")


def test_upload_txt_gbk_fallback_and_bad_bytes():
    gbk_bytes = "张三 Python 工程师".encode("gbk")
    _, out = prepare_resume_content("简历.txt", gbk_bytes)
    assert "张三" in out.decode("utf-8")
    with pytest.raises(ResumeUploadError, match="UTF-8"):
        prepare_resume_content("简历.txt", b"\xff\xfe\x00")


def test_upload_md_gbk_fallback():
    _, out = prepare_resume_content("简历.md", "# 张三\n".encode("gbk"))
    assert "张三" in out.decode("utf-8")


def test_upload_uppercase_suffix_ok():
    _, out = prepare_resume_content("RESUME.PDF", _sample("reportlab.pdf"))
    assert "Zhang San" in out.decode("utf-8")
    _, out = prepare_resume_content("RESUME.DOCX", _sample("normal.docx"))
    assert "张三" in out.decode("utf-8")


def test_upload_legacy_doc_gives_actionable_tip():
    with pytest.raises(ResumeUploadError, match="另存为 .docx"):
        prepare_resume_content("旧版简历.doc", _sample("old.doc"))
    with pytest.raises(ResumeUploadError, match="另存为 .docx"):
        prepare_resume_content("简历.rtf", b"{\\rtf1 hello}")
    with pytest.raises(ResumeUploadError, match="仅支持"):
        prepare_resume_content("简历.xyz", b"hello")


# ── config pointing at .pdf/.docx (manual config edit) ─────────────────


def test_resolve_configured_files_maps_docx_to_md_stem(tmp_path):
    md_path, pdf_path = resolve_configured_resume_files(tmp_path / "resume.docx")
    assert md_path == tmp_path / "resume.md"
    assert pdf_path == tmp_path / "resume.pdf"


def test_ensure_resume_markdown_converts_docx_config(tmp_path):
    docx_path = tmp_path / "resume.docx"
    docx_path.write_bytes(_sample("normal.docx"))
    md_path = ensure_resume_markdown_file(docx_path)
    assert md_path == tmp_path / "resume.md"
    assert "张三" in md_path.read_text(encoding="utf-8")


def test_load_resume_info_reads_docx_config(tmp_path):
    docx_path = tmp_path / "resume.docx"
    docx_path.write_bytes(_sample("sdt.docx"))
    info = load_resume_info(docx_path)
    assert info is not None
    assert "工作经历" in info["content"]
    assert info["path"].endswith("resume.md")


def test_read_base_resume_text_dispatches_by_suffix(tmp_path):
    from bosshunter.ai.resume import _read_base_resume_text

    md_path = tmp_path / "resume.md"
    md_path.write_text("# 张三\n", encoding="utf-8")
    assert "张三" in _read_base_resume_text(md_path)

    pdf_path = tmp_path / "resume.pdf"
    pdf_path.write_bytes(_sample("word-chinese.pdf"))
    assert "张三" in _read_base_resume_text(pdf_path)

    docx_path = tmp_path / "resume.docx"
    docx_path.write_bytes(_sample("normal.docx"))
    assert "张三" in _read_base_resume_text(docx_path)

    binary_path = tmp_path / "resume.bin"
    binary_path.write_bytes(b"\x00\x01\x02\xff")
    with pytest.raises(ResumeUploadError, match="不是文字格式"):
        _read_base_resume_text(binary_path)
