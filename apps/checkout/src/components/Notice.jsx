import { AlertIcon, InfoIcon } from "./icons.jsx";

// A message about the payment, in words first: the colour and the icon repeat
// what the sentence says and never carry it alone.
const TONES = {
  danger: { box: "border-danger-line bg-danger-soft", icon: "text-danger", Icon: AlertIcon },
  info: { box: "border-brand-line bg-brand-soft", icon: "text-brand", Icon: InfoIcon },
};

export default function Notice({ tone = "info", children, action }) {
  const { box, icon, Icon } = TONES[tone] ?? TONES.info;
  return (
    <div className={`animate-fade-in rounded-field border px-4 py-3 ${box}`}>
      <div className="flex items-start gap-3">
        <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${icon}`} />
        <div className="min-w-0 flex-1 text-sm leading-6 text-ink">
          <p>{children}</p>
          {action && <div className="mt-3">{action}</div>}
        </div>
      </div>
    </div>
  );
}
