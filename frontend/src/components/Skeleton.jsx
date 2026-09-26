// The loading state. A block the shape and size of the thing that is coming,
// so the layout does not jump when the data lands -- and never a spinner where
// a shape is known.
//
// aria-hidden: a screen reader gets the region's own aria-live text ("...
// yükleniyor"), not a description of grey rectangles.
export default function Skeleton({ className = "h-4 w-full", rounded = "rounded-field" }) {
  return (
    <span
      aria-hidden="true"
      className={`relative block overflow-hidden bg-panel-raised ${rounded} ${className}`}
    >
      <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
    </span>
  );
}

export function SkeletonText({ lines = 3, className = "" }) {
  return (
    <span aria-hidden="true" className={`block space-y-2 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? "w-2/3" : "w-full"}`} rounded="rounded-full" />
      ))}
    </span>
  );
}
