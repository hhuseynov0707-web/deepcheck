import { describe, expect, it } from "vitest";

import {
  detectBrand,
  formatCardName,
  formatCardNumber,
  formatCvv,
  formatExpiry,
  luhnValid,
  parseExpiry,
  validateCardName,
  validateCardNumber,
  validateCvv,
  validateExpiry,
} from "./card.js";

// Appends the Luhn check digit, so test numbers are valid by construction
// rather than copied from somewhere.
function withCheckDigit(body) {
  for (let d = 0; d <= 9; d += 1) {
    if (luhnValid(`${body}${d}`)) return `${body}${d}`;
  }
  throw new Error("unreachable");
}

const NOW = new Date(2026, 9, 1); // 1 Ekim 2026

describe("card helpers", () => {
  it("detects the four accepted brands and nothing else", () => {
    expect(detectBrand("4111")).toBe("visa");
    expect(detectBrand("5555 5555")).toBe("mastercard");
    expect(detectBrand("2221 00")).toBe("mastercard");
    expect(detectBrand("2720 99")).toBe("mastercard");
    expect(detectBrand("3782")).toBe("amex");
    expect(detectBrand("3412")).toBe("amex");
    expect(detectBrand("9792 1234")).toBe("troy");
    expect(detectBrand("6011 0000")).toBeNull(); // Discover
    expect(detectBrand("2721")).toBeNull();
    expect(detectBrand("")).toBeNull();
  });

  it("groups 4-4-4-4, and 4-6-5 for American Express", () => {
    expect(formatCardNumber("4111111111111111999")).toBe("4111 1111 1111 1111");
    expect(formatCardNumber("41111")).toBe("4111 1");
    expect(formatCardNumber("378282246310005")).toBe("3782 822463 10005");
    expect(formatCardNumber("3782-8224-6310-0059")).toBe("3782 822463 10005");
    expect(formatCardNumber("abc")).toBe("");
  });

  it("checks the Luhn digit", () => {
    expect(luhnValid("4111 1111 1111 1111")).toBe(true);
    expect(luhnValid("4111 1111 1111 1112")).toBe(false);
    expect(luhnValid("378282246310005")).toBe(true);
    expect(luhnValid(withCheckDigit("979200000000000"))).toBe(true);
    expect(luhnValid("0")).toBe(false);
  });

  it("formats and parses the expiry as AA/YY", () => {
    expect(formatExpiry("1")).toBe("1");
    expect(formatExpiry("4")).toBe("04");
    expect(formatExpiry("123")).toBe("12/3");
    expect(formatExpiry("12/30")).toBe("12/30");
    expect(formatExpiry("12305")).toBe("12/30");
    expect(parseExpiry("07/29")).toEqual({ month: 7, year: 2029 });
    expect(parseExpiry("7/29")).toBeNull();
  });

  it("limits the CVV to the brand's length", () => {
    expect(formatCvv("12345", "visa")).toBe("123");
    expect(formatCvv("12345", "amex")).toBe("1234");
    expect(formatCvv("1a2", null)).toBe("12");
  });

  it("upper-cases the name the Turkish way", () => {
    expect(formatCardName("ayşe yılmaz")).toBe("AYŞE YILMAZ");
    // Digits and symbols are dropped as typed or pasted.
    expect(formatCardName("ay3şe y1lmaz#")).toBe("AYŞE YLMAZ");
    expect(formatCardName("o'neil-nur.")).toBe("O'NEİL-NUR.");
    expect(formatCardName("ilker  işık")).toBe("İLKER İŞIK");
  });
});

describe("validation messages", () => {
  it("kart numarası", () => {
    expect(validateCardNumber("")).toBe("Kart numaranızı girin.");
    expect(validateCardNumber("4111 1111")).toBe("Kart numarası eksik görünüyor.");
    expect(validateCardNumber("4111 1111 1111 1112")).toBe("Kart numarası geçersiz. Lütfen kontrol edin.");
    expect(validateCardNumber(withCheckDigit("601100000000000"))).toMatch(/desteklenmiyor/);
    expect(validateCardNumber("4111 1111 1111 1111")).toBeNull();
    expect(validateCardNumber("5555 5555 5555 4444")).toBeNull();
    expect(validateCardNumber("3782 822463 10005")).toBeNull();
    expect(validateCardNumber(withCheckDigit("979200000000000"))).toBeNull();
  });

  it("isim", () => {
    expect(validateCardName(" ")).toBe("Kart üzerindeki ismi girin.");
    expect(validateCardName("A1")).toBe("İsimde rakam kullanılamaz.");
    expect(validateCardName("AY")).toMatch(/harflerle/);
    expect(validateCardName("AYŞE YILMAZ")).toBeNull();
  });

  it("son kullanma", () => {
    expect(validateExpiry("", NOW)).toBe("Son kullanma tarihini girin.");
    expect(validateExpiry("12", NOW)).toMatch(/AA\/YY/);
    expect(validateExpiry("13/28", NOW)).toMatch(/Geçerli bir ay/);
    expect(validateExpiry("00/28", NOW)).toMatch(/Geçerli bir ay/);
    expect(validateExpiry("09/26", NOW)).toBe("Kartınızın son kullanma tarihi geçmiş.");
    expect(validateExpiry("10/26", NOW)).toBeNull(); // valid through the end of its month
    expect(validateExpiry("12/30", NOW)).toBeNull();
    expect(validateExpiry("12/99", NOW)).toMatch(/kontrol edin/);
  });

  it("CVV", () => {
    expect(validateCvv("", "visa")).toBe("Güvenlik kodunu (CVV) girin.");
    expect(validateCvv("12", "visa")).toBe("CVV 3 haneli olmalıdır.");
    expect(validateCvv("123", "amex")).toBe("CVV 4 haneli olmalıdır.");
    expect(validateCvv("1234", "amex")).toBeNull();
    expect(validateCvv("739", "mastercard")).toBeNull();
  });
});
