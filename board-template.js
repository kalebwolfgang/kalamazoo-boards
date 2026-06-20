/* ═══════════════════════════════════════════════════════════════
   board-template.js
   Runtime engine for all board detail pages.

   Depends on:  styles.css, BOARD config object (inline in HTML)
   Runs on:     board-*.html pages after Task 9 thin-format conversion
   Does NOT run on fat-format board pages (those have their own inline JS)

   Initialization order:
     1. CSS variable fallback
     2. Analytics injection
     3. Gov strip render
     4. Accordion handlers + deep link
     5. Custom scrollbars
     6. Tooltip system
     7. Parallel async fetches: content, meeting data, last updated
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ─────────────────────────────────────────────────────────────
   ANALYTICS CONFIG
   Change these two constants to swap analytics providers
   across all 25+ board pages in a single edit.
   ───────────────────────────────────────────────────────────── */
const ANALYTICS_TOKEN  = '2d2238a3d3d7465c86da4cd5a0854e8e';  
const ANALYTICS_SRC    = 'https://static.cloudflareinsights.com/beacon.min.js';
const UMAMI_WEBSITE_ID = '507d2340-9a98-4f50-848e-14ac20c833ad';
const UMAMI_SCRIPT_URL = 'https://cloud.umami.is/script.js';

/* ─────────────────────────────────────────────────────────────
   PWA CONFIG — Task 16 placeholder
   Define PWA_TAGS here and inject in init() once Task 16 is built.
   ───────────────────────────────────────────────────────────── */
// TODO Task 16:
// const PWA_MANIFEST = '/kalamazoo-boards/manifest.json';
// const PWA_THEME    = '#0d2b3e';
// function injectPWATags() { ... }

/* ─────────────────────────────────────────────────────────────
   SHARED CONSTANTS
   ───────────────────────────────────────────────────────────── */
const MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTHS_LONG  = ['January','February','March','April','May','June',
                      'July','August','September','October','November','December'];

/* Shared SVG icons — defined here so template strings can reference them */
const SVG_EXT = `<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
const SVG_CAL = `<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="12" y1="14" x2="12" y2="18"/><line x1="10" y1="16" x2="14" y2="16"/></svg>`;
const SVG_DOC = `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;
const SVG_PLY = `<svg width="20" height="20" fill="white" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;

/* Module-level state for disclaimer popup */
let pendingCalendarAction = null;


/* ═══════════════════════════════════════════════════════════════
   INIT — runs synchronously on script load
   All called functions are hoisted declarations below.
   ═══════════════════════════════════════════════════════════════ */
(function init() {
  if (typeof BOARD === 'undefined') {
    console.error('board-template.js: BOARD config not found on this page.');
    return;
  }

  const { abbr, color, bodyType = 'appointed' } = BOARD;

  /* 1. CSS variable fallback — if build pipeline didn't inject it */
  const existing = getComputedStyle(document.documentElement)
    .getPropertyValue('--board-color').trim();
  if (!existing) {
    document.documentElement.style.setProperty('--board-color', color);
  }

  /* 2. Analytics */
  injectAnalytics();

  /* 3. TODO Task 16: injectPWATags(); */

  /* 4. Gov strip + synchronous page sections */
  renderGovStrip();
  renderApplyToServe();
  renderSpecialCta();
  renderMembersSubhead();
  renderStaffLiaison();
  renderDocuments();
  renderExternalLinks();

  /* 5. Accordion handlers (must run before deep link) */
  initAccordions();
  handleDeepLink();

  /* 6. Custom scrollbars */
  initScrollbars();

  /* 7. Tooltip */
  initTooltip();

  /* 8. Disclaimer popup */
  injectDisclaimerPopup();

  /* 9. Add-to-Calendar click delegation */
  document.addEventListener('click', handleCalendarClick);

  /* 10. Parallel async fetches */
  fetchContent(abbr, bodyType);
  fetchMeetingData(abbr);
  fetchLastUpdated();
})();

/* ═══════════════════════════════════════════════════════════════
   MEMBERS SUBHEAD + VACANCY ALERT
   Reads BOARD.membersSubhead and injects below the members heading.
   Counts vacant dots from build.py output and shows alert if > 0.
   ═══════════════════════════════════════════════════════════════ */
function renderMembersSubhead() {
  const inner = document.querySelector('.members-inner');
  if (!inner) return;
  const heading = inner.querySelector('.members-heading');
  if (!heading) return;

  if (BOARD.membersSubhead) {
    const sub = document.createElement('p');
    sub.className = 'members-subhead';
    sub.textContent = BOARD.membersSubhead;
    heading.insertAdjacentElement('afterend', sub);
  }

  const vacantCount = document.querySelectorAll('.seat-dot-hdr[data-termcls="vacant"]').length;
  if (vacantCount > 0) {
    const alert = document.createElement('div');
    alert.className = 'vacancy-alert';
    alert.innerHTML = `<strong>${vacantCount} open seat${vacantCount > 1 ? 's' : ''}</strong> — <a href="#apply-to-serve">apply now</a>`;
    const insertAfter = inner.querySelector('.members-subhead') || heading;
    insertAfter.insertAdjacentElement('afterend', alert);
  }
}


/* ═══════════════════════════════════════════════════════════════
   STAFF LIAISON
   Renders 0, 1, or 2 liaison blocks after .members-section.
   Shows board email below liaison block if BOARD.boardEmail is set.
   ═══════════════════════════════════════════════════════════════ */
function renderStaffLiaison() {
  const liaisons = Array.isArray(BOARD.staffLiaison)
    ? BOARD.staffLiaison
    : (BOARD.staffLiaison ? [BOARD.staffLiaison] : []);
  if (!liaisons.length) return;

  const membersSection = document.querySelector('.members-section');
  if (!membersSection) return;

  const icon = `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="flex-shrink:0;color:var(--navy-light);margin-top:2px"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;

  const rows = liaisons.map(l => `
    <div class="staff-liaison-row">
      <span class="staff-liaison-name">${l.name}</span>
      ${l.title ? `<span class="staff-liaison-role">${l.title}</span>` : ''}
      ${l.email ? `<span class="staff-liaison-email"><a href="mailto:${l.email}">${l.email}</a></span>` : ''}
    </div>
    ${l.note ? `<div style="font-size:13px;color:var(--muted);margin-top:4px;line-height:1.5">${l.note}</div>` : ''}
  `).join('');

  const boardEmailHtml = BOARD.boardEmail
    ? `<div style="margin-top:8px;font-size:13px">Board email: <a href="mailto:${BOARD.boardEmail}" style="color:var(--navy-light)">${BOARD.boardEmail}</a></div>`
    : '';

  const wrap = document.createElement('div');
  wrap.className = 'staff-note-wrap';
  wrap.innerHTML = `<div class="staff-note">${icon}<div><strong>Staff Liaison${liaisons.length > 1 ? 's' : ''}:</strong><div class="staff-liaisons">${rows}</div>${boardEmailHtml}</div></div>`;

  membersSection.insertAdjacentElement('afterend', wrap);
}


/* ═══════════════════════════════════════════════════════════════
   KEY DOCUMENTS CARD
   Appends a Key Documents card to .bottom-cards-grid.
   Skipped for ECC, HDC, HPC — those have static Key Documents
   in the sidebar of their thin HTML.
   ═══════════════════════════════════════════════════════════════ */
function renderDocuments() {
  const docs = BOARD.documents;
  if (!docs || !docs.length) return;
  if (['ECC', 'HDC', 'HPC'].includes(BOARD.abbr)) return;

  const grid = document.querySelector('.bottom-cards');
  if (!grid) return;

  const card = document.createElement('div');
  card.className = 'bottom-card';
  card.innerHTML = `
    <div class="bottom-card-header">
      ${SVG_DOC} Key Documents
    </div>
    <div class="bottom-card-body">
      ${docs.map(d => `
        <a class="doc-link" href="${d.href}" target="_blank" rel="noopener">
          ${SVG_DOC}
          <span>${d.text}</span>
          ${d.pdf ? '<span class="pdf-badge">PDF</span>' : ''}
        </a>`).join('')}
    </div>`;

  grid.appendChild(card);
}


/* ═══════════════════════════════════════════════════════════════
   EXTERNAL LINKS CARD
   Appends an External Resources card to .sidebar.
   Currently only CPSRAB has external links, but universal.
   ═══════════════════════════════════════════════════════════════ */
function renderExternalLinks() {
  const links = BOARD.externalLinks;
  if (!links || !links.length) return;

  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;

  const card = document.createElement('div');
  card.className = 'sidebar-card';
  card.innerHTML = `
    <div class="sidebar-card-header">External Resources</div>
    <div class="sidebar-card-body">
      ${links.map(l => `
        <div class="ext-link-item">
          <a class="ext-link-url" href="${l.href}" target="_blank" rel="noopener">
            ${l.text} ${SVG_EXT}
          </a>
          ${l.sub ? `<div class="ext-link-sub">${l.sub}</div>` : ''}
        </div>`).join('')}
    </div>`;

  sidebar.appendChild(card);
}


/* ═══════════════════════════════════════════════════════════════
   APPLY TO SERVE SECTION
   Injects before .members-section.
   Skipped when BOARD.hasApply is false (EC, TRB).
   Reserves a hidden .subscribe-slot for Task 20.
   ═══════════════════════════════════════════════════════════════ */
function renderApplyToServe() {
  if (!BOARD.hasApply) return;

  const membersSection = document.querySelector('.members-section');
  if (!membersSection) return;

  const section = document.createElement('div');
  section.className = 'apply-section';
  section.id = 'apply-to-serve';
  section.innerHTML = `
    <div class="apply-standalone-inner">
      <div class="apply-standalone-text">
        <div class="apply-standalone-heading">Apply to Serve</div>
        <p class="apply-standalone-desc">${BOARD.applyText || ''}</p>
      </div>
      <div>
        <a class="apply-standalone-btn"
           href="https://www.kalamazoocity.org/Government/Boards-Commissions/Apply-to-Join-a-Board-or-Commission"
           target="_blank" rel="noopener">Apply Now →</a>
        <div class="subscribe-slot" style="display:none;margin-top:12px"></div>
      </div>
    </div>`;

  if (BOARD.subscribeEnabled) {
    section.querySelector('.subscribe-slot').style.display = '';
    /* TODO Task 20: populate subscribe flow */
  }

  membersSection.insertAdjacentElement('beforebegin', section);
}


/* ═══════════════════════════════════════════════════════════════
   SPECIAL CTA SECTION
   Injects before #apply-to-serve (or .members-section if no apply).
   CRB has btnUrl: null — renders section without a button.
   type maps to visual treatment via .special-cta--{type} in styles.css.
   ═══════════════════════════════════════════════════════════════ */
function renderSpecialCta() {
  const cta = BOARD.specialCta;
  if (!cta) return;

  const target = document.getElementById('apply-to-serve')
    || document.querySelector('.members-section');
  if (!target) return;

  const section = document.createElement('div');
  section.className = `special-cta special-cta--${cta.type || 'default'}`;
  section.innerHTML = `
    <div class="special-cta-inner">
      <div>
        ${cta.eyebrow ? `<div class="special-cta-eyebrow">${cta.eyebrow}</div>` : ''}
        <div class="special-cta-heading">${cta.heading}</div>
        <p class="special-cta-desc">${cta.desc}</p>
      </div>
      ${cta.btnUrl
        ? `<a class="special-cta-btn" href="${cta.btnUrl}" target="_blank" rel="noopener">${cta.btnText || 'Learn More →'}</a>`
        : ''}
    </div>`;

  target.insertAdjacentElement('beforebegin', section);
}

/* ═══════════════════════════════════════════════════════════════
   ANALYTICS INJECTION
   ═══════════════════════════════════════════════════════════════ */
function injectAnalytics() {
  const cf = document.createElement('script');
  cf.defer = true;
  cf.src   = ANALYTICS_SRC;
  cf.setAttribute('data-cf-beacon', JSON.stringify({ token: ANALYTICS_TOKEN }));
  document.head.appendChild(cf);

  const um = document.createElement('script');
  um.defer = true;
  um.src   = UMAMI_SCRIPT_URL;
  um.setAttribute('data-website-id', UMAMI_WEBSITE_ID);
  document.head.appendChild(um);
}


/* ═══════════════════════════════════════════════════════════════
   GOV STRIP
   Renders BOARD.govStrip array into .gov-inner.
   build.py leaves .gov-inner empty; board-template.js fills it.
   ═══════════════════════════════════════════════════════════════ */
function renderGovStrip() {
  const inner = document.querySelector('.gov-inner');
  if (!inner || !Array.isArray(BOARD.govStrip) || !BOARD.govStrip.length) return;

  const items = [...BOARD.govStrip];

  /* Slot 4 — Seat Status — computed from dot grid injected by build.py */
  if (BOARD.bodyType !== 'elected') {
    const nVacant   = document.querySelectorAll('.seat-dot-hdr.vacant').length;
    const nHoldover = document.querySelectorAll('.seat-dot-hdr.holdover').length;
    const nTrans    = document.querySelectorAll('.seat-dot-hdr.transitioning').length;
    let slot4, color;
    if (nVacant > 0) {
      slot4 = `${nVacant} Open Seat${nVacant > 1 ? 's' : ''}`;
      color = '#f87171';
    } else if (nHoldover > 0) {
      slot4 = `${nHoldover} In Holdover`;
      color = '#f87171';
    } else if (nTrans > 0) {
      slot4 = `${nTrans} Transitioning`;
      color = 'var(--gold-lt)';
    } else {
      slot4 = '\u2713 Fully Seated';
      color = '#86efac';
    }
    items.push({ value: slot4, label: 'Seat Status', _color: color });
  }

  inner.innerHTML = items
    .map(item => `
      <div class="gov-item">
        <div class="gov-value"${item._color ? ` style="color:${item._color}"` : ''}>${item.value}</div>
        <div class="gov-label">${item.label}</div>
      </div>`)
    .join('');
}


/* ═══════════════════════════════════════════════════════════════
   ACCORDION SYSTEM
   build.py renders accordion shells with aria-expanded="false"
   and hidden panels. board-template.js attaches toggle handlers.
   Multiple accordions may be open simultaneously.
   ═══════════════════════════════════════════════════════════════ */
function initAccordions() {
  document.querySelectorAll('.acc-trigger').forEach(btn => {
    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      const next     = !expanded;
      btn.setAttribute('aria-expanded', String(next));
      btn.nextElementSibling.hidden = !next;

      /* Sync URL hash with open accordion */
      const item = btn.closest('.acc-item');
      if (item?.id) {
        if (next) {
          history.replaceState(null, '', '#' + item.id);
        } else if (window.location.hash === '#' + item.id) {
          history.replaceState(null, '', window.location.pathname + window.location.search);
        }
      }
    });
  });
}

function handleDeepLink() {
  if (!window.location.hash) return;
  const id   = window.location.hash.slice(1);
  const item = document.getElementById(id);
  if (!item) return;

  const trigger = item.querySelector('.acc-trigger');
  const panel   = item.querySelector('.acc-panel');
  if (!trigger || !panel) return;

  trigger.setAttribute('aria-expanded', 'true');
  panel.hidden = false;

  /* Smooth scroll after a tick so layout has settled */
  setTimeout(() => {
    const top = item.getBoundingClientRect().top + window.scrollY - 72;
    window.scrollTo({ top, behavior: 'smooth' });
  }, 100);
}


/* ═══════════════════════════════════════════════════════════════
   CUSTOM SCROLLBARS
   Vertical:   minutes card, recordings card
   Horizontal: members table
   Both hidden on touch devices — native scroll is used instead.
   ═══════════════════════════════════════════════════════════════ */
function initScrollbars() {
  [
    { scrollId: 'scroll-minutes',    wrapId: 'wrap-minutes'    },
    { scrollId: 'scroll-recordings', wrapId: 'wrap-recordings' },
  ].forEach(({ scrollId, wrapId }) => {
    const el   = document.getElementById(scrollId);
    const wrap = document.getElementById(wrapId);
    if (el && wrap) initVerticalScrollbar(el, wrap);
  });

  const tableWrap  = document.getElementById('members-table-wrap');
  const tableOuter = document.getElementById('members-table-outer');
  if (tableWrap && tableOuter) initHorizontalScrollbar(tableWrap, tableOuter);
}

function initVerticalScrollbar(el, wrap) {
  /* Skip on touch devices */
  if (window.matchMedia('(pointer: coarse)').matches) return;

  const track = document.createElement('div'); track.className = 'scroll-track';
  const thumb = document.createElement('div'); thumb.className = 'scroll-thumb';
  track.appendChild(thumb);
  wrap.appendChild(track);

  function update() {
    const { scrollTop, scrollHeight, clientHeight } = el;
    const scrollable = scrollHeight - clientHeight;
    if (scrollable <= 0) { track.style.opacity = '0'; wrap.classList.add('at-end'); return; }
    track.style.opacity = '1';
    const thumbPct = clientHeight / scrollHeight;
    const trackH   = track.offsetHeight;
    const thumbH   = Math.max(24, Math.round(thumbPct * trackH));
    thumb.style.height = thumbH + 'px';
    thumb.style.top    = Math.round((scrollTop / scrollable) * (trackH - thumbH)) + 'px';
    wrap.classList.toggle('at-end', scrollTop + clientHeight >= scrollHeight - 4);
  }

  el.addEventListener('scroll', update, { passive: true });
  new ResizeObserver(update).observe(el);
  update();

  /* Expose so content renders can trigger a refresh */
  el._refreshScrollbar = update;
}

function initHorizontalScrollbar(wrap, outer) {
  const track = document.createElement('div'); track.className = 'h-scroll-track';
  const thumb = document.createElement('div'); thumb.className = 'h-scroll-thumb';
  track.appendChild(thumb);
  outer.before(track);

  function update() {
    const { scrollLeft, scrollWidth, clientWidth } = wrap;
    const maxScroll = scrollWidth - clientWidth;
    if (maxScroll <= 0) { track.style.display = 'none'; return; }
    track.style.display = '';
    const thumbPct = clientWidth / scrollWidth;
    const trackW   = track.offsetWidth;
    const thumbW   = Math.max(32, Math.round(thumbPct * trackW));
    thumb.style.width = thumbW + 'px';
    thumb.style.left  = Math.round((scrollLeft / maxScroll) * (trackW - thumbW)) + 'px';
  }

  wrap.addEventListener('scroll', update, { passive: true });
  new ResizeObserver(update).observe(wrap);
  update();

  /* Prevent rubber-band overscroll at horizontal boundaries on iOS */
  let startX = 0, startY = 0, isHoriz = null;
  wrap.addEventListener('touchstart', e => {
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    isHoriz = null;
  }, { passive: true });
  wrap.addEventListener('touchmove', e => {
    const dx = e.touches[0].clientX - startX;
    const dy = e.touches[0].clientY - startY;
    if (isHoriz === null && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
      isHoriz = Math.abs(dx) > Math.abs(dy);
    }
    if (!isHoriz) return;
    const { scrollLeft, scrollWidth, clientWidth } = wrap;
    const atLeft  = scrollLeft <= 0;
    const atRight = scrollLeft >= scrollWidth - clientWidth - 1;
    if ((atLeft && dx > 0) || (atRight && dx < 0)) e.preventDefault();
  }, { passive: false });
}


/* ═══════════════════════════════════════════════════════════════
   TOOLTIP SYSTEM
   Desktop: follows cursor on .seat-dot-hdr hover.
            Flips left if within 180px of right edge.
            Flips up if within 80px of bottom.
   Mobile:  bottom sheet slides up on tap.
            Closes on tap outside or X button.
   Data attributes set by build.py on each .seat-dot-hdr:
     data-name    — member name or "Open Seat"
     data-role    — member role / position title
     data-term    — formatted term text
     data-termcls — CSS class: vacant | holdover | transitioning | seated
     data-res     — residency requirement
   ═══════════════════════════════════════════════════════════════ */
function initTooltip() {
  const tooltip = document.getElementById('tooltip');
  if (!tooltip) return;

  const ttName  = document.getElementById('tt-name');
  const ttRole  = document.getElementById('tt-role');
  const ttTerm  = document.getElementById('tt-term');
  const ttRes   = document.getElementById('tt-res');
  const ttClose = document.getElementById('tooltip-close');
  let lastDot   = null;
  const isTouch = () => window.matchMedia('(pointer: coarse)').matches;

  function populate(el) {
    if (ttName) ttName.textContent = el.dataset.name || 'Open Seat';
    if (ttRole) {
      ttRole.textContent   = el.dataset.role || '';
      ttRole.style.display = el.dataset.role ? '' : 'none';
    }
    if (ttTerm) {
      ttTerm.textContent = el.dataset.term || '';
      ttTerm.className   = `tt-term ${el.dataset.termcls || ''}`;
    }
    if (ttRes) {
      ttRes.textContent   = el.dataset.res ? `Residency: ${el.dataset.res}` : '';
      ttRes.style.display = el.dataset.res ? '' : 'none';
    }
  }

  /* Desktop — follow cursor */
  document.addEventListener('mousemove', e => {
    if (isTouch()) return;
    const dot = e.target?.classList?.contains('seat-dot-hdr') ? e.target : null;
    if (!dot) { tooltip.classList.remove('visible'); lastDot = null; return; }
    if (dot !== lastDot) { lastDot = dot; populate(dot); }
    tooltip.classList.add('visible');

    let x = e.clientX + 16, y = e.clientY + 16;
    const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
    if (x + tw > window.innerWidth  - 8) x = e.clientX - tw - 16;
    if (y + th > window.innerHeight - 8) y = e.clientY - th - 16;
    tooltip.style.left = x + 'px';
    tooltip.style.top  = y + 'px';
  });

  document.addEventListener('mouseleave', () => {
    if (isTouch()) return;
    tooltip.classList.remove('visible');
    lastDot = null;
  });

  /* Mobile — bottom sheet tap */
  document.addEventListener('click', e => {
    if (!isTouch()) return;
    const dot = e.target?.classList?.contains('seat-dot-hdr') ? e.target : null;
    if (dot) {
      populate(dot);
      tooltip.classList.add('visible');
      e.stopPropagation();
    } else if (!tooltip.contains(e.target)) {
      tooltip.classList.remove('visible');
    }
  });

  /* Keyboard */
  document.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target?.classList?.contains('seat-dot-hdr')) {
      e.preventDefault();
      populate(e.target);
      tooltip.classList.add('visible');
    }
    if (e.key === 'Escape') tooltip.classList.remove('visible');
  });

  if (ttClose) ttClose.addEventListener('click', () => tooltip.classList.remove('visible'));
}


/* ═══════════════════════════════════════════════════════════════
   SEAT STATUS UTILITIES
   Must stay logically consistent with the Python equivalent
   in build.py. If the logic changes, change both files.
   Only called for bodyType: "appointed" boards.
   ═══════════════════════════════════════════════════════════════ */
function seatStatus(member) {
  const today   = new Date();
  const sixMo   = new Date(); sixMo.setMonth(sixMo.getMonth() + 6);
  const termEnd = new Date(member.termEnd);
  if (member.isVacant)  return 'vacant';
  if (termEnd < today)  return 'holdover';
  if (termEnd < sixMo)  return 'transitioning';
  return 'seated';
}

function fmtDate(iso) {
  if (!iso || iso === '2100-01-01') return null;
  const [y, m, d] = iso.split('-');
  return `${MONTHS_SHORT[+m - 1]} ${+d}, ${y}`;
}


/* ═══════════════════════════════════════════════════════════════
   CONTENT FETCH — content/{abbr}.json
   Populates accordion bodies, How to Join / How to Run section,
   and Public Comment Guide.
   On failure: injects a brief error note into empty panels.
               Page structure stays intact — no broken layout.
   ═══════════════════════════════════════════════════════════════ */
function fetchContent(abbr, bodyType) {
  fetch(`content/${abbr.toLowerCase()}.json`)
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(content => {
      renderAccordionBodies(content.accordions || []);
      renderHowToSection(content, bodyType);
      renderPublicCommentGuide(content.publicCommentGuide);
    })
    .catch(() => {
      /* Leave accordion shells visible but flag empty ones */
      document.querySelectorAll('.acc-panel-inner').forEach(panel => {
        if (!panel.textContent.trim()) {
          panel.innerHTML =
            '<p style="color:var(--muted);font-size:15px;font-style:italic;line-height:1.6">' +
            'Content temporarily unavailable.</p>';
        }
      });
    });
}

function renderAccordionBodies(accordions) {
  accordions.forEach(acc => {
    if (!acc.anchor) return;
    const item = document.getElementById(acc.anchor);
    if (!item) return;
    const panel = item.querySelector('.acc-panel-inner');
    if (!panel || panel.textContent.trim()) return; /* skip if already populated */

    panel.innerHTML = renderBodyItems(acc.body || []);

    /* Open if flagged in content file */
    if (acc.open) {
      const trigger  = item.querySelector('.acc-trigger');
      const accPanel = item.querySelector('.acc-panel');
      if (trigger)  trigger.setAttribute('aria-expanded', 'true');
      if (accPanel) accPanel.hidden = false;
    }
  });
}

/*
 * Renders a body items array into HTML.
 * Supported types:
 *   paragraph — { type: "paragraph", text: "..." }
 *   list      — { type: "list", items: ["...", "..."] }
 *   field     — { type: "field", label: "...", value: "..." }
 *
 * Consecutive field items are automatically grouped into a .req-grid.
 */
function renderBodyItems(items) {
  const out        = [];
  let   fieldGroup = [];

  function flushFields() {
    if (!fieldGroup.length) return;
    out.push(`<div class="req-grid">${fieldGroup.join('')}</div>`);
    fieldGroup = [];
  }

  (items || []).forEach(item => {
    if (item.type === 'paragraph') {
      flushFields();
      out.push(`<p>${item.text}</p>`);
    } else if (item.type === 'list') {
      flushFields();
      out.push(`<ul>${(item.items || []).map(li => `<li>${li}</li>`).join('')}</ul>`);
    } else if (item.type === 'heading') {
      flushFields();
      out.push(`<h4>${item.text}</h4>`);
    } else if (item.type === 'field') {
      fieldGroup.push(
        `<div class="req-card">` +
        `<div class="req-card-label">${item.label}</div>` +
        `<div class="req-card-value">${item.value}</div>` +
        `</div>`
      );
    }
  });

  flushFields();
  return out.join('');
}

/*
 * Renders How to Join (appointed) or How to Run (elected)
 * into the accordion with the matching anchor id.
 * Anchor convention: "how-to-join" | "how-to-run"
 */
function renderHowToSection(content, bodyType) {
  const isElected = bodyType === 'elected';
  const anchor    = isElected ? 'how-to-run' : 'how-to-join';
  const data      = isElected ? content.howToRun : content.howToJoin;
  if (!data) return;

  const item  = document.getElementById(anchor);
  if (!item) return;
  const panel = item.querySelector('.acc-panel-inner');
  if (!panel) return;

  let fields = [];

  if (isElected) {
    /* howToRun fields */
    fields = [
      data.electionCycle            && { label: 'Election Cycle',      value: data.electionCycle },
      data.nextElectionDate         && { label: 'Next Election',        value: data.nextElectionDate },
      data.candidateFilingDeadline  && { label: 'Filing Deadline',      value: data.candidateFilingDeadline },
      data.filingRequirements       && { label: 'Filing Requirements',  value: data.filingRequirements },
      data.qualifyingPetition       && { label: 'Qualifying Petition',  value: data.qualifyingPetition },
      data.electionsInfoUrl         && {
        label: 'More Information',
        value: `<a href="${data.electionsInfoUrl}" target="_blank" rel="noopener">City Elections Page</a>`
      },
    ].filter(Boolean);
  } else {
    /* howToJoin fields */
    fields = [
      data.appointingAuthority && { label: 'Appointing Authority', value: data.appointingAuthority },
      data.eligibility         && { label: 'Eligibility',          value: data.eligibility },
      data.residency           && { label: 'Residency',            value: data.residency },
      data.meetingFrequency    && { label: 'Meeting Frequency',    value: data.meetingFrequency },
      data.timeCommitment      && { label: 'Time Commitment',      value: data.timeCommitment },
    ].filter(Boolean);
  }

  if (fields.length) {
    panel.innerHTML +=
      `<div class="req-grid">` +
      fields.map(f =>
        `<div class="req-card">` +
        `<div class="req-card-label">${f.label}</div>` +
        `<div class="req-card-value">${f.value}</div>` +
        `</div>`
      ).join('') +
      `</div>`;
  }
}

/*
 * Renders the Public Comment Guide into the accordion
 * with id="public-comment".
 */
function renderPublicCommentGuide(guide) {
  if (!guide) return;
  const item  = document.getElementById('public-comment');
  if (!item) return;
  const panel = item.querySelector('.acc-panel-inner');
  if (!panel) return;

  const rows = [
    guide.location  && { label: 'Location',   value: guide.location },
    guide.remote    && { label: 'Remote',      value: guide.remote },
    guide.signUp    && { label: 'Sign-Up',     value: guide.signUp },
    guide.timeLimit && { label: 'Time Limit',  value: guide.timeLimit },
  ].filter(Boolean);

  if (rows.length) {
    panel.innerHTML +=
      `<div class="req-grid">` +
      rows.map(r =>
        `<div class="req-card">` +
        `<div class="req-card-label">${r.label}</div>` +
        `<div class="req-card-value">${r.value}</div>` +
        `</div>`
      ).join('') +
      `</div>`;
  }
}


/* ═══════════════════════════════════════════════════════════════
   MEETING DATA FETCH — data/{abbr}.json

   On success renders:
     - Upcoming meetings in sidebar card
     - Minutes & Agendas scrollable card
     - Recordings card (only if BOARD.hasRecordings === true)

   On ANY failure (404, network, parse error):
     Removes all three cards from the DOM entirely.
     No empty cards, no broken layout, no error states.
     This is a hard requirement — several boards have no data file.
   ═══════════════════════════════════════════════════════════════ */
function fetchMeetingData(abbr) {
  fetch(`data/${abbr.toLowerCase()}.json`)
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(data => {
      renderUpcomingMeetings(data);
      renderMinutes(data);
      if (BOARD.hasRecordings) {
        renderRecordings(data);
      } else {
        /* Board doesn't have recordings — remove card shell */
        removeCard('scroll-recordings');
      }
    })
    .catch(() => {
      /* Hard requirement: remove all three cards, no error UI */
      const sidebar = document.querySelector('.sidebar .sidebar-card');
      if (sidebar) sidebar.remove();
      removeCard('scroll-minutes');
      removeCard('scroll-recordings');
    });
}

/* Removes the .bottom-card that contains a given element id */
function removeCard(childId) {
  const child = document.getElementById(childId);
  if (child) {
    const card = child.closest('.bottom-card');
    if (card) card.remove();
  }
}

/* ─────────────────────────────────────────────────────────────
   UPCOMING MEETINGS
   Supports both data formats:
     Old: { upcoming_meetings: [...] }
     New: meetings array filtered by date >= today
   Shows up to 4 meetings. First one gets "Next" badge.
   ───────────────────────────────────────────────────────────── */
function renderUpcomingMeetings(data) {
  const el = document.getElementById('upcoming-meetings-list');
  if (!el) return;

  const today = new Date(); today.setHours(0, 0, 0, 0);

  let upcoming = [];
  if (Array.isArray(data.upcoming_meetings) && data.upcoming_meetings.length) {
    upcoming = data.upcoming_meetings
      .filter(m => new Date(m.date + 'T00:00:00') >= today)
      .sort((a, b) => a.date.localeCompare(b.date));
  } else if (Array.isArray(data.meetings)) {
    upcoming = data.meetings
      .filter(m => new Date(m.date + 'T00:00:00') >= today)
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  if (!upcoming.length) {
    el.innerHTML =
      '<p style="font-size:14px;color:var(--muted);line-height:1.6">' +
      'No upcoming meetings on record. Check back soon.</p>';
    return;
  }

  /* Store for Add to Calendar handler */
  window._upcomingMeetings = upcoming;

  el.innerHTML = upcoming.slice(0, 4).map((m, i) => {
    const cancelled = m.isCancelled || m.cancelled || false;
    return `
      <div class="meeting-item${cancelled ? ' meeting-canceled' : ''}">
        ${i === 0 ? '<span class="next-badge">Next</span>' : ''}
        <div class="meeting-date">${m.display || m.date}</div>
        ${m.time     ? `<div class="meeting-time">${m.time}</div>` : ''}
        ${m.location ? `<div class="meeting-time" style="margin-top:3px;font-size:13px">${m.location}</div>` : ''}
        ${cancelled ? '<div style="margin-top:6px;font-size:11px;font-weight:700;color:#dc2626;letter-spacing:0.06em;text-transform:uppercase">Cancelled</div>' : ''}
        ${m.isSpecialSession ? '<div style="margin-top:6px;background:#fef3c7;border:1px solid #fcd34d;border-radius:3px;padding:5px 9px;font-size:11px;font-weight:700;color:#92400e;letter-spacing:0.06em;text-transform:uppercase">Special Session</div>' : ''}
        ${(m.isLocationChanged || m.locationChanged) ? '<div style="margin-top:6px;background:#fef3c7;border:1px solid #fcd34d;border-radius:3px;padding:5px 9px;font-size:11px;font-weight:700;color:#92400e;letter-spacing:0.06em;text-transform:uppercase">Location Changed</div>' : ''}
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;align-items:center">
          ${m.agenda_url && !cancelled
            ? `<a href="${m.agenda_url}" target="_blank" rel="noopener" style="font-size:13px;color:var(--navy-light);text-decoration:none;display:inline-flex;align-items:center;gap:4px">${SVG_EXT} Agenda</a>`
            : ''}
          ${!cancelled
            ? `<button class="add-cal-btn" data-date="${m.date}" style="font-size:13px;background:none;border:none;cursor:pointer;color:var(--navy-light);padding:0;display:inline-flex;align-items:center;gap:4px;font-family:inherit">${SVG_CAL} Add to Calendar</button>`
            : ''}
        </div>
      </div>`;
  }).join('');
}

/* ─────────────────────────────────────────────────────────────
   MINUTES & AGENDAS
   Shows past meetings sorted newest-first.
   Handles both isCancelled (new) and cancelled (old) field names.
   ───────────────────────────────────────────────────────────── */
function renderMinutes(data) {
  const el   = document.getElementById('scroll-minutes');
  const pill = document.getElementById('pill-meetings');
  if (!el) return;

  const today = new Date(); today.setHours(0, 0, 0, 0);

  let meetings = [];
  if (Array.isArray(data.meetings)) {
    meetings = data.meetings
      .filter(m => new Date(m.date + 'T00:00:00') < today)
      .sort((a, b) => b.date.localeCompare(a.date));
  }

  if (pill) pill.textContent = `${meetings.length} meeting${meetings.length !== 1 ? 's' : ''}`;

  if (!meetings.length) {
    el.innerHTML =
      '<div style="padding:18px;font-size:14px;color:var(--muted)">No meeting records found.</div>';
    if (el._refreshScrollbar) el._refreshScrollbar();
    return;
  }

  /* Year filter */
  const years = [...new Set(meetings.map(m => m.date.slice(0, 4)))].sort((a, b) => b - a);
  if (years.length > 1) {
    const card = el.closest('.bottom-card');
    if (card && !card.querySelector('.card-filter-bar')) {
      const bar = document.createElement('div');
      bar.className = 'card-filter-bar';
      bar.innerHTML = `
        <select class="card-filter-select" id="minutes-year-select">
          <option value="all">All years</option>
          ${years.map(y => `<option value="${y}">${y}</option>`).join('')}
        </select>
        <span class="card-filter-count" id="minutes-filter-count"></span>`;
      el.parentElement.insertBefore(bar, el);
      document.getElementById('minutes-year-select').addEventListener('change', function () {
        drawMinutes(meetings, this.value, el, document.getElementById('minutes-filter-count'));
        if (el._refreshScrollbar) el._refreshScrollbar();
      });
    }
  }

 drawMinutes(meetings, 'all', el, null);
  if (el._refreshScrollbar) el._refreshScrollbar();
}

function drawMinutes(meetings, year, el, countEl) {
  const filtered = year === 'all'
    ? meetings
    : meetings.filter(m => m.date.slice(0, 4) === year);

  if (countEl) {
    countEl.textContent = filtered.length !== meetings.length ? `${filtered.length} shown` : '';
  }

  el.innerHTML = filtered.map(m => {
    const cancelled = m.isCancelled || m.cancelled || false;
    const url       = m.minutes_url || m.agenda_url || m.url;
    const label     = m.link_label  ||
      (m.minutes_url ? 'View Minutes' : m.agenda_url ? 'View Agenda' : 'View Record');
    const iconColor = cancelled ? 'var(--muted)' : 'var(--navy-light)';
    const docIcon   = `<svg width="16" height="16" fill="none" stroke="${iconColor}" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;

    const inner = `
      <div class="minutes-icon">${docIcon}</div>
      <div class="minutes-info">
        <div class="minutes-date">${m.display || m.date}</div>
        <div class="minutes-sublabel">${BOARD.abbr}</div>
        ${!cancelled && url  ? `<div class="minutes-action">${SVG_EXT} ${label}</div>` : ''}
        ${cancelled ? '<div class="minutes-sublabel" style="color:#dc2626;font-weight:600;margin-top:3px">Cancelled</div>' : ''}
      </div>`;

    return url
      ? `<a class="minutes-item${cancelled ? ' meeting-canceled' : ''}" href="${url}" target="_blank" rel="noopener">${inner}</a>`
      : `<div class="minutes-item${cancelled ? ' meeting-canceled' : ''}">${inner}</div>`;
  }).join('')
    || `<div style="padding:18px;font-size:14px;color:var(--muted)">No records found for this year.</div>`;
}

/* ─────────────────────────────────────────────────────────────
   RECORDINGS
   Only called if BOARD.hasRecordings === true.
   If the data file has no recordings, removes the card entirely.
   Year filter dropdown is populated from actual recording dates.
   ───────────────────────────────────────────────────────────── */
function renderRecordings(data) {
  const el      = document.getElementById('scroll-recordings');
  const pill    = document.getElementById('pill-recordings');
  const yearSel = document.getElementById('recordings-year-select');
  const countEl = document.getElementById('recordings-filter-count');
  if (!el) return;

  /* Build recordings list — prefer explicit recordings field,
     fall back to past meetings with youtube data */
  let recordings = [];
  if (Array.isArray(data.recordings) && data.recordings.length) {
    recordings = data.recordings;
  } else if (Array.isArray(data.meetings)) {
    recordings = data.meetings.filter(m => m.youtube_id || m.youtube_url);
  }
  recordings.sort((a, b) => b.date.localeCompare(a.date));

  if (!recordings.length) {
    removeCard('scroll-recordings');
    return;
  }

  if (pill) pill.textContent = `${recordings.length} recording${recordings.length !== 1 ? 's' : ''}`;

  /* Populate year filter */
  const years = [...new Set(recordings.map(r => r.date.slice(0, 4)))].sort((a, b) => b - a);
  if (yearSel) {
    yearSel.innerHTML =
      `<option value="all">All years</option>` +
      years.map(y => `<option value="${y}">${y}</option>`).join('');
    yearSel.addEventListener('change', () => {
      drawRecordings(recordings, yearSel.value, el, countEl);
      if (el._refreshScrollbar) el._refreshScrollbar();
    });
  }

  drawRecordings(recordings, 'all', el, countEl);

function drawRecordings(recordings, year, el, countEl) {
  const filtered = year === 'all'
    ? recordings
    : recordings.filter(r => r.date.slice(0, 4) === year);

  if (countEl) {
    countEl.textContent =
      filtered.length !== recordings.length ? `${filtered.length} shown` : '';
  }

  el.innerHTML = filtered.map(r => {
    const url   = r.youtube_url || (r.youtube_id ? `https://youtube.com/watch?v=${r.youtube_id}` : null);
    const thumb = r.youtube_id ? `https://img.youtube.com/vi/${r.youtube_id}/mqdefault.jpg` : null;
    if (!url) return '';
    return `
      <a class="recording-item" href="${url}" target="_blank" rel="noopener">
        <div class="rec-thumb">
          ${thumb ? `<img src="${thumb}" alt="${r.display || r.date} recording" loading="lazy">` : ''}
          <div class="rec-play">${SVG_PLY}</div>
        </div>
        <div class="rec-info">
          <div class="rec-date">${r.display || r.date}</div>
          <div class="rec-sublabel">${BOARD.abbr}</div>
          <div class="rec-watch">${SVG_EXT} Watch on YouTube</div>
        </div>
      </a>`;
  }).join('') ||
  `<div style="padding:18px;font-size:14px;color:var(--muted)">No recordings found for this year.</div>`;
}


/* ═══════════════════════════════════════════════════════════════
   LAST UPDATED
   Fetches data/meta.json and appends "Data last updated: [date]"
   to the topbar. Fails silently if meta.json is unavailable.
   ═══════════════════════════════════════════════════════════════ */
function fetchLastUpdated() {
  fetch('data/meta.json')
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(meta => {
      if (!meta.lastUpdated) return;
      const d = new Date(meta.lastUpdated);
      if (isNaN(d.getTime())) return;
      const label = `${MONTHS_LONG[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
      const topbar = document.querySelector('.topbar-inner');
      if (!topbar) return;
      const span = document.createElement('span');
      span.style.cssText = 'margin-left:auto;opacity:0.6;font-size:12px';
      span.textContent   = `Data last updated: ${label}`;
      topbar.appendChild(span);
    })
    .catch(() => {}); /* Fail silently */
}


/* ═══════════════════════════════════════════════════════════════
   DISCLAIMER POPUP
   Injected into the DOM on init. Same popup as calendar.html.
   CSS lives in styles.css (.cal-disclaimer-overlay, .cal-disclaimer etc.)
   ═══════════════════════════════════════════════════════════════ */
function injectDisclaimerPopup() {
  const overlay = document.createElement('div');
  overlay.className = 'cal-disclaimer-overlay';
  overlay.id        = 'board-disclaimer-overlay';
  overlay.innerHTML = `
    <div class="cal-disclaimer">
      <div class="cal-disclaimer-icon">⚠</div>
      <p class="cal-disclaimer-text">This event won't update automatically if the meeting is cancelled or rescheduled. Always check back on this site before attending.</p>
      <button class="cal-disclaimer-btn" id="board-disclaimer-btn">I understand</button>
    </div>`;
  document.body.appendChild(overlay);

  document.getElementById('board-disclaimer-btn').addEventListener('click', () => {
    overlay.classList.remove('open');
    document.body.style.overflow = '';
    if (pendingCalendarAction) { pendingCalendarAction(); pendingCalendarAction = null; }
  });

  /* Close on backdrop click */
  overlay.addEventListener('click', e => {
    if (e.target === overlay) {
      overlay.classList.remove('open');
      document.body.style.overflow = '';
      pendingCalendarAction = null;
    }
  });
}


/* ═══════════════════════════════════════════════════════════════
   ADD TO CALENDAR
   Shows disclaimer popup first, then fires the calendar action.
   Mobile (iOS + Android): ICS download → opens native calendar app.
   Desktop: Google Calendar URL.
   TODO Task 4 custom events: fire 'add_to_calendar' analytics event.
   ═══════════════════════════════════════════════════════════════ */
function handleCalendarClick(e) {
  const btn = e.target.closest('.add-cal-btn');
  if (!btn) return;
  const date     = btn.dataset.date;
  const meetings = window._upcomingMeetings || [];
  const m        = meetings.find(x => x.date === date);
  if (!m) return;

  /* TODO Task 4: window.cf_beacon && window.cf_beacon('event', { name: 'add_to_calendar' }); */

  const isMobile = /iPad|iPhone|iPod|Android/.test(navigator.userAgent);
  pendingCalendarAction = isMobile
    ? () => downloadICS(m)
    : () => { const url = buildGCalUrl(m); if (url) window.open(url, '_blank'); };

  const overlay = document.getElementById('board-disclaimer-overlay');
  if (overlay) {
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
  }
}

/*
 * ICS download — used on mobile so Add to Calendar opens the native
 * calendar app (iOS Calendar, Android default calendar, etc.)
 * Not exposed as a standalone download link anywhere on the page.
 */
function downloadICS(m) {
  const dateRaw  = m.date.replace(/-/g, '');
  const meeting  = BOARD.meeting || {};
  const location = m.location || meeting.location || '';
  let dtStart, dtEnd;
  if (m.time) {
    const range = buildDateRange(dateRaw, m.time);
    [dtStart, dtEnd] = range.split('/');
  } else {
    dtStart = dtEnd = dateRaw;
  }
  const content = [
    'BEGIN:VCALENDAR', 'VERSION:2.0',
    'PRODID:-//City of Kalamazoo Boards & Commissions//EN',
    'BEGIN:VEVENT',
    `UID:${BOARD.abbr}-${m.date}@kalamazoocity-boards`,
    `DTSTART:${dtStart}`,
    `DTEND:${dtEnd}`,
    `SUMMARY:${BOARD.abbr} — City of Kalamazoo`,
    `LOCATION:${location ? location + ', Kalamazoo, MI' : 'Kalamazoo, MI'}`,
    `DESCRIPTION:Public meeting of the ${BOARD.abbr}.`,
    'END:VEVENT', 'END:VCALENDAR',
  ].join('\r\n');

  const url = URL.createObjectURL(new Blob([content], { type: 'text/calendar' }));
  const a   = Object.assign(document.createElement('a'), {
    href: url, download: `${BOARD.abbr}-${m.date}.ics`
  });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function buildGCalUrl(m) {
  if (!m?.date) return null;
  const dateRaw  = m.date.replace(/-/g, '');
  const meeting  = BOARD.meeting || {};
  const location = m.location || meeting.location || '';
  const pageUrl  = window.location.href.split('?')[0];
  const params   = new URLSearchParams({
    action:   'TEMPLATE',
    text:     `${BOARD.abbr} — City of Kalamazoo`,
    dates:    m.time ? buildDateRange(dateRaw, m.time) : `${dateRaw}/${dateRaw}`,
    details:  `Public meeting of the ${BOARD.abbr}.\n\n` +
              `⚠ This event does not update automatically if the meeting is cancelled or rescheduled. ` +
              `Always verify before attending:\n${pageUrl}`,
    location: location ? `${location}, Kalamazoo, MI` : 'Kalamazoo, MI',
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}

function buildDateRange(dateRaw, timeStr) {
  const parts = timeStr.split(/[–—]|\s+-\s+/);
  const start = parseTimeTo6(parts[0]?.trim() || '');
  const end   = parts[1] ? parseTimeTo6(parts[1].trim()) : addMins(start, 90);
  return `${dateRaw}T${start}/${dateRaw}T${end}`;
}

function parseTimeTo6(str) {
  const m = str.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!m) return '090000';
  let h = parseInt(m[1]), min = parseInt(m[2]);
  const ap = m[3].toUpperCase();
  if (ap === 'PM' && h !== 12) h += 12;
  if (ap === 'AM' && h === 12) h = 0;
  return `${String(h).padStart(2,'0')}${String(min).padStart(2,'0')}00`;
}

function addMins(t6, mins) {
  const h   = parseInt(t6.slice(0, 2));
  const min = parseInt(t6.slice(2, 4));
  const tot = h * 60 + min + mins;
  return `${String(Math.floor(tot / 60)).padStart(2,'0')}${String(tot % 60).padStart(2,'0')}00`;
}
