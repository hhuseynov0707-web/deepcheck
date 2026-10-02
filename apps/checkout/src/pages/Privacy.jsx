// /gizlilik: the KVKK notice, served by this app.
//
// One source: docs/kvkk-aydinlatma.md, imported as text at build time (the
// Dockerfile copies it into the build stage), so the page and the document
// cannot drift apart and the link works on an offline LAN demo.
//
// The renderer was ported from the former combined demo's KVKK page
// (frontend/, removed 2026-10-02), restyled for this light page. It handles the
// subset that file uses -- headings, paragraphs, "-" and "1." lists, one pipe
// table, **bold** and `code` -- and builds React elements, never HTML strings,
// so nothing in the file can inject markup.
import { useEffect } from "react";

import notice from "../../../../docs/kvkk-aydinlatma.md?raw";
import { ArrowLeftIcon } from "../components/icons.jsx";

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
        <strong key={key} className="font-semibold text-ink">
          {match[1]}
        </strong>,
      );
    } else {
      parts.push(
        <code key={key} className="rounded bg-canvas px-1 py-0.5 text-[0.85em] text-ink">
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

// "## 5. Haklarınız ..." -> "bolum-5", so other pages can link to a numbered
// section (lib/routes.js RIGHTS_HREF). Unnumbered headings get no id.
export function sectionId(headingText) {
  const number = /^(\d+)\.\s/.exec(headingText);
  return number ? `bolum-${number[1]}` : undefined;
}

function Block({ block, index }) {
  const key = `b${index}`;
  if (block.type === "heading") {
    if (block.level === 1) {
      return <h1 className="text-2xl font-semibold tracking-tight text-ink">{inline(block.text, key)}</h1>;
    }
    // scroll-mt clears the sticky site header when a link lands here.
    return (
      <h2 id={sectionId(block.text)} className="scroll-mt-20 pt-4 text-lg font-semibold tracking-tight text-ink">
        {inline(block.text, key)}
      </h2>
    );
  }
  if (block.type === "list") {
    const ListTag = block.ordered ? "ol" : "ul";
    return (
      <ListTag className={`${block.ordered ? "list-decimal" : "list-disc"} space-y-1.5 pl-6 text-sm leading-6 text-ink`}>
        {block.items.map((item, i) => (
          <li key={`${key}-${i}`}>{inline(item, `${key}-${i}`)}</li>
        ))}
      </ListTag>
    );
  }
  if (block.type === "table") {
    return (
      <div className="-mx-1 overflow-x-auto px-1">
        <table className="w-full border-collapse text-left text-sm text-ink">
          <thead>
            <tr>
              {block.header.map((cell, i) => (
                <th key={`${key}-h${i}`} className="border-b border-line-control px-2 py-2 font-semibold">
                  {inline(cell, `${key}-h${i}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={`${key}-r${r}`} className="align-top">
                {row.map((cell, c) => (
                  <td key={`${key}-r${r}-${c}`} className="border-b border-line px-2 py-2">
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
  return <p className="text-sm leading-6 text-ink">{inline(block.text, key)}</p>;
}

// What THIS page sends where, in the store's own words. Each sentence is a
// property of apps/checkout and apps/checkout-server as written; the notice
// below covers the behaviour SDK and the DeepCheck core. The two durations
// are that server's constants, not measurements: a pending challenge lives
// PENDING_TTL_S (600 s) and an address one rate-limit window (60 s), each
// plus up to SWEEP_INTERVAL_S (15 s) until the periodic sweep drops it --
// hence "yaklaşık". Change them together.
//
// Guest checkout: no e-mail address or account is asked for. The reference
// the core receives is per session (checkout-api customer_ref_for: HMAC of the
// session id under CHECKOUT_CUSTOMER_REF_KEY, a key given to checkout-api only
// and refused at startup if it equals the merchant key the core holds).
const STORE_FACTS = [
  "Bu ödeme sayfası misafir ödemesidir: e-posta adresi, hesap ya da başka bir kimlik bilgisi istenmez.",
  "Ödeme sayfası mağaza sunucusuna yalnızca oturum kimliğini, oturum jetonunu ve kartın son 4 hanesini, türünü ve son kullanma tarihini gönderir. Tam kart numarası ve CVV hiçbir sunucuya gönderilmez.",
  "Mağaza sunucusu DeepCheck'e kartın görünen alanlarını da göndermez; yalnızca bu ödeme oturumuna özel bir müşteri referansı iletir. Referans, oturum kimliğinden, yalnızca mağaza sunucusuna verilen ayrı bir anahtarla (HMAC-SHA256) üretilir; sizinle ilgili hiçbir bilgi içermez ve her ödeme oturumu için farklıdır.",
  "Ek doğrulama istenen bir ödemede müşteri referansı ile kartın son 4 hanesi ve türü, doğrulama bitene kadar, en fazla yaklaşık 10 dakika, yalnızca mağaza sunucusunun belleğinde tutulur.",
  "Mağaza sunucusu, kısa sürede çok sayıda deneme yapılmasını sınırlamak için IP adresinizi yalnızca çalışan sürecin belleğinde, son denemenizden sonra yaklaşık bir dakika tutar. Erişim günlükleri kapalıdır; nginx'in hata günlüğü açıktır ve bir hata satırında istemci adresini yazabilir.",
];

// Shown only while the page itself is served over plain http, which is how
// the LAN demo serves it (docs/canli-demo.md). Read from the address, not
// assumed: behind TLS the sentence would be false and is not shown.
export const PLAIN_HTTP_FACT =
  "Bu demo sayfası şifrelenmemiş HTTP üzerinden sunuluyor. Sayfanın gönderdiği her şey (kartın son 4 hanesi, türü ve son kullanma tarihi, doğrulama kodu, oturum bilgileri ve davranış ölçümleri) ağ trafiğini görebilen biri tarafından okunabilir; tam kart numarası ve CVV gönderilmediği için bunlara dahil değildir. Gerçek bir ödeme sayfası yalnızca HTTPS ile sunulur.";

export default function Privacy({ markdown = notice, protocol = window.location.protocol }) {
  useEffect(() => {
    const previous = document.title;
    document.title = "Aydınlatma Metni - TechStore";
    return () => {
      document.title = previous;
    };
  }, []);

  // A link such as the decline message's (/gizlilik#bolum-5) arrives before
  // React has rendered the heading, so the browser's own jump to the fragment
  // finds nothing. Done once the notice is in the DOM.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView?.();
  }, []);

  const blocks = parseNotice(markdown);
  const facts = protocol === "http:" ? [...STORE_FACTS, PLAIN_HTTP_FACT] : STORE_FACTS;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-12 pt-6 sm:px-6 sm:pt-10">
      <a
        href="/"
        className="inline-flex min-h-[2.75rem] cursor-pointer items-center gap-2 rounded-field text-sm font-medium text-brand transition-colors duration-150 hover:text-brand-hover"
      >
        <ArrowLeftIcon className="h-4 w-4" />
        Ödeme sayfasına dön
      </a>

      <section aria-labelledby="magaza-basligi" className="mt-4 rounded-card border border-brand-line bg-brand-soft p-5 sm:p-6">
        <h2 id="magaza-basligi" className="text-base font-semibold text-ink">
          Bu ödeme sayfası hakkında
        </h2>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6 text-ink">
          {facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      </section>

      <article className="mt-6 space-y-4 rounded-card border border-line bg-card p-5 shadow-card sm:p-8">
        {blocks.map((block, index) => (
          <Block key={index} block={block} index={index} />
        ))}
      </article>
    </div>
  );
}
