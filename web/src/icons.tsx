/**
 * The three glyphs the file tree needs.
 *
 * Drawn rather than typed: an emoji is a picture of someone else's icon set,
 * rendered at whatever weight and colour the platform feels like, and it does
 * not follow the theme. These are a stroke of currentColor, so they sit at the
 * same weight as the text beside them in either theme.
 */

const box = {
  width: 14,
  height: 14,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function FolderIcon({ open = false }: { open?: boolean }) {
  return (
    <svg {...box} className="icon">
      {open ? (
        <path d="M2 12.5V4.5a1 1 0 0 1 1-1h3l1.5 1.5H13a1 1 0 0 1 1 1V7M2 12.5 3.6 7.6a1 1 0 0 1 .95-.7h9.2a1 1 0 0 1 .95 1.3l-1.35 4.3H2Z" />
      ) : (
        <path d="M2 12.5v-8a1 1 0 0 1 1-1h3l1.5 1.5H13a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1Z" />
      )}
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg {...box} className="icon">
      <path d="M9 1.75H4.5a1 1 0 0 0-1 1v10.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V5.25L9 1.75Z" />
      <path d="M9 1.75v3.5h3.5" />
    </svg>
  );
}

/** The whole board, one level above the semesters. */
export function BoardIcon() {
  return (
    <svg {...box} className="icon">
      <path d="M2.5 3.5h11M2.5 8h11M2.5 12.5h11" />
    </svg>
  );
}
