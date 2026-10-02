// Amounts as a Turkish shop prints them: "₺2.038,80". Built from a plain
// number format plus the sign, rather than style: "currency", so the output
// does not depend on how complete the runtime's locale data is (a currency
// style can come out as "TRY 2.038,80" on a minimal ICU build).
const NUMBER = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatTry(amount) {
  return `₺${NUMBER.format(amount)}`;
}

// "%20" from the cart's own figures, so the label cannot disagree with them.
export function vatPercent(subtotal, vat) {
  if (!subtotal) return null;
  return Math.round((vat / subtotal) * 100);
}
