// Gera imagens da conversa, empacotadas num PDF.
//
// Regras do desenho (pedidas expressamente):
//  - cada imagem/página tem altura dinâmica: cresce até ao máximo definido,
//    nunca fica com espaços vazios enormes;
//  - uma mensagem nunca é cortada a meio da linha: se não couber na página,
//    a página fecha e a mensagem continua na seguinte, partindo-a apenas em
//    fronteiras de linha.

import { imagesToPdfBlob } from "./minimalPdf";

export interface ShareMessage {
  role: "user" | "assistant";
  content: string;
}

const PAGE_WIDTH = 900;
const PAGE_MAX_HEIGHT = 2000;
const PAGE_MIN_HEIGHT = 460;
const PADDING = 30;
const GAP = 16;
const BUBBLE_PADDING_X = 18;
const BUBBLE_PADDING_TOP = 12;
const BUBBLE_PADDING_BOTTOM = 14;
const META_HEIGHT = 22;
const LINE_HEIGHT = 24;
const MAX_TEXT_WIDTH =
  PAGE_WIDTH - PADDING * 2 - BUBBLE_PADDING_X * 2 - 60 - 4;

const FONT =
  '15px "Segoe UI", "Helvetica Neue", Arial, "Noto Sans", sans-serif';
const META_FONT =
  '600 11px "Segoe UI", "Helvetica Neue", Arial, "Noto Sans", sans-serif';
const HEADER_FONT =
  '700 16px "Segoe UI", "Helvetica Neue", Arial, "Noto Sans", sans-serif';

const HEADER_TEXT = "Politómetro · Escrutínio IA";
const HEADER_HEIGHT = 34;

const COLORS = {
  page: "#f7f2e7",
  header: "#0a314a",
  headerText: "#fff8ec",
  userBubble: "#d9ebff",
  userBorder: "#9dc3ea",
  assistantBubble: "#ffffff",
  assistantBorder: "#d8d2c4",
  text: "#22303c",
  metaUser: "#2b6cb0",
  metaAssistant: "#0a314a",
};

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
): string[] {
  const lines: string[] = [];
  for (const rawLine of text.split("\n")) {
    if (rawLine.trim() === "") {
      lines.push("");
      continue;
    }
    let current = "";
    for (const word of rawLine.split(/\s+/)) {
      const candidate = current ? `${current} ${word}` : word;
      if (ctx.measureText(candidate).width <= maxWidth || !current) {
        current = candidate;
      } else {
        lines.push(current);
        current = word;
      }
    }
    if (current) lines.push(current);
  }
  return lines;
}

/** Markdown inline → texto simples preservando a estrutura de linhas. */
function plainText(content: string): string {
  return (content || "")
    .replace(/^###\s+/gm, "")
    .replace(/^##\s+/gm, "")
    .replace(/^#\s+/gm, "")
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/\[([^\]\n]+)\]\([^)\s]+\)/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

interface FullBlock {
  isHeader: boolean;
  role: "user" | "assistant";
  meta: string;
  lines: string[];
}

/** Altura de um bloco parcial (conjunto de linhas) fora da bolha. */
function sliceHeight(block: FullBlock, startLine: number, endLine: number): number {
  if (block.isHeader) return HEADER_HEIGHT;
  return (
    BUBBLE_PADDING_TOP +
    META_HEIGHT +
    (endLine - startLine) * LINE_HEIGHT +
    BUBBLE_PADDING_BOTTOM
  );
}

const CHROME_HEIGHT = BUBBLE_PADDING_TOP + META_HEIGHT + BUBBLE_PADDING_BOTTOM;

function buildBlocks(
  ctx: CanvasRenderingContext2D,
  messages: ShareMessage[],
): FullBlock[] {
  const blocks: FullBlock[] = [
    { isHeader: true, role: "assistant", meta: HEADER_TEXT, lines: [] },
  ];
  messages.forEach((message, index) => {
    const text = plainText(message.content);
    if (!text) return;
    ctx.font = FONT;
    blocks.push({
      isHeader: false,
      role: message.role,
      meta:
        message.role === "user"
          ? `Pergunta ${Math.floor(index / 2) + 1}`
          : "Politómetro",
      lines: wrapText(ctx, text, MAX_TEXT_WIDTH),
    });
  });
  return blocks;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

interface PageSpec {
  items: Array<{ block: FullBlock; startLine: number; endLine: number }>;
  contentHeight: number;
}

function drawSlice(
  ctx: CanvasRenderingContext2D,
  item: { block: FullBlock; startLine: number; endLine: number },
  y: number,
): number {
  const { block, startLine, endLine } = item;
  if (block.isHeader) {
    ctx.fillStyle = COLORS.header;
    ctx.fillRect(PADDING, y, PAGE_WIDTH - PADDING * 2, HEADER_HEIGHT);
    ctx.fillStyle = COLORS.headerText;
    ctx.font = HEADER_FONT;
    ctx.textBaseline = "middle";
    ctx.fillText(block.meta, PADDING + 16, y + HEADER_HEIGHT / 2);
    ctx.textBaseline = "alphabetic";
    return y + HEADER_HEIGHT + GAP;
  }

  const bubbleWidth = PAGE_WIDTH - PADDING * 2 - 60;
  const bubbleX = block.role === "user" ? PADDING + 60 : PADDING;
  const height = sliceHeight(block, startLine, endLine);
  const isUser = block.role === "user";

  ctx.fillStyle = isUser ? COLORS.userBubble : COLORS.assistantBubble;
  roundRect(ctx, bubbleX, y, bubbleWidth, height, 12);
  ctx.fill();
  ctx.strokeStyle = isUser ? COLORS.userBorder : COLORS.assistantBorder;
  ctx.lineWidth = 1;
  ctx.stroke();

  let cursorY = y + BUBBLE_PADDING_TOP + 11;
  ctx.font = META_FONT;
  ctx.fillStyle = isUser ? COLORS.metaUser : COLORS.metaAssistant;
  ctx.fillText(block.meta.toUpperCase(), bubbleX + BUBBLE_PADDING_X, cursorY);

  ctx.font = FONT;
  ctx.fillStyle = COLORS.text;
  for (let index = startLine; index < endLine; index += 1) {
    cursorY += LINE_HEIGHT;
    ctx.fillText(block.lines[index], bubbleX + BUBBLE_PADDING_X, cursorY - 4);
  }
  return y + height + GAP;
}

function renderPage(spec: PageSpec): HTMLCanvasElement {
  const height = Math.max(
    PAGE_MIN_HEIGHT,
    Math.min(spec.contentHeight + PADDING * 2, PAGE_MAX_HEIGHT),
  );
  const canvas = document.createElement("canvas");
  canvas.width = PAGE_WIDTH;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas indisponível neste navegador.");
  ctx.fillStyle = COLORS.page;
  ctx.fillRect(0, 0, PAGE_WIDTH, height);

  let y = PADDING;
  for (const item of spec.items) {
    y = drawSlice(ctx, item, y);
  }
  return canvas;
}

export interface ConversationPdfResult {
  blob: Blob;
  pageCount: number;
}

export async function conversationImagesPdf(
  messages: ShareMessage[],
): Promise<ConversationPdfResult> {
  const measure = document.createElement("canvas");
  const measureCtx = measure.getContext("2d");
  if (!measureCtx) throw new Error("Canvas indisponível neste navegador.");
  const blocks = buildBlocks(measureCtx, messages);
  if (blocks.length <= 1) throw new Error("A conversa ainda está vazia.");

  const usable = PAGE_MAX_HEIGHT - PADDING * 2;

  const pages: PageSpec[] = [];
  let current: PageSpec = { items: [], contentHeight: 0 };

  const closePage = () => {
    if (current.items.length === 0) return;
    pages.push(current);
    current = { items: [], contentHeight: 0 };
  };

  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];

    // Bloco que cabe numa página: atómico — ou entra na página atual, ou
    // fecha-a e passa para a seguinte inteiro.
    if (sliceHeight(block, 0, block.lines.length) <= usable) {
      const height =
        sliceHeight(
          block,
          0,
          block.lines.length,
        ) + (current.items.length > 0 ? GAP : 0);
      if (current.contentHeight + height > usable && current.items.length > 0) {
        closePage();
      }
      const gap = current.items.length > 0 ? GAP : 0;
      current.items.push({ block, startLine: 0, endLine: block.lines.length });
      current.contentHeight += sliceHeight(block, 0, block.lines.length) + gap;
      continue;
    }

    // Mensagem muito longa: fecha a página atual e divide por linhas
    // completas, nunca a meio da linha.
    closePage();
    const maxLines = Math.max(
      1,
      Math.floor((usable - CHROME_HEIGHT) / LINE_HEIGHT),
    );
    for (let startLine = 0; startLine < block.lines.length; startLine += maxLines) {
      const endLine = Math.min(block.lines.length, startLine + maxLines);
      const height = sliceHeight(block, startLine, endLine);
      current.items.push({ block, startLine, endLine });
      current.contentHeight += height;
      if (endLine < block.lines.length) {
        closePage();
      }
    }
  }
  closePage();

  if (pages.length === 0) throw new Error("A conversa ainda está vazia.");

  const pdfPages = pages.map((spec) => {
    const canvas = renderPage(spec);
    return {
      jpegDataUrl: canvas.toDataURL("image/jpeg", 0.92),
      widthPx: PAGE_WIDTH,
      heightPx: Math.max(
        PAGE_MIN_HEIGHT,
        Math.min(spec.contentHeight + PADDING * 2, PAGE_MAX_HEIGHT),
      ),
    };
  });

  return { blob: imagesToPdfBlob(pdfPages), pageCount: pdfPages.length };
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
