import { describe, expect, it } from 'vitest'
import {
  afterResumeLoadFailure,
  afterSuccessfulResumeLoad,
  afterSuccessfulResumeUpload,
  emptyResumePanelMessages,
  isPdfResumeFile,
  isSupportedResumeFile,
  PDF_UPLOAD_SUCCESS_MESSAGE,
  UNSUPPORTED_RESUME_MESSAGE,
} from './resumeDisplay'

describe('isPdfResumeFile (v28 PDF 导入)', () => {
  it('accepts lowercase .pdf', () => {
    expect(isPdfResumeFile('resume.pdf')).toBe(true)
  })

  it('accepts uppercase .PDF', () => {
    expect(isPdfResumeFile('RESUME.PDF')).toBe(true)
  })

  it('rejects other extensions', () => {
    expect(isPdfResumeFile('resume.docx')).toBe(false)
    expect(isPdfResumeFile('resume.md')).toBe(false)
    expect(isPdfResumeFile('resume.txt')).toBe(false)
    expect(isPdfResumeFile('resume.pdf.exe')).toBe(false)
    expect(isPdfResumeFile('resume')).toBe(false)
    expect(isPdfResumeFile('')).toBe(false)
    expect(isPdfResumeFile(null)).toBe(false)
  })
})

describe('isSupportedResumeFile (v28：与后端 SUPPORTED_RESUME_EXTENSIONS 一致)', () => {
  it('accepts .md/.docx/.pdf/.txt, case-insensitive', () => {
    expect(isSupportedResumeFile('resume.pdf')).toBe(true)
    expect(isSupportedResumeFile('RESUME.PDF')).toBe(true)
    expect(isSupportedResumeFile('resume.docx')).toBe(true)
    expect(isSupportedResumeFile('Resume.DOCX')).toBe(true)
    expect(isSupportedResumeFile('resume.md')).toBe(true)
    expect(isSupportedResumeFile('resume.txt')).toBe(true)
  })

  it('rejects unsupported extensions', () => {
    expect(isSupportedResumeFile('resume.exe')).toBe(false)
    expect(isSupportedResumeFile('resume.pdf.exe')).toBe(false)
    expect(isSupportedResumeFile('resume')).toBe(false)
    expect(isSupportedResumeFile('')).toBe(false)
    expect(isSupportedResumeFile(null)).toBe(false)
  })

  it('uses the exact rejection message', () => {
    expect(UNSUPPORTED_RESUME_MESSAGE).toBe('仅支持 .md、.docx、.pdf 或 .txt 格式')
  })
})

describe('upload success message (v28 PDF 导入)', () => {
  it('sets the success message and clears the load error', () => {
    const failed = afterResumeLoadFailure(emptyResumePanelMessages(), '简历没传上去')
    const done = afterSuccessfulResumeUpload(failed, PDF_UPLOAD_SUCCESS_MESSAGE)
    expect(done.success).toBe('PDF 简历上传成功')
    expect(done.loadError).toBe('')
  })

  it('keeps the success message across a later successful GET', () => {
    const done = afterSuccessfulResumeUpload(emptyResumePanelMessages(), PDF_UPLOAD_SUCCESS_MESSAGE)
    expect(afterSuccessfulResumeLoad(done).success).toBe('PDF 简历上传成功')
  })

  it('clears the success message on the next failure', () => {
    const done = afterSuccessfulResumeUpload(emptyResumePanelMessages(), PDF_UPLOAD_SUCCESS_MESSAGE)
    expect(afterResumeLoadFailure(done, '简历没传上去').success).toBe('')
  })
})
