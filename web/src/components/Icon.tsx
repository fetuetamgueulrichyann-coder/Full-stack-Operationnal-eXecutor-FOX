const PATHS = {
  chat: "M4 5h16v11H9l-5 4z",
  agenda: "M5 6h14v13H5zM5 10h14M9 4v4M15 4v4",
  tasks: "M5 5h14v14H5zM8.5 12l2.5 2.5 4.5-5",
  quotes: "M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6",
  privacy: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z",
  settings: "M4 7h10M18 7h2M4 17h2M10 17h10M16 4v6M8 14v6",
  plus: "M12 5v14M5 12h14",
  send: "M4 12l16-8-6 16-3-7z",
  arrow: "M5 12h14M13 6l6 6-6 6",
  user: "M4 21c1-4 4-6 8-6s7 2 8 6",
  lock: "M8 11V8a4 4 0 0 1 8 0v3",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z",
  eyeOff: "M3 3l18 18M10.6 6.2A9.7 9.7 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.4 7.5A17 17 0 0 0 2 12s4 7 10 7a9.7 9.7 0 0 0 4-.9",
  key: "M11 12l9-9M16 7l3 3",
  alert: "M12 7v6M12 16.5v.01",
  list: "M4 6h16M4 12h16M4 18h16",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  site: "M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6",
  mic: "M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3",
  speaker: "M11 5 6 9H3v6h3l5 4zM15.5 9a4 4 0 0 1 0 6M18.5 6a8 8 0 0 1 0 12",
  wave: "M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4",
  stop: "M6 6h12v12H6z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
      {name === "user" && <circle cx="12" cy="8" r="4" />}
      {name === "lock" && <rect x="5" y="11" width="14" height="9" rx="2" />}
      {name === "eye" && <circle cx="12" cy="12" r="3" />}
      {name === "key" && <circle cx="8" cy="15" r="4" />}
      {name === "alert" && <circle cx="12" cy="12" r="9" />}
    </svg>
  );
}
