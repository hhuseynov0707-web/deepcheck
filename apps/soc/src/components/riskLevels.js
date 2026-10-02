import { risk } from "../designTokens.js";
import { AlertCircleIcon, AlertTriangleIcon, BanIcon, CheckIcon } from "./icons.jsx";

// The four published score bands and the Turkish label each one carries, in one
// place, so the badge, the session list and the chart's background bands cannot
// drift apart.
//
// THIS IS LABELLING, NOT A DECISION. Whether a payment is allowed, warned,
// stepped up or blocked is settled by POST /api/decision on the server, which
// applies the 40/60/80 ladder where an attacker cannot edit it. Nothing in the
// browser may re-derive that: this table only decides which word and which
// colour are printed next to a score the server already sent.
//
// Each level also carries a glyph. Deuteranopia makes the 60-80 orange and the
// 80-100 rose hard to tell apart, so the band is always readable three ways --
// the word, the icon and the colour -- never the colour alone.
//
// `action` is the PUBLISHED ladder entry for the band (CLAUDE.md "Risk Skoru
// Kateqoriyaları"), as a Turkish phrase to print. It is read by nothing but a
// <span>: no control flow anywhere in this app branches on it, because the
// action is the server's to take in POST /api/decision. It is here so the demo
// can state the policy beside the live score instead of leaving a juror to
// guess what a number means -- and every screen that prints it also prints
// that the server decides.
export const RISK_LEVELS = [
  {
    key: "safe",
    label: "Gerçek Kullanıcı",
    action: "Müdahale yok",
    upperBound: 40,
    hex: risk.safe,
    Icon: CheckIcon,
    text: "text-risk-safe",
    fill: "bg-risk-safe",
    tint: "bg-risk-safe/10",
    border: "border-risk-safe/30",
  },
  {
    key: "suspect",
    label: "Şüpheli",
    action: "Ek doğrulama",
    upperBound: 60,
    hex: risk.suspect,
    Icon: AlertCircleIcon,
    text: "text-risk-suspect",
    fill: "bg-risk-suspect",
    tint: "bg-risk-suspect/10",
    border: "border-risk-suspect/30",
  },
  {
    key: "high",
    label: "Yüksek Risk",
    action: "Ek doğrulama",
    upperBound: 80,
    hex: risk.high,
    Icon: AlertTriangleIcon,
    text: "text-risk-high",
    fill: "bg-risk-high",
    tint: "bg-risk-high/10",
    border: "border-risk-high/30",
  },
  {
    key: "blocked",
    label: "Bot Tespit Edildi",
    action: "Oturum engellenir",
    upperBound: Infinity,
    hex: risk.blocked,
    Icon: BanIcon,
    text: "text-risk-blocked",
    fill: "bg-risk-blocked",
    tint: "bg-risk-blocked/10",
    border: "border-risk-blocked/30",
  },
];

// Same boundaries the ladder has always used here: strictly below 40, 60, 80.
export function riskLevelFor(score) {
  return RISK_LEVELS.find((level) => score < level.upperBound) ?? RISK_LEVELS[RISK_LEVELS.length - 1];
}

export function riskLevelByKey(key) {
  return RISK_LEVELS.find((level) => level.key === key) ?? null;
}
