"use client";

import { type ReactNode } from "react";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

const INLINE_MARKDOWN_PATTERN =
  /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))/g;

function safeExternalHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function renderInlineMarkdown(value: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let tokenIndex = 0;

  for (const match of value.matchAll(INLINE_MARKDOWN_PATTERN)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(value.slice(cursor, start));

    const token = match[0];
    const key = `${keyPrefix}-${tokenIndex}`;
    if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = linkMatch ? safeExternalHref(linkMatch[2]) : null;
      nodes.push(
        href && linkMatch ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {linkMatch[1]}
          </a>
        ) : (
          token
        ),
      );
    }
    cursor = start + token.length;
    tokenIndex += 1;
  }

  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

export function ChatMarkdown({
  content,
  classes,
}: {
  content: string;
  classes: {
    mdH2: string;
    mdH3: string;
    mdParagraph: string;
    mdUl: string;
    mdSpacing: string;
  };
}) {
  const lines = (content || "").split("\n");
  return (
    <>
      {lines.map((line, idx) => {
        if (line.startsWith("### ")) {
          return (
            <h3 key={idx} className={classes.mdH3}>
              {renderInlineMarkdown(line.slice(4), `h3-${idx}`)}
            </h3>
          );
        }
        if (line.startsWith("## ")) {
          return (
            <h2 key={idx} className={classes.mdH2}>
              {renderInlineMarkdown(line.slice(3), `h2-${idx}`)}
            </h2>
          );
        }
        if (line.startsWith("🗳️ ")) {
          return (
            <p key={idx} className={classes.mdParagraph}>
              {renderInlineMarkdown(line, `ballot-${idx}`)}
            </p>
          );
        }
        if (line.startsWith("- ") || line.startsWith("* ")) {
          return (
            <ul key={idx} className={classes.mdUl}>
              <li>{renderInlineMarkdown(line.slice(2), `bullet-${idx}`)}</li>
            </ul>
          );
        }
        return (
          <p
            key={idx}
            className={line.trim() === "" ? classes.mdSpacing : classes.mdParagraph}
          >
            {line ? renderInlineMarkdown(line, `line-${idx}`) : "\u00A0"}
          </p>
        );
      })}
    </>
  );
}
