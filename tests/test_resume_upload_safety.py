import pytest

from bosshunter.web.resume_upload import ResumeUploadError, prepare_resume_content


def test_markdown_upload_rejects_non_utf8_bytes():
    with pytest.raises(ResumeUploadError, match="UTF-8"):
        prepare_resume_content("resume.md", b"\xff\xfe\x00")


def test_markdown_upload_rejects_empty_file():
    # H-4：0 字节 .md 之前返回成功并覆盖好简历。
    with pytest.raises(ResumeUploadError, match="没有内容"):
        prepare_resume_content("empty.md", b"")


def test_text_upload_rejects_whitespace_only_file():
    # H-4：仅空格换行的 .txt 之前成功存为 .md 空文件。
    with pytest.raises(ResumeUploadError, match="没有内容"):
        prepare_resume_content("blank.txt", "  \n\t\n".encode("utf-8"))


def test_markdown_upload_rejects_nul_only_file():
    # H-4：仅 NUL 的文件 sanitize 后为空，同样拒绝。
    with pytest.raises(ResumeUploadError, match="没有内容"):
        prepare_resume_content("nul.md", b"\x00\x00")


def test_markdown_upload_keeps_valid_utf8_content():
    content = "# 真实简历\n".encode("utf-8")
    filename, normalized = prepare_resume_content("真实简历.md", content)

    assert filename == "真实简历.md"
    assert normalized == content
