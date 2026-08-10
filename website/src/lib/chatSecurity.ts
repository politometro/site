export const MAX_CHAT_REQUEST_BYTES = 128 * 1024;
export const MAX_CHAT_MESSAGES = 64;
export const MAX_CHAT_MESSAGE_CHARACTERS = 12_000;
export const MAX_RAG_TEXT_CHARACTERS = 1_500;

export const GENERIC_CHAT_ERROR =
  "Ocorreu um erro temporário no chat. Tenta novamente dentro de instantes.";

export type UserChatMessage = {
  role: "user";
  content: string;
};

export type SafeRagMetadata = {
  text: string;
  party: string;
  category: string;
  year: string;
};

export class ChatRequestTooLargeError extends Error {
  constructor() {
    super("CHAT_REQUEST_TOO_LARGE");
    this.name = "ChatRequestTooLargeError";
  }
}

export class InvalidChatRequestError extends Error {
  constructor() {
    super("INVALID_CHAT_REQUEST");
    this.name = "InvalidChatRequestError";
  }
}

const UNSAFE_UNICODE_CHARACTERS =
  /[\p{Cc}\p{Cf}\p{Co}\p{Cn}\p{Cs}]/gu;

/** Replace controls, bidi/format, private-use, unassigned, and surrogate code points. */
export function stripUnsafeUnicode(value: string) {
  return value.replace(UNSAFE_UNICODE_CHARACTERS, " ");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function redactWithPattern(value: string, pattern: RegExp, replacement: string) {
  return value.replace(pattern, replacement);
}

function redactPathLikeText(value: string) {
  return value
    .replace(
      /(?:[A-Za-z]:[\\/][^\s<>"'`]+|(?:\.\.?[\\/])[^\s<>"'`]+|\bfile:\/\/[^\s<>"'`]+|(?<!:)\/(?:home|Users|var|etc|tmp|opt|app|workspace|private|mnt)(?:[\\/][^\s<>"'`]+)+)/gi,
      "[redacted]"
    )
    .replace(
      /\b[^\s<>"'`/\\]+\.(?:env|ini|cfg|conf|json|ya?ml|toml|md|txt|pdf|csv|ts|tsx|js|jsx|py)\b/gi,
      "[redacted]"
    );
}

/**
 * Redacts configured provider values plus common credential shapes. This is
 * intentionally generic defense-in-depth; callers still control which values
 * are allowed to enter a provider payload.
 */
export function redactSensitiveText(
  value: string,
  explicitSecrets: readonly (string | undefined)[] = []
) {
  let redacted = stripUnsafeUnicode(value);

  const secrets = Array.from(
    new Set(
      explicitSecrets.filter(
        (secret): secret is string =>
          typeof secret === "string" && secret.length >= 6
      )
    )
  ).sort((left, right) => right.length - left.length);

  for (const secret of secrets) {
    redacted = redacted.split(secret).join("[redacted]");
  }

  redacted = redactWithPattern(
    redacted,
    /-----BEGIN [^-]+ PRIVATE KEY-----[\s\S]*?-----END [^-]+ PRIVATE KEY-----/gi,
    "[redacted]"
  );
  redacted = redactWithPattern(
    redacted,
    /\b(?:sk|gsk|hf|pcsk|pclx|xox[baprs]-)[A-Za-z0-9_-]{12,}\b/gi,
    "[redacted]"
  );
  redacted = redactWithPattern(
    redacted,
    /\bBearer\s+[A-Za-z0-9._~+/=-]{12,}/gi,
    "Bearer [redacted]"
  );
  redacted = redactWithPattern(
    redacted,
    /\b(?:api[-_ ]?key|access[-_ ]?token|auth(?:orization)?|password|passwd|secret|token)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;)}\]]+)/gi,
    "[redacted]"
  );
  redacted = redactWithPattern(
    redacted,
    /\b(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}\.[A-Za-z0-9._-]{10,})\b/g,
    "[redacted]"
  );
  redacted = redactWithPattern(
    redacted,
    /\b[A-Z][A-Z0-9_]{2,}\s*=\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/g,
    "[redacted]"
  );

  return redactPathLikeText(stripUnsafeUnicode(redacted));
}

export function normalizePublicText(
  value: unknown,
  maxCharacters: number
) {
  if (typeof value !== "string" && typeof value !== "number") {
    return "";
  }

  const normalized = redactSensitiveText(String(value).normalize("NFKC"))
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  return normalized.slice(0, Math.max(0, maxCharacters)).trim();
}

export function sanitizeRagMetadata(
  metadata: unknown,
  maxTextCharacters = MAX_RAG_TEXT_CHARACTERS
): SafeRagMetadata {
  const record = isRecord(metadata) ? metadata : {};

  return {
    text: normalizePublicText(record.text, maxTextCharacters),
    party: normalizePublicText(record.party, 120),
    category: normalizePublicText(record.category, 120),
    year: normalizePublicText(record.year, 20),
  };
}

export function sanitizeChatMessages(value: unknown): UserChatMessage[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const userMessages = value.filter(isRecord).flatMap((message) => {
    if (message.role !== "user" || typeof message.content !== "string") {
      return [];
    }

    const content = stripUnsafeUnicode(message.content.normalize("NFKC"))
      .slice(0, MAX_CHAT_MESSAGE_CHARACTERS)
      .trim();

    return content ? [{ role: "user" as const, content }] : [];
  });

  return userMessages.slice(-MAX_CHAT_MESSAGES);
}

export async function readRequestText(
  request: Request,
  maxBytes = MAX_CHAT_REQUEST_BYTES
) {
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new ChatRequestTooLargeError();
  }

  if (!request.body) {
    return "";
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      if (!value) {
        continue;
      }

      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel();
        throw new ChatRequestTooLargeError();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bodyBytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bodyBytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  return new TextDecoder("utf-8", { fatal: true }).decode(bodyBytes);
}

export async function readChatMessages(request: Request) {
  const bodyText = await readRequestText(request);
  let parsed: unknown;

  try {
    parsed = JSON.parse(bodyText);
  } catch {
    throw new InvalidChatRequestError();
  }

  if (!isRecord(parsed)) {
    throw new InvalidChatRequestError();
  }

  const messages = sanitizeChatMessages(parsed.messages);
  if (messages.length === 0) {
    throw new InvalidChatRequestError();
  }

  return messages;
}

function parseHttpsOrigin(value: unknown) {
  if (typeof value !== "string") {
    return null;
  }

  const rawValue = value.trim();
  if (!rawValue || rawValue.includes("\\")) {
    return null;
  }

  try {
    const url = new URL(
      rawValue.includes("://") ? rawValue : `https://${rawValue}`
    );
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    ) {
      return null;
    }

    return url;
  } catch {
    return null;
  }
}

/**
 * Returns the safe origin for a Pinecone query, or null before any index key
 * is sent to the returned host.
 */
export function allowedPineconeIndexOrigin(
  candidate: unknown,
  configuredHost?: unknown
) {
  const candidateUrl = parseHttpsOrigin(candidate);
  if (!candidateUrl) {
    return null;
  }

  const configuredValue =
    typeof configuredHost === "string" ? configuredHost.trim() : "";
  if (configuredValue) {
    const configuredUrl = parseHttpsOrigin(configuredValue);
    if (
      !configuredUrl ||
      candidateUrl.origin !== configuredUrl.origin
    ) {
      return null;
    }
  } else if (
    candidateUrl.hostname !== "pinecone.io" &&
    !candidateUrl.hostname.endsWith(".pinecone.io")
  ) {
    return null;
  } else if (candidateUrl.port && candidateUrl.port !== "443") {
    return null;
  }

  return candidateUrl.origin;
}
