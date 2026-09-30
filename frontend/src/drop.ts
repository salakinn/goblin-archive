const supported = new Set(['epub', 'pdf', 'mobi', 'azw3', 'fb2'])

type FileEntry = {
  isFile: boolean
  isDirectory: boolean
  file: (success: (file: File) => void, error?: (error: DOMException) => void) => void
  createReader?: () => { readEntries: (success: (entries: FileEntry[]) => void, error?: (error: DOMException) => void) => void }
}

function extension(file: File) {
  return file.name.split('.').pop()?.toLowerCase() || ''
}

export function compatibleBookFiles(files: Iterable<File>): File[] {
  return Array.from(files).filter(file => supported.has(extension(file)))
}

async function readDirectory(entry: FileEntry): Promise<FileEntry[]> {
  const reader = entry.createReader!()
  const result: FileEntry[] = []
  while (true) {
    const batch = await new Promise<FileEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
    if (!batch.length) return result
    result.push(...batch)
  }
}

async function filesFromEntry(entry: FileEntry): Promise<File[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => entry.file(resolve, reject))
    return supported.has(extension(file)) ? [file] : []
  }
  if (entry.isDirectory) {
    const nested = await readDirectory(entry)
    return (await Promise.all(nested.map(filesFromEntry))).flat()
  }
  return []
}

export async function filesFromDrop(dataTransfer: DataTransfer): Promise<File[]> {
  const entries = Array.from(dataTransfer.items)
    .map(item => (item as unknown as { webkitGetAsEntry?: () => FileEntry | null }).webkitGetAsEntry?.() || null)
    .filter((entry): entry is FileEntry => entry !== null)
  if (entries.length) return (await Promise.all(entries.map(filesFromEntry))).flat()
  return compatibleBookFiles(dataTransfer.files)
}
