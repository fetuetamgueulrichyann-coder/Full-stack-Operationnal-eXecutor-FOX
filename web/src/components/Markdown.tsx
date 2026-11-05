import { Fragment, type ReactNode } from "react";

/**
 * Rendu léger du Markdown produit par l'IA (gras, italique, code, titres, listes).
 * Construit uniquement des éléments React : aucun HTML brut n'est interprété.
 */

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|(?<![*\w])\*[^*\s][^*\n]*\*(?![*\w]))/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    const token = match[0];
    const key = `${keyPrefix}-${n++}`;
    if (token.startsWith("**")) out.push(<strong key={key}>{inline(token.slice(2, -2), key)}</strong>);
    else if (token.startsWith("`")) out.push(<code key={key}>{token.slice(1, -1)}</code>);
    else out.push(<em key={key}>{token.slice(1, -1)}</em>);
    last = start + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "code"; text: string };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{1,4})\s+(.*)$/;

function parse(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }
    if (line.trim().startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "h", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    const bullet = BULLET.exec(line);
    const ordered = bullet ? null : ORDERED.exec(line);
    if (bullet || ordered) {
      const kind = bullet ? "ul" : "ol";
      const pattern = bullet ? BULLET : ORDERED;
      const items: string[] = [];
      while (i < lines.length) {
        const m = pattern.exec(lines[i]);
        if (!m) break;
        items.push(m[1]);
        i++;
      }
      blocks.push({ kind, items });
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !HEADING.test(lines[i]) &&
      !BULLET.test(lines[i]) &&
      !ORDERED.test(lines[i]) &&
      !lines[i].trim().startsWith("```")
    ) {
      para.push(lines[i++]);
    }
    blocks.push({ kind: "p", lines: para });
  }
  return blocks;
}

export function Markdown({ text }: { text: string | null | undefined }) {
  const blocks = parse(typeof text === "string" ? text : "");
  return (
    <div className="md">
      {blocks.map((block, index) => {
        const key = `b${index}`;
        switch (block.kind) {
          case "h":
            return (
              <p key={key} className="md-title">
                {inline(block.text, key)}
              </p>
            );
          case "ul":
            return (
              <ul key={key}>
                {block.items.map((item, k) => (
                  <li key={k}>{inline(item, `${key}-${k}`)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key}>
                {block.items.map((item, k) => (
                  <li key={k}>{inline(item, `${key}-${k}`)}</li>
                ))}
              </ol>
            );
          case "code":
            return (
              <pre key={key}>
                <code>{block.text}</code>
              </pre>
            );
          default:
            return (
              <p key={key}>
                {block.lines.map((line, k) => (
                  <Fragment key={k}>
                    {k > 0 && <br />}
                    {inline(line, `${key}-${k}`)}
                  </Fragment>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}
