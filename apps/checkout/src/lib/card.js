// Card input helpers: formatting, brand detection and validation. Pure
// functions, so the rules are tested without rendering anything.
//
// Everything here is input help for the person paying. None of it is a
// control: the store's server validates the display fields again
// (apps/checkout-server/main.py), and the full number and CVV never leave this
// page at all.

// Grouping follows what is printed on the card: 4-4-4-4 for the 16-digit
// brands, 4-6-5 for American Express.
export const BRANDS = {
  visa: { label: "VISA", name: "Visa", length: 16, cvv: 3, gaps: [4, 8, 12] },
  mastercard: { label: "MASTERCARD", name: "Mastercard", length: 16, cvv: 3, gaps: [4, 8, 12] },
  troy: { label: "TROY", name: "Troy", length: 16, cvv: 3, gaps: [4, 8, 12] },
  amex: { label: "AMEX", name: "American Express", length: 15, cvv: 4, gaps: [4, 10] },
};

export const ACCEPTED_BRANDS = ["visa", "mastercard", "troy", "amex"];

const DEFAULT_LAYOUT = { length: 16, gaps: [4, 8, 12] };

export function digitsOnly(value) {
  return String(value ?? "").replace(/\D/g, "");
}

// Issuer prefixes, simplified to what a Turkish checkout meets: Visa 4,
// Mastercard 51-55 and 2221-2720, American Express 34/37, Troy 9792. Troy also
// issues from ranges that overlap other networks; those are not guessed at.
// Null until the prefix is unambiguous.
export function detectBrand(value) {
  const digits = digitsOnly(value);
  if (/^3[47]/.test(digits)) return "amex";
  if (/^9792/.test(digits)) return "troy";
  if (/^4/.test(digits)) return "visa";
  if (/^(5[1-5]|222[1-9]|22[3-9]\d|2[3-6]\d\d|27[01]\d|2720)/.test(digits)) return "mastercard";
  return null;
}

export function formatCardNumber(value) {
  const brand = detectBrand(value);
  const layout = brand ? BRANDS[brand] : DEFAULT_LAYOUT;
  const digits = digitsOnly(value).slice(0, layout.length);
  let out = "";
  for (let i = 0; i < digits.length; i += 1) {
    if (layout.gaps.includes(i)) out += " ";
    out += digits[i];
  }
  return out;
}

export function luhnValid(value) {
  const digits = digitsOnly(value);
  if (digits.length < 12) return false;
  let sum = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    let d = digits.charCodeAt(i) - 48;
    if (double) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
    double = !double;
  }
  return sum % 10 === 0;
}

// "AA/YY". A first digit above 1 can only be a single-digit month, so "4"
// becomes "04" -- the field then reads the way it will be checked.
export function formatExpiry(value) {
  let digits = digitsOnly(value).slice(0, 4);
  if (digits.length === 1 && Number(digits) > 1) digits = `0${digits}`;
  if (digits.length <= 2) return digits;
  return `${digits.slice(0, 2)}/${digits.slice(2)}`;
}

export function parseExpiry(value) {
  const match = /^(\d{2})\/(\d{2})$/.exec(String(value ?? "").trim());
  if (!match) return null;
  return { month: Number(match[1]), year: 2000 + Number(match[2]) };
}

export function formatCvv(value, brand) {
  return digitsOnly(value).slice(0, brand ? BRANDS[brand].cvv : 4);
}

// Turkish capitals: "i" becomes "İ", not "I".
// A cardholder name is letters: digits (and any other symbol) are dropped as
// they are typed or pasted, so the field can never hold one. Spaces, the
// apostrophe, the dot and the hyphen stay for names such as "O'NEIL" or
// "AYŞE-NUR".
export function formatCardName(value) {
  return String(value ?? "")
    .replace(/[^\p{L} .'-]/gu, "")
    .replace(/\s{2,}/g, " ")
    .toLocaleUpperCase("tr-TR");
}

// --- validation: each returns a Turkish message, or null when the value is fine.

export function validateCardNumber(value) {
  const digits = digitsOnly(value);
  if (!digits) return "Kart numaranızı girin.";
  const brand = detectBrand(digits);
  const length = brand ? BRANDS[brand].length : DEFAULT_LAYOUT.length;
  if (digits.length < length) return "Kart numarası eksik görünüyor.";
  if (!brand) return "Bu kart türü desteklenmiyor. Visa, Mastercard, Troy veya American Express kullanın.";
  if (!luhnValid(digits)) return "Kart numarası geçersiz. Lütfen kontrol edin.";
  return null;
}

export function validateCardName(value) {
  const name = String(value ?? "").trim();
  if (!name) return "Kart üzerindeki ismi girin.";
  if (/\d/.test(name)) return "İsimde rakam kullanılamaz.";
  if (name.length < 3 || !/^[\p{L}][\p{L} .'-]*$/u.test(name)) return "Kart üzerinde yazan ismi harflerle girin.";
  return null;
}

export const MAX_YEARS_AHEAD = 20;

export function validateExpiry(value, now = new Date()) {
  if (!String(value ?? "").trim()) return "Son kullanma tarihini girin.";
  const parsed = parseExpiry(value);
  if (!parsed) return "Son kullanma tarihini AA/YY biçiminde girin.";
  if (parsed.month < 1 || parsed.month > 12) return "Geçerli bir ay girin (01-12).";
  const current = now.getFullYear() * 12 + now.getMonth();
  const expiry = parsed.year * 12 + (parsed.month - 1);
  if (expiry < current) return "Kartınızın son kullanma tarihi geçmiş.";
  if (parsed.year > now.getFullYear() + MAX_YEARS_AHEAD) return "Son kullanma tarihini kontrol edin.";
  return null;
}

export function validateCvv(value, brand) {
  const digits = digitsOnly(value);
  const expected = brand ? BRANDS[brand].cvv : 3;
  if (!digits) return "Güvenlik kodunu (CVV) girin.";
  if (digits.length !== expected) return `CVV ${expected} haneli olmalıdır.`;
  return null;
}

export function cvvHelp(brand) {
  // Short enough for one line under a half-width field at 375 px.
  return brand === "amex" ? "Kartın önündeki 4 hane." : "Kartın arkasındaki 3 hane.";
}
