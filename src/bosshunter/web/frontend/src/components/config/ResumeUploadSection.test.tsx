import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ResumeUploadSection } from './ResumeUploadSection'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function mockFetch(uploadResponse: { ok: boolean; body: unknown }) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/api/resume/upload')) {
      return { ok: uploadResponse.ok, json: async () => uploadResponse.body }
    }
    // Initial GET /api/resume: no resume configured yet.
    return { ok: true, json: async () => null }
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function pdfFile(name = 'resume.pdf') {
  return new File(['%PDF-1.4 fake'], name, { type: 'application/pdf' })
}

function renderSection(fetchMock: ReturnType<typeof vi.fn>) {
  const updateConfig = vi.fn()
  const utils = render(<ResumeUploadSection currentResumePath="" updateConfig={updateConfig} />)
  return { ...utils, fetchMock, updateConfig }
}

it('rejects an unsupported file picked in the file input', async () => {
  const fetchMock = mockFetch({ ok: true, body: { success: true } })
  const { container } = renderSection(fetchMock)
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['x'], 'resume.exe')] } })
  expect(await screen.findByText('仅支持 .md、.docx、.pdf 或 .txt 格式')).toBeTruthy()
  // Rejected client-side: no upload request was sent (only the initial GET).
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/upload'))).toHaveLength(0)
})

it('rejects a 0-byte file picked in the file input without sending it (H-4)', async () => {
  const fetchMock = mockFetch({ ok: true, body: { success: true } })
  const { container } = renderSection(fetchMock)
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File([], 'empty.md', { type: 'text/markdown' })] } })
  expect(await screen.findByText('这个文件是空的，没有内容，换一份有内容的简历再上传')).toBeTruthy()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/upload'))).toHaveLength(0)
})

it('accepts a .docx file and shows the generic success message', async () => {
  const fetchMock = mockFetch({
    ok: true,
    body: { success: true, filename: 'resume.md', size: 12, path: '/tmp/resume.md', content: 'hello' },
  })
  const { container } = renderSection(fetchMock)
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['x'], 'resume.docx')] } })
  expect(await screen.findByText('简历上传成功')).toBeTruthy()
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/upload'))).toBe(true)
})

it('accepts an uppercase .PDF file and shows the success message', async () => {
  const fetchMock = mockFetch({
    ok: true,
    body: { success: true, filename: 'resume.md', size: 12, path: '/tmp/resume.md', content: 'hello' },
  })
  const { container } = renderSection(fetchMock)
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [pdfFile('RESUME.PDF')] } })
  expect(await screen.findByText('PDF 简历上传成功')).toBeTruthy()
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/upload'))).toBe(true)
})

it('rejects an unsupported file dropped onto the zone', async () => {
  const fetchMock = mockFetch({ ok: true, body: { success: true } })
  renderSection(fetchMock)
  const zone = screen.getByText('把简历拖进来，或者点一下选文件 (.md、.docx、.pdf、.txt)').closest('label') as HTMLElement
  fireEvent.drop(zone, { dataTransfer: { files: [new File(['x'], 'notes.exe')] } })
  expect(await screen.findByText('仅支持 .md、.docx、.pdf 或 .txt 格式')).toBeTruthy()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/upload'))).toHaveLength(0)
})
