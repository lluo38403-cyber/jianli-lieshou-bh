"""Resume upload filename handling and document-to-Markdown conversion."""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from bosshunter.lazy_module import lazy_attr
from bosshunter.web.resume_text import sanitize_resume_text

# v30 启动提速：pypdf 冷导入约 0.6 秒，启动首屏用不上；PdfReader
# 只在第一次真正解析 PDF 时才导入。代理是真实的模块全局名，
# 函数里直接用、测试照常 patch（见 bosshunter.lazy_module）。
PdfReader = lazy_attr("pypdf", "PdfReader")

MAX_DOCX_XML_SIZE = 20 * 1024 * 1024
SUPPORTED_RESUME_EXTENSIONS = {".md", ".docx", ".pdf", ".txt"}
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_WORD_NAMESPACE}}}"
# 段落可能藏在这些包装元素里（内容控件、智能标记等）；只看 body 直接子节点会丢字。
_WRAPPER_TAGS = {
	f"{_W}sdt",
	f"{_W}smartTag",
	f"{_W}customXml",
	f"{_W}ins",
	f"{_W}moveFrom",
	f"{_W}moveTo",
}
_PAGE_NUMBER_RE = re.compile(r"^\s*(?:第\s*\d+\s*页|\d+\s*/\s*\d+|\d+)\s*$")
# 格式本身不支持时，给一句能照着做的提示，而不是只说“不支持”。
_UNSUPPORTED_FORMAT_TIPS = {
	".doc": "老版 .doc 打不开，请用 Word 或 WPS 打开后另存为 .docx 再上传",
	".wps": "老版 .wps 打不开，请用 WPS 打开后另存为 .docx 再上传",
	".rtf": ".rtf 格式暂不支持，请另存为 .docx 或 .pdf 再上传",
	".odt": ".odt 格式暂不支持，请另存为 .docx 或 .pdf 再上传",
}


class ResumeUploadError(ValueError):
	"""An invalid or unsupported resume upload."""


def safe_resume_filename(raw_filename: str) -> str:
	"""Return a filesystem-safe filename while preserving Unicode characters."""
	if not raw_filename:
		raise ResumeUploadError("文件名不能为空")

	name = unicodedata.normalize("NFC", str(raw_filename))
	name = name.replace("\\", "/").rsplit("/", 1)[-1]
	name = "".join(char for char in name if char >= " " and char != "\x7f")
	name = name.strip().strip(".")
	if not name:
		raise ResumeUploadError("文件名不能为空")

	suffix = Path(name).suffix.lower()
	if suffix not in SUPPORTED_RESUME_EXTENSIONS:
		raise ResumeUploadError(
			_UNSUPPORTED_FORMAT_TIPS.get(suffix, "仅支持 .md、.docx、.pdf 或 .txt 格式")
		)

	stem = name[: -len(Path(name).suffix)].strip().strip(".")
	if not stem:
		stem = "resume"

	# Keep the final name comfortably below common 255-byte filesystem limits.
	return f"{_truncate_utf8(stem, 200 - len(suffix.encode('utf-8')))}{suffix}"


def _decode_plain_text(content: bytes, *, kind: str) -> str:
	"""Decode a Markdown/text upload, accepting UTF-8 or GBK.

	中文用户手头的 .md/.txt 常是 GBK 编码；只认 UTF-8 会把这类文件误判为无效。
	"""
	try:
		return content.decode("utf-8")
	except UnicodeDecodeError:
		pass
	try:
		return content.decode("gbk")
	except UnicodeDecodeError as exc:
		raise ResumeUploadError(f"{kind}文件编码无法识别，请另存为 UTF-8 编码再上传") from exc


def prepare_resume_content(filename: str, content: bytes) -> tuple[str, bytes]:
	"""Return the Markdown storage filename and UTF-8 content for an upload."""
	safe_name = safe_resume_filename(filename)
	suffix = Path(safe_name).suffix.lower()
	if suffix in {".md", ".txt"}:
		kind = "Markdown" if suffix == ".md" else "文本"
		text = sanitize_resume_text(_decode_plain_text(content, kind=kind))
		# H-4：空白 .md/.txt 之前直接存空文件，还会覆盖好简历并删掉原
		# PDF 伴档。与 PDF（无文字即报错）/docx（无文字即报错）分支对齐：
		# sanitize 后只剩空白/NUL 即拒绝，落盘之前就拦住。
		if not text.strip():
			raise ResumeUploadError("简历文件没有内容，请换一份有内容的简历再上传")
		output_name = safe_name if suffix == ".md" else f"{Path(safe_name).stem}.md"
		return output_name, text.encode("utf-8")

	markdown = docx_to_markdown(content) if suffix == ".docx" else pdf_to_markdown(content)
	output_name = f"{Path(safe_name).stem}.md"
	return output_name, sanitize_resume_text(markdown).encode("utf-8")


def _is_pdf_parse_error(exc: BaseException) -> bool:
	"""True when ``exc`` is a real PDF parse failure (M-37).

	Only PdfReadError (covers PdfStreamError / EmptyFileError /
	FileNotDecryptedError) and OSError count: a truncated, garbage or
	otherwise unreadable file. Program errors (NameError, ImportError,
	MemoryError, ...) are NOT parse failures and must propagate — the
	old bare ``except Exception`` turned them into a misleading
	"PDF 文件无效或已损坏" (v30 构建中间态 pypdf 懒导入写坏时，
	NameError 就是这样被吞掉、6 项 PDF 测试误报文件损坏）。
	PdfReadError is imported lazily inside (a top-level import would
	defeat the v30 lazy pypdf load); if that import itself fails,
	return False so the original error propagates.
	"""
	if isinstance(exc, OSError):
		return True
	try:
		from pypdf.errors import PdfReadError
	except Exception:
		return False
	return isinstance(exc, PdfReadError)


def pdf_to_markdown(content: bytes) -> str:
	"""Extract text-layer content from a PDF resume as Markdown-like text."""
	try:
		reader = PdfReader(BytesIO(content))
	except Exception as exc:
		if not _is_pdf_parse_error(exc):
			raise
		raise ResumeUploadError("PDF 文件无效或已损坏") from exc

	if reader.is_encrypted:
		# 空密码加密的 PDF 在阅读器里能直接打开；先试空密码，解不开才是真加密。
		try:
			decrypted = reader.decrypt("")
		except Exception:
			decrypted = 0
		if not decrypted:
			raise ResumeUploadError("PDF 已加密，请上传未加密的简历")

	try:
		pages = list(reader.pages)
	except Exception as exc:
		# M-37：同上，只收真正的解析失败；程序错误放行。
		if not _is_pdf_parse_error(exc):
			raise
		raise ResumeUploadError("PDF 文件无效或已损坏") from exc
	if not pages:
		raise ResumeUploadError("这个 PDF 没有页面内容，请换一份有内容的简历再上传")

	# 逐页提取：一页的字体/内容流坏了不该毁掉整份简历，只跳过坏页。
	texts: list[str] = []
	failed_pages = 0
	for page in pages:
		try:
			text = page.extract_text() or ""
		except Exception:
			failed_pages += 1
			continue
		if text.strip():
			texts.append(text.strip())

	text = "\n\n".join(texts).strip()
	if not text:
		if failed_pages:
			raise ResumeUploadError("这个 PDF 每一页都没读出文字，文件可能已损坏，请换一份能正常打开的 PDF 再上传")
		raise ResumeUploadError("未能从 PDF 提取文字；扫描版或无文字层简历请先 OCR 后再上传")
	return f"{text}\n"


def docx_to_markdown(content: bytes) -> str:
	"""Extract readable Markdown-like text from a DOCX document."""
	try:
		archive = ZipFile(BytesIO(content))
	except (BadZipFile, OSError, RuntimeError) as exc:
		raise ResumeUploadError("Word 文件无效或已损坏") from exc

	with archive:
		try:
			document_info = archive.getinfo("word/document.xml")
		except KeyError as exc:
			raise ResumeUploadError("Word 文件无效：缺少正文内容") from exc
		if document_info.file_size > MAX_DOCX_XML_SIZE:
			raise ResumeUploadError("Word 文件内容过大")
		try:
			document_xml = archive.read(document_info)
		except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
			raise ResumeUploadError("Word 文件无效或已损坏") from exc
		header_footer_lines = _docx_header_footer_lines(archive)

	try:
		root = ElementTree.fromstring(document_xml)
	except ElementTree.ParseError as exc:
		raise ResumeUploadError("Word 文件正文无法解析") from exc

	body = root.find(f".//{_W}body")
	if body is None:
		raise ResumeUploadError("Word 文件无效：缺少正文内容")

	lines = [line for line in _docx_block_lines(body) if line]
	# 页眉/页脚常放姓名电话，放正文前面；正文已有的行不再重复，纯页码不要。
	seen = set(lines)
	head_lines = [
		line
		for line in header_footer_lines
		if line not in seen and not _PAGE_NUMBER_RE.fullmatch(line)
	]
	markdown = "\n\n".join([*head_lines, *lines]).strip()
	if not markdown:
		raise ResumeUploadError("Word 文件中未识别到可用文字")
	return f"{markdown}\n"


def _docx_block_lines(element: ElementTree.Element, *, plain: bool = False):
	"""Yield Markdown lines for an element, recursing into wrappers and tables.

	Word 模板常用内容控件（w:sdt）包裹段落，旧实现只看 body 的直接子节点，
	会把这类段落整个丢掉；嵌套表格的内层文字也一样。plain=True 时段落只出
	纯文本（表格单元格用，不带标题/列表标记）。
	"""
	for child in element:
		if child.tag == f"{_W}p":
			line = _element_text(child) if plain else _paragraph_to_markdown(child)
			if line:
				yield line
		elif child.tag == f"{_W}tbl":
			yield from _table_to_markdown(child)
		elif child.tag in _WRAPPER_TAGS:
			content = child.find(f"./{_W}sdtContent")
			yield from _docx_block_lines(content if content is not None else child, plain=plain)


def _docx_header_footer_lines(archive: ZipFile) -> list[str]:
	"""Extract plain lines from word/header*.xml and word/footer*.xml, if any."""
	lines: list[str] = []
	for name in sorted(archive.namelist()):
		if not name.endswith(".xml"):
			continue
		if not (name.startswith("word/header") or name.startswith("word/footer")):
			continue
		try:
			root = ElementTree.fromstring(archive.read(name))
		except (ElementTree.ParseError, BadZipFile, KeyError, OSError, RuntimeError):
			continue
		lines.extend(line for line in _docx_block_lines(root, plain=True) if line)
	return lines


def _paragraph_to_markdown(paragraph: ElementTree.Element) -> str:
	text = _element_text(paragraph)
	if not text:
		return ""

	style_node = paragraph.find(f"./{_W}pPr/{_W}pStyle")
	style = style_node.get(f"{_W}val", "") if style_node is not None else ""
	heading_match = re.fullmatch(r"(?:Heading|标题)\s*([1-6])", style, flags=re.IGNORECASE)
	if style.lower() in {"title", "标题"}:
		return f"# {text}"
	if heading_match:
		return f"{'#' * int(heading_match.group(1))} {text}"
	if paragraph.find(f"./{_W}pPr/{_W}numPr") is not None:
		return f"- {text}"
	return text


def _table_to_markdown(table: ElementTree.Element) -> list[str]:
	rows: list[str] = []
	for row in table.findall(f"./{_W}tr"):
		cells = []
		for cell in row.findall(f"./{_W}tc"):
			cell_text = " / ".join(
				line for line in _docx_block_lines(cell, plain=True) if line
			)
			if cell_text:
				cells.append(cell_text)
		if cells:
			rows.append(" | ".join(cells))
	return rows


def _element_text(element: ElementTree.Element) -> str:
	parts: list[str] = []
	for node in element.iter():
		if node.tag == f"{_W}t" and node.text:
			parts.append(node.text)
		elif node.tag == f"{_W}tab":
			parts.append(" ")
		elif node.tag in {f"{_W}br", f"{_W}cr"}:
			parts.append("\n")
	return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _truncate_utf8(value: str, max_bytes: int) -> str:
	encoded = value.encode("utf-8")
	if len(encoded) <= max_bytes:
		return value
	return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
