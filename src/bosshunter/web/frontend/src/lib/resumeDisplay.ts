/** Pure helpers for resume dual-preview UI. No I/O, no React state. */

export type ResumeInfo = {
  filename: string
  size: number
  size_label?: string
  uploaded_at?: string
  cache_buster?: string
  content?: string
  path: string
  has_original_pdf?: boolean
  original_filename?: string
  original_preview_url?: string
}

/** Flip a visibility flag. */
export function nextResumeContentVisible(isVisible: boolean): boolean {
  return !isVisible
}

/** Label for the converted Markdown preview control. */
export function resumeMarkdownToggleLabel(isVisible: boolean): string {
  return isVisible ? '隐藏转换后的 MD' : '查看转换后的 MD'
}

/** Label for the original PDF preview control. */
export function resumePdfToggleLabel(isVisible: boolean): string {
  return isVisible ? '隐藏原 PDF' : '查看原 PDF'
}

/** Prefer server-provided size_label; fall back to a local KB label. */
export function resumeSizeLabel(info: Pick<ResumeInfo, 'size' | 'size_label'>): string {
  if (info.size_label && info.size_label.trim()) {
    return info.size_label
  }
  return `${(info.size / 1024).toFixed(1)} KB`
}

/** True when the API returned Markdown text we can preview. */
export function hasResumePreviewContent(info: Pick<ResumeInfo, 'content'> | null | undefined): boolean {
  return Boolean(info?.content && info.content.trim())
}

/** True when a companion original PDF is available for preview. */
export function hasOriginalPdf(info: Pick<ResumeInfo, 'has_original_pdf'> | null | undefined): boolean {
  return Boolean(info?.has_original_pdf)
}

/** Build the PDF iframe URL with a second-precision cache buster when available. */
export function resumeOriginalPreviewSrc(
  info: Pick<ResumeInfo, 'original_preview_url' | 'cache_buster' | 'uploaded_at'> | null | undefined,
): string {
  const base = info?.original_preview_url || '/api/resume/original'
  const token = info?.cache_buster || info?.uploaded_at
  if (token) {
    return `${base}?t=${encodeURIComponent(String(token))}`
  }
  return base
}

/** Tip when a file is dropped outside the resume upload zone. Pure. */
export function resumeDropOutsideTip(): string {
  return '请将文件拖放到简历上传区域'
}

/** v28 PDF 导入：判断是否为 PDF（大小写不敏感），用于成功提示文案。Pure. */
export function isPdfResumeFile(filename: string | null | undefined): boolean {
  return String(filename || '').trim().toLowerCase().endsWith('.pdf')
}

/** 客户端接受的简历格式，与后端 SUPPORTED_RESUME_EXTENSIONS 一致（大小写不敏感）。Pure. */
const SUPPORTED_RESUME_EXTENSIONS = new Set(['.md', '.docx', '.pdf', '.txt'])

export function isSupportedResumeFile(filename: string | null | undefined): boolean {
  const name = String(filename || '').trim().toLowerCase()
  const dot = name.lastIndexOf('.')
  if (dot < 0) return false
  return SUPPORTED_RESUME_EXTENSIONS.has(name.slice(dot))
}

/** 不支持的格式被客户端校验挡下时的提示，与后端口径一致。Pure. */
export const UNSUPPORTED_RESUME_MESSAGE = '仅支持 .md、.docx、.pdf 或 .txt 格式'

/** PDF 上传成功后的提示。Pure. */
export const PDF_UPLOAD_SUCCESS_MESSAGE = 'PDF 简历上传成功'

/** 非 PDF 简历上传成功后的提示。Pure. */
export const RESUME_UPLOAD_SUCCESS_MESSAGE = '简历上传成功'

/** Three message channels: load/upload error, outside-drop tip, upload success. GET must not wipe the tip. */
export type ResumePanelMessages = {
  loadError: string
  dropOutsideTip: string
  success: string
}

export function emptyResumePanelMessages(): ResumePanelMessages {
  return { loadError: '', dropOutsideTip: '', success: '' }
}

/** Prefer load/upload errors; fall back to the outside-drop tip. Pure. */
export function resumePanelVisibleMessage(messages: ResumePanelMessages): string {
  const loadError = String(messages.loadError || '').trim()
  if (loadError) return loadError
  return String(messages.dropOutsideTip || '').trim()
}

/** Successful GET /api/resume clears load errors but keeps an outside-drop tip and any upload success. Pure. */
export function afterSuccessfulResumeLoad(messages: ResumePanelMessages): ResumePanelMessages {
  return { loadError: '', dropOutsideTip: messages.dropOutsideTip, success: messages.success }
}

/** Failed GET /api/resume sets the load error without clearing the tip. Pure. */
export function afterResumeLoadFailure(
  messages: ResumePanelMessages,
  error: string,
): ResumePanelMessages {
  return { loadError: error, dropOutsideTip: messages.dropOutsideTip, success: '' }
}

/** Successful upload sets the success message and clears the load error. Pure. */
export function afterSuccessfulResumeUpload(
  messages: ResumePanelMessages,
  success: string,
): ResumePanelMessages {
  return { loadError: '', dropOutsideTip: messages.dropOutsideTip, success }
}

/** File dropped outside the upload zone. Pure. */
export function afterDropOutside(messages: ResumePanelMessages, tip: string): ResumePanelMessages {
  return { ...messages, dropOutsideTip: tip }
}

/** Starting an upload/replace attempt clears both channels. Pure. */
export function afterUploadAttempt(messages: ResumePanelMessages): ResumePanelMessages {
  return emptyResumePanelMessages()
}

/** Parse /api/resume failures into a user-visible message. Pure. */
export function resumeLoadErrorMessage(
  statusOk: boolean,
  data: { filename?: string; error?: string } | null | undefined,
): string {
  if (!statusOk) {
    return (data && data.error) || '读取简历失败'
  }
  if (data && data.error) {
    return data.error
  }
  return ''
}

/** True when GET /api/resume path must be written into local config state. Pure. */
export function shouldSyncResumePath(
  currentPath: string | null | undefined,
  serverPath: string | null | undefined,
): boolean {
  const current = String(currentPath || '').trim()
  const server = String(serverPath || '').trim()
  if (!server) return false
  return current !== server
}
