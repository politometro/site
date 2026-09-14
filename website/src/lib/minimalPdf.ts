// Escritor PDF minimalista: incorpora imagens JPEG (DCTDecode) como páginas.
// Cada página tem a dimensão exata da imagem — é o que permite gerar
// "imagens" de conversa com alturas diferentes dentro de um único PDF.

function base64ToUint8Array(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

interface JpegSize {
  width: number;
  height: number;
}

function jpegSize(bytes: Uint8Array): JpegSize | null {
  // Parse mínimo do SOF (baseline e progressivo).
  let offset = 2;
  while (offset + 9 < bytes.length) {
    if (bytes[offset] !== 0xff) return null;
    const marker = bytes[offset + 1];
    const length = (bytes[offset + 2] << 8) | bytes[offset + 3];
    if (
      marker >= 0xc0 &&
      marker <= 0xcf &&
      marker !== 0xc4 &&
      marker !== 0xc8 &&
      marker !== 0xcc
    ) {
      return {
        height: (bytes[offset + 5] << 8) | bytes[offset + 6],
        width: (bytes[offset + 7] << 8) | bytes[offset + 8],
      };
    }
    offset += 2 + length;
  }
  return null;
}

export interface PdfPageInput {
  /** dataURL `data:image/jpeg;base64,...` da imagem da página */
  jpegDataUrl: string;
  widthPx: number;
  heightPx: number;
}

export function imagesToPdfBlob(pages: PdfPageInput[]): Blob {
  if (pages.length === 0) throw new Error("Sem páginas para o PDF.");

  const chunks: Uint8Array[] = [];
  let length = 0;
  const offsets: number[] = [];

  const push = (data: Uint8Array | string) => {
    const bytes =
      typeof data === "string"
        ? new TextEncoder().encode(data)
        : data;
    chunks.push(bytes);
    length += bytes.length;
  };
  const beginObj = (id: number) => {
    offsets[id] = length;
    push(`${id} 0 obj\n`);
  };

  const decoded = pages.map((page) => {
    const base64 = page.jpegDataUrl.split(",")[1] ?? "";
    return {
      bytes: base64ToUint8Array(base64),
      widthPx: page.widthPx,
      heightPx: page.heightPx,
    };
  });

  const objectCount = 2 + decoded.length * 3;

  push("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n");

  beginObj(1);
  push("<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
  beginObj(2);
  const kids = decoded
    .map((_, index) => `${3 + index * 3} 0 R`)
    .join(" ");
  push(`<< /Type /Pages /Kids [ ${kids} ] /Count ${decoded.length} >>\nendobj\n`);

  decoded.forEach((page, index) => {
    const pageId = 3 + index * 3;
    const imageId = pageId + 1;
    const contentId = pageId + 2;
    const widthPt = page.widthPx;
    const heightPt = page.heightPx;

    beginObj(pageId);
    push(
      `<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 ${widthPt} ${heightPt} ] ` +
        `/Resources << /XObject << /Im${index} ${imageId} 0 R >> /ProcSet [ /PDF /ImageC ] >> ` +
        `/Contents ${contentId} 0 R >>\nendobj\n`,
    );

    const size = jpegSize(page.bytes) ?? {
      width: page.widthPx,
      height: page.heightPx,
    };
    beginObj(imageId);
    push(
      `<< /Type /XObject /Subtype /Image /Width ${size.width} /Height ${size.height} ` +
        `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode ` +
        `/Length ${page.bytes.length} >>\nstream\n`,
    );
    push(page.bytes);
    push("\nendstream\nendobj\n");

    const content = `q ${widthPt} 0 0 ${heightPt} 0 0 cm /Im${index} Do Q\n`;
    beginObj(contentId);
    push(`<< /Length ${content.length} >>\nstream\n${content}endstream\nendobj\n`);
  });

  const xrefOffset = length;
  let xref = `xref\n0 ${objectCount + 1}\n0000000000 65535 f \n`;
  for (let id = 1; id <= objectCount; id += 1) {
    const offset = offsets[id] ?? 0;
    xref += `${String(offset).padStart(10, "0")} 00000 n \n`;
  }
  push(xref);
  push(
    `trailer\n<< /Size ${objectCount + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`,
  );

  return new Blob(chunks as BlobPart[], { type: "application/pdf" });
}
