// The KVKK notice, served by the app itself at /kvkk.
//
// The Demo page used to link to the file on GitHub, which did not exist -- a
// 404 on the one link a jury member clicks to see what happens to their data --
// and would only ever resolve for a pushed, public repository. The notice has a
// single source, docs/kvkk-aydinlatma.md, imported here as text at build time
// (frontend/Dockerfile copies it into the image), so the page and the document
// cannot drift apart and the link works on an offline LAN demo too.
//
// A deliberately tiny renderer for the subset that file uses -- headings,
// paragraphs, "-" lists, one pipe table, **bold** and `code` -- building React
// elements, never HTML strings, so nothing in the file can inject markup.
import notice from "../../../docs/kvkk-aydinlatma.md?raw";

function inline(text, keyPrefix) {
  const parts = [];
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`/g;
  let last = 0;
  let match;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const key = `${keyPrefix}-${index++}`;
    if (match[1] !== undefined) {
      parts.push(
        <strong key={key} className="font-semibold text-zinc-100">
          {match[1]}
        </strong>,
      );
    } else {
      parts.push(
        <code key={key} className="rounded bg-zinc-800 px-1 py-0.5 text-[0.85em] text-zinc-200">
          {match[2]}
        </code>,
      );
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function cells(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

// Blocks: consecutive non-blank lines of the same kind.
export function parseNotice(markdown) {
  const blocks = [];
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const row = cells(lines[i]);
        if (!row.every((cell) => /^:?-{3,}:?$/.test(cell))) rows.push(row);
        i += 1;
      }
      blocks.push({ type: "table", header: rows[0], rows: rows.slice(1) });
      continue;
    }
    if (/^\s*(-|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*(-|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*(-|\d+\.)\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    const paragraph = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^#{1,3}\s/.test(lines[i]) &&
      !lines[i].trim().startsWith("|") &&
      !/^\s*(-|\d+\.)\s+/.test(lines[i])
    ) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join(" ") });
  }
  return blocks;
}

function Block({ block, index }) {
  const key = `b${index}`;
  if (block.type === "heading") {
    if (block.level === 1) {
      return <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">{inline(block.text, key)}</h1>;
    }
    return <h2 className="pt-4 text-lg font-semibold tracking-tight text-zinc-100">{inline(block.text, key)}</h2>;
  }
  if (block.type === "list") {
    const ListTag = block.ordered ? "ol" : "ul";
    return (
      <ListTag className={`${block.ordered ? "list-decimal" : "list-disc"} space-y-1 pl-6 text-sm text-zinc-300`}>
        {block.items.map((item, i) => (
          <li key={`${key}-${i}`}>{inline(item, `${key}-${i}`)}</li>
        ))}
      </ListTag>
    );
  }
  if (block.type === "table") {
    return (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm text-zinc-300">
          <thead>
            <tr>
              {block.header.map((cell, i) => (
                <th key={`${key}-h${i}`} className="border-b border-zinc-700 px-2 py-2 font-medium text-zinc-100">
                  {inline(cell, `${key}-h${i}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={`${key}-r${r}`} className="align-top">
                {row.map((cell, c) => (
                  <td key={`${key}-r${r}-${c}`} className="border-b border-zinc-800 px-2 py-2">
                    {inline(cell, `${key}-r${r}-${c}`)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return <p className="text-sm leading-relaxed text-zinc-300">{inline(block.text, key)}</p>;
}

export default function KvkkNotice({ markdown = notice }) {
  const blocks = parseNotice(markdown);
  return (
    <div className="min-h-[calc(100vh-64px)] px-4 py-10">
      <article className="mx-auto max-w-3xl space-y-4 rounded-lg border border-zinc-800 bg-[#18181b] p-6 shadow-xl shadow-black/50">
        {blocks.map((block, index) => (
          <Block key={index} block={block} index={index} />
        ))}
      </article>
    </div>
  );
}
