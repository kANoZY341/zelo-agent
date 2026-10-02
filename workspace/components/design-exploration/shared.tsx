export function Glyph({
  name,
  size = 20,
}: {
  name:
    | "mic"
    | "stop"
    | "arrow"
    | "system"
    | "history"
    | "keyboard"
    | "close"
    | "plus"
    | "sound"
    | "back"
    | "check"
    | "pause";
  size?: number;
}) {
  const path = {
    mic: "M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0V5ZM6 10v1a6 6 0 0 0 12 0v-1M12 17v4m-3 0h6",
    stop: "M7 7h10v10H7z",
    arrow: "M5 12h14m-6-6 6 6-6 6",
    back: "M19 12H5m6-6-6 6 6 6",
    system: "m12 3 8 5v8l-8 5-8-5V8l8-5Zm0 0v18M4 8l8 5 8-5",
    history: "M4 11a8 8 0 1 1 2 6M4 5v6h6m2-5v6l4 2",
    keyboard: "M3 6h18v12H3V6Zm4 4h.01M11 10h.01M15 10h.01M18 10h.01M7 14h10",
    close: "m6 6 12 12M18 6 6 18",
    plus: "M12 5v14M5 12h14",
    sound: "M4 10h4l5-4v12l-5-4H4v-4Zm12-2c3 2 3 6 0 8m3-11c5 4 5 10 0 14",
    check: "m5 12 4 4L19 6",
    pause: "M9 6v12M15 6v12",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={path[name]} />
    </svg>
  );
}
