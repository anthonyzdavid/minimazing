// Original, hand-drawn line icons (not traced from any app's asset files).
// Simple 24x24 stroke glyphs, stroke="currentColor" so CSS controls color.
const ICON_SHELL = (inner) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ` +
  `stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;

const ICONS = {
  logo: ICON_SHELL(`<rect x="7" y="2.5" width="10" height="19" rx="2.3"/>
    <line x1="10" y1="19" x2="14" y2="19"/>`),

  device: ICON_SHELL(`<rect x="7" y="2" width="10" height="20" rx="2.2"/>
    <line x1="10" y1="18.4" x2="14" y2="18.4"/>`),

  messages: ICON_SHELL(`<path d="M4 5.5A2 2 0 0 1 6 3.5h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-4 3.5v-3.5H6a2 2 0 0 1-2-2z"/>`),

  whatsapp: ICON_SHELL(`<path d="M4 5.5A2 2 0 0 1 6 3.5h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-4 3.5v-3.5H6a2 2 0 0 1-2-2z"/>
    <circle cx="9" cy="10" r=".9" fill="currentColor" stroke="none"/>
    <circle cx="13" cy="10" r=".9" fill="currentColor" stroke="none"/>
    <circle cx="17" cy="10" r=".9" fill="currentColor" stroke="none"/>`),

  contacts: ICON_SHELL(`<circle cx="12" cy="8.3" r="3.3"/>
    <path d="M5 20c0-3.6 3.1-6.2 7-6.2s7 2.6 7 6.2"/>`),

  calls: ICON_SHELL(`<path d="M5.5 4.5h3l1.3 4-2 1.6a12 12 0 0 0 5.6 5.6l1.6-2 4 1.3v3a1.5 1.5 0 0 1-1.6 1.5A15.5 15.5 0 0 1 4 5.6 1.5 1.5 0 0 1 5.5 4.5z"/>`),

  calendar: ICON_SHELL(`<rect x="3.5" y="5" width="17" height="15.5" rx="2"/>
    <line x1="3.5" y1="9.5" x2="20.5" y2="9.5"/>
    <line x1="8" y1="3" x2="8" y2="7"/>
    <line x1="16" y1="3" x2="16" y2="7"/>`),

  notes: ICON_SHELL(`<rect x="4.5" y="3.5" width="15" height="17" rx="2"/>
    <line x1="8" y1="8.5" x2="16" y2="8.5"/>
    <line x1="8" y1="12.5" x2="16" y2="12.5"/>
    <line x1="8" y1="16.5" x2="13" y2="16.5"/>`),

  safari: ICON_SHELL(`<circle cx="12" cy="12" r="9"/>
    <path d="M15.5 8.5 13 13l-4.5 2.5L11 11z" fill="currentColor" stroke="none"/>`),

  photos: ICON_SHELL(`<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>
    <circle cx="8.5" cy="9.5" r="1.6"/>
    <path d="M4 17.5 9 12l3.5 3.5L16 11l4.5 5.5"/>`),

  voicememos: ICON_SHELL(`<rect x="9" y="3" width="6" height="11" rx="3"/>
    <path d="M6 11a6 6 0 0 0 12 0"/>
    <line x1="12" y1="17" x2="12" y2="21"/>
    <line x1="8.5" y1="21" x2="15.5" y2="21"/>`),

  music: ICON_SHELL(`<circle cx="7" cy="18" r="2.3"/>
    <circle cx="17" cy="16.5" r="2.3"/>
    <path d="M9.3 18V5.5L19.3 4v12.5"/>`),

  apps: ICON_SHELL(`<rect x="4" y="4" width="6.5" height="6.5" rx="1.4"/>
    <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.4"/>
    <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.4"/>
    <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.4"/>`),

  files: ICON_SHELL(`<path d="M4 6.5a1.5 1.5 0 0 1 1.5-1.5h4l2 2.2h7a1.5 1.5 0 0 1 1.5 1.5v9.3a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z"/>`),

  voicemail: ICON_SHELL(`<circle cx="7" cy="12" r="3.5"/><circle cx="17" cy="12" r="3.5"/>
    <line x1="7" y1="15.5" x2="17" y2="15.5"/>`),

  video: ICON_SHELL(`<rect x="3.5" y="6" width="12" height="12" rx="2"/>
    <path d="M15.5 10.5 20.5 7.5v9l-5-3z"/>`),

  file: ICON_SHELL(`<path d="M6.5 3.5h7l4 4v13h-11z"/><path d="M13.5 3.5v4h4"/>`),

  search: ICON_SHELL(`<circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.3" y1="15.3" x2="20" y2="20"/>`),

  lock: ICON_SHELL(`<rect x="5.5" y="10.5" width="13" height="9" rx="1.8"/>
    <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>`),

  download: ICON_SHELL(`<path d="M12 3.5v12"/><path d="M7.5 11.5 12 16l4.5-4.5"/>
    <line x1="4.5" y1="20" x2="19.5" y2="20"/>`),
};

function icon(key, cls = "") {
  return `<span class="icon ${cls}">${ICONS[key] || ""}</span>`;
}
