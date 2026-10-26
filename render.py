"""Turns any record into readable HTML: icons for common things (name, phone, address, ...),
status badges, money, dates, chips, tables, and sections for the records that belong to it.

It works on any JSON by looking at field NAMES and VALUES, so new data looks fine without any
configuration. A config's view.json can refine it:

    {
      "primary": "customers",                      the collection other collections link to
      "collections": {
        "customers": {"label": "Patients", "label_one": "Patient", "icon": "🧑‍⚕️", "accent": "teal",
                      "title": "name", "subtitle": ["plan"], "first": ["email", "phone"],
                      "hide": ["internal_note"], "labels": {"nr": "Account no."}},
        "orders":    {"link": "customer_id", "title": "id", "icon": "🛒", "accent": "green"}
      }
    }

    link       field in this collection that holds the primary record's id: those records are
               shown under the primary record, as a section
    title      field used as the card title (default: name / title / subject / ... else the id)
    subtitle   fields shown as small chips under the title
    first      fields shown first, in this order     hide    fields never shown
    labels     {field: label} overrides              icon / accent / label / label_one

Everything here returns HTML strings with no line breaks (so Streamlit's markdown keeps them intact).
All data is escaped.
"""
from __future__ import annotations

import html
import re

# ---------------------------------------------------------------- categories
# (keyword in the collection name -> icon, accent). The first match wins.
CATEGORIES = [
    ("customer", "👤", "blue"), ("patient", "🧑‍⚕️", "teal"), ("employee", "🧑‍💼", "blue"), ("member", "👤", "blue"),
    ("client", "👤", "blue"), ("user", "👤", "blue"), ("person", "👤", "blue"),
    ("order", "🛒", "green"), ("return", "↩️", "amber"), ("shipment", "📦", "teal"), ("delivery", "📦", "teal"),
    ("transfer", "➡️", "purple"), ("document", "📄", "slate"), ("statement", "📄", "slate"), ("receipt", "🧾", "slate"),
    ("invoice", "🧾", "purple"), ("bill", "🧾", "purple"), ("payment", "💳", "purple"), ("transaction", "💸", "purple"),
    ("claim", "📋", "orange"), ("case", "📁", "slate"), ("issue", "⚠️", "red"), ("incident", "🚨", "red"),
    ("ticket", "🎫", "red"), ("problem", "⚠️", "red"), ("request", "📝", "slate"), ("quote", "💬", "teal"),
    ("appointment", "📅", "teal"), ("booking", "📅", "teal"), ("visit", "📅", "teal"),
    ("subscription", "🔁", "purple"), ("contract", "📜", "slate"), ("line", "📶", "teal"),
    ("prescription", "💊", "green"), ("medication", "💊", "green"), ("result", "🧪", "teal"),
    ("account", "🏦", "blue"), ("card", "💳", "purple"), ("policy", "🛡️", "blue"),
    ("asset", "💻", "slate"), ("device", "📱", "slate"), ("equipment", "💻", "slate"),
    ("product", "🏷️", "green"), ("item", "🏷️", "green"), ("loan", "🏠", "amber"), ("complaint", "😠", "red"),
]
DEFAULT_CATEGORY = ("🗂️", "slate")

TITLE_KEYS = ["name", "full name", "title", "subject", "summary", "item", "type", "product", "vehicle", "description"]
STATUS_KEYS = {"status", "state", "stage", "priority", "severity", "result"}
MONEY_WORDS = ("price", "total", "amount", "balance", "cost", "fee", "premium", "limit", "salary", "charge", "refund", "payment due", "threshold", "outstanding", "instalment", "repayable")
GOOD = {"delivered", "shipped", "resolved", "closed", "completed", "complete", "approved", "paid", "active", "done", "confirmed",
        "ok", "available", "yes", "true", "settled", "fixed", "enabled", "verified", "refunded", "issued", "healthy", "normal", "low"}
WARN = {"pending", "processing", "in progress", "awaiting", "waiting", "scheduled", "open", "new", "submitted", "under review",
        "review", "info required", "action required", "partial", "on hold", "preparing", "booked", "in transit", "requested",
        "pending confirmation", "trial", "medium", "assigned", "investigating", "in review"}
BAD = {"failed", "overdue", "rejected", "denied", "blocked", "cancelled", "canceled", "critical", "urgent", "expired", "suspended",
       "declined", "lost", "disputed", "unpaid", "refused", "no", "false", "high", "outage", "stolen"}
CURRENCY = {"EUR": "€", "GBP": "£", "USD": "$", "TRY": "₺", "CHF": "CHF "}
ACRONYMS = {"id": "ID", "iban": "IBAN", "bic": "BIC", "sku": "SKU", "url": "URL", "vat": "VAT", "sim": "SIM", "imei": "IMEI",
            "ip": "IP", "os": "OS", "pin": "PIN", "dob": "Date of birth", "nr": "No.", "msisdn": "MSISDN", "vpn": "VPN",
            "wifi": "Wi-Fi", "gp": "GP", "mfa": "MFA", "sla": "SLA", "5g": "5G", "4g": "4G", "pc": "PC", "tv": "TV"}

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
DATE_LIKE = re.compile(r"(\d{4}-\d{2}-\d{2})|(\d{1,2} [A-Z][a-z]+ \d{4})|(\d{1,2}/\d{1,2}/\d{2,4})")

CSS = """
<style>
.xd-card{border:1px solid rgba(128,128,128,.28);border-left:5px solid var(--acc,#64748b);border-radius:12px;padding:14px 16px;margin:10px 0;background:rgba(128,128,128,.07)}
.xd-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.xd-ico{font-size:1.5rem;line-height:1}
.xd-title{font-weight:700;font-size:1.05rem}
.xd-big{font-weight:800;font-size:1.6rem;line-height:1.2}
.xd-id{font-family:ui-monospace,Consolas,monospace;font-size:.78rem;opacity:.65}
.xd-sub{display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 6px}
.xd-chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:.78rem;background:rgba(128,128,128,.18);margin:1px 3px 1px 0}
.xd-badge{display:inline-block;padding:1px 10px;border-radius:999px;font-size:.78rem;font-weight:700}
.xd-good{background:rgba(34,165,91,.18);color:#22a55b}.xd-warn{background:rgba(217,142,4,.20);color:#d98e04}
.xd-bad{background:rgba(224,72,62,.18);color:#e0483e}.xd-neutral{background:rgba(128,128,128,.2)}
.xd-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;margin:10px 0}
.xd-tile{display:flex;gap:10px;align-items:center;border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:8px 12px;background:rgba(128,128,128,.07)}
.xd-tile .xd-ico{font-size:1.3rem}
.xd-lab{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;opacity:.6}
.xd-val{font-size:.95rem;word-break:break-word}
.xd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px 18px}
.xd-wide{grid-column:1/-1}
.xd-sec{display:flex;align-items:center;gap:8px;margin:22px 0 4px;font-weight:700;font-size:1.1rem;scroll-margin-top:4rem}
.xd-count{background:var(--acc,#64748b);color:#fff;border-radius:999px;font-size:.75rem;padding:0 8px;font-weight:700}
.xd-sub-card{border:1px dashed rgba(128,128,128,.35);border-radius:8px;padding:8px 10px}
table.xd-table{border-collapse:collapse;font-size:.85rem;width:100%}
table.xd-table th{text-align:left;font-weight:600;opacity:.7;border-bottom:1px solid rgba(128,128,128,.35);padding:3px 8px}
table.xd-table td{border-bottom:1px solid rgba(128,128,128,.15);padding:3px 8px}
.xd-mono{font-family:ui-monospace,Consolas,monospace}
.acc-blue{--acc:#3b82f6}.acc-green{--acc:#22a55b}.acc-amber{--acc:#d98e04}.acc-orange{--acc:#ea7a1a}.acc-red{--acc:#e0483e}
.acc-purple{--acc:#8b5cf6}.acc-teal{--acc:#14a3a3}.acc-slate{--acc:#64748b}
</style>
"""


# ---------------------------------------------------------------- small helpers
def esc(value) -> str:
    return html.escape(str(value), quote=True)


def norm(key) -> str:
    return re.sub(r"[_\-\s]+", " ", str(key).strip().lower())


def humanize(key, labels: dict | None = None) -> str:
    """date_of_birth -> Date of birth; ids and common acronyms keep their form."""
    if labels and key in labels:
        return labels[key]
    words = norm(key).split(" ")
    out = [ACRONYMS.get(w, w) for w in words]
    text = " ".join(out)
    return text[:1].upper() + text[1:] if text else str(key)


def singular(word: str) -> str:
    word = norm(word)
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses") or word.endswith("xes"):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def collection_config(name: str, view: dict | None) -> dict:
    """Category defaults for a collection, overridden by the config's view.json."""
    base = singular(name)
    icon, accent = DEFAULT_CATEGORY
    for word, ic, ac in CATEGORIES:
        if word in base:
            icon, accent = ic, ac
            break
    cfg = {"label": humanize(name), "label_one": humanize(base), "icon": icon, "accent": accent,
           "title": None, "subtitle": [], "first": [], "hide": [], "labels": {}, "link": None}
    cfg.update(((view or {}).get("collections") or {}).get(name, {}))
    return cfg


def status_class(value) -> str:
    text = norm(value)
    if text in BAD or any(w in text for w in ("overdue", "failed", "rejected", "cancel", "critical", "blocked")):
        return "xd-bad"
    if text in WARN or "pending" in text or "required" in text or "progress" in text:
        return "xd-warn"
    if text in GOOD or text.startswith(("deliver", "resolv", "complet", "approv")):
        return "xd-good"
    return "xd-neutral"


def kind_of(key, value) -> str:
    """What sort of thing a field is, from its name and value."""
    k = norm(key)
    if value is None or value == "":
        return "empty"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "table" if value and all(isinstance(x, dict) for x in value) else "chips"
    text = str(value)
    if "email" in k or k.replace(" ", "") == "email" or EMAIL.fullmatch(text):
        return "email"
    if any(w in k for w in ("phone", "mobile", "telephone", "msisdn", "fax")) or k in ("tel", "number to call"):
        return "phone"
    if "birth" in k or k == "dob":
        return "dob"
    if k in STATUS_KEYS or k.endswith(" status") or k.endswith(" state") or k.endswith(" priority"):
        return "status"
    if len(text) <= 30 and norm(text) in (GOOD | WARN | BAD):
        return "state"                       # a status-like word under an ordinary key, e.g. "photos sent": "action required"
    if any(w in k for w in ("address", "street", "city", "postcode", "post code", "zip", "location")) and not isinstance(value, (int, float)):
        return "address"
    if isinstance(value, (int, float)) and any(w in k for w in MONEY_WORDS):
        return "money"
    if text.startswith(("http://", "https://")):
        return "url"
    if "date" in k or k.endswith(" at") or k in ("created", "updated", "due", "expires") or (len(text) <= 22 and DATE_LIKE.fullmatch(text)):
        return "date"
    if k == "id" or k.endswith(" id") or k.endswith(" number") or k.endswith(" nr") or k.endswith(" no") or k in ("iban", "sku", "reference", "ref", "imei", "tracking") or k.endswith(" reference"):
        return "id"
    if len(text) > 90:
        return "text"
    return "plain"


ICONS = {"email": "✉️", "phone": "📞", "address": "🏠", "dob": "🎂", "date": "📅", "money": "💶", "url": "🔗", "id": "#️⃣"}
TILE_KINDS = ("email", "phone", "address", "dob")


def money_text(value, currency: str | None) -> str:
    symbol = CURRENCY.get((currency or "").upper(), f"{currency} " if currency else "")
    sign = "-" if value < 0 else ""
    number = f"{abs(value):,.2f}"
    return f"{sign}{symbol}{number}"


def link_label(key, value, record: dict | None = None) -> str:
    """What to show for a link instead of the whole URL (which can carry a long access token)."""
    k = norm(key)
    if k == "doc url" and (record or {}).get("doc_id"):
        return f"Receipt {record['doc_id']}"                                       # e.g. "Receipt DOC8001"
    if "documents page" in k:
        return "Documents page"
    text = re.sub(r"^https?://", "", str(value).split("?")[0])
    return text if len(text) <= 48 else text[:45] + "…"


def value_html(key, value, record: dict | None = None, cfg: dict | None = None) -> str:
    """One value, formatted for its kind."""
    kind = kind_of(key, value)
    if kind == "empty":
        return '<span style="opacity:.4">–</span>'
    if kind == "bool":
        return '<span class="xd-badge xd-good">✓ yes</span>' if value else '<span class="xd-badge xd-neutral">✗ no</span>'
    if kind in ("status", "state"):
        return f'<span class="xd-badge {status_class(value)}">{esc(value)}</span>'
    if kind == "money":
        return f'<span class="xd-mono">{esc(money_text(value, (record or {}).get("currency")))}</span>'
    if kind == "email":
        return f'<a href="mailto:{esc(value)}">{esc(value)}</a>'
    if kind == "url":
        return f'<a href="{esc(value)}" target="_blank" rel="noopener noreferrer">{esc(link_label(key, value, record))}</a>'
    if kind == "id":
        return f'<span class="xd-mono">{esc(value)}</span>'
    if kind == "chips":
        return "".join(f'<span class="xd-chip">{esc(item)}</span>' for item in value) if value else '<span style="opacity:.4">–</span>'
    if kind == "table":
        return table_html(value, cfg)
    if kind == "dict":
        return grid_html(value, cfg)
    if isinstance(value, float) and not isinstance(value, bool):
        return esc(f"{value:g}")
    return esc(value)


def table_html(rows: list, cfg: dict | None = None) -> str:
    labels = (cfg or {}).get("labels")
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    head = "".join(f"<th>{esc(humanize(c, labels))}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{value_html(c, row.get(c), row, cfg)}</td>" for c in columns) + "</tr>" for row in rows)
    return f'<table class="xd-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _cell(key, value, record, cfg) -> str:
    kind = kind_of(key, value)
    icon = ICONS.get(kind, "")
    label = (icon + " " if icon else "") + humanize(key, (cfg or {}).get("labels"))
    wide = " xd-wide" if kind in ("table", "dict", "text") else ""
    return f'<div class="{wide.strip()}"><div class="xd-lab">{esc(label)}</div><div class="xd-val">{value_html(key, value, record, cfg)}</div></div>'


def ordered_keys(record: dict, cfg: dict, skip: set) -> list[str]:
    hide = set(cfg.get("hide") or []) | skip
    first = [k for k in (cfg.get("first") or []) if k in record and k not in hide]
    rest = [k for k in record if k not in hide and k not in first]
    return first + rest


def grid_html(record: dict, cfg: dict | None = None, skip: set | None = None) -> str:
    cfg = cfg or {}
    skip = set(skip or ())
    if "currency" in record and any(kind_of(k, v) == "money" for k, v in record.items()):
        skip.add("currency")                 # the amounts already show their currency symbol
    keys = ordered_keys(record, cfg, skip)
    cells = "".join(_cell(k, record[k], record, cfg) for k in keys)
    return f'<div class="xd-grid">{cells}</div>'


# ---------------------------------------------------------------- titles
def title_of(record_id: str, record: dict, cfg: dict) -> tuple[str, str | None]:
    """(title text, key it came from). Falls back to the record id."""
    wanted = cfg.get("title")
    if wanted and wanted != "id" and record.get(wanted) not in (None, ""):
        return str(record[wanted]), wanted
    if wanted == "id":
        return record_id, None
    lowered = {norm(k): k for k in record}
    for candidate in TITLE_KEYS:
        key = lowered.get(candidate)
        if key and isinstance(record[key], (str, int, float)) and record[key] != "":
            return str(record[key]), key
    return record_id, None


def badges_html(record: dict, skip: set | None = None) -> str:
    return "".join(value_html(k, v) for k, v in record.items()
                   if kind_of(k, v) == "status" and k not in (skip or set()) and isinstance(v, (str, int, float)))


# ---------------------------------------------------------------- cards and sections
def section_header(icon: str, title: str, accent: str, count: int | None = None, anchor: str | None = None) -> str:
    badge = f'<span class="xd-count">{count}</span>' if count is not None else ""
    ident = f' id="section-{esc(anchor)}"' if anchor else ""
    return f'<div{ident} class="xd-sec acc-{esc(accent)}"><span class="xd-ico">{icon}</span><span>{esc(title)}</span>{badge}</div>'


def record_card(collection: str, record_id: str, record: dict, view: dict | None = None, link: str | None = None,
                owner: str | None = None) -> str:
    """A card for one record of any collection.

    link   the field that points to the primary record: left out of the card (it is shown as `owner`)
    owner  text for a chip saying whose record this is, e.g. "👤 Sophie Berthier"
    """
    cfg = collection_config(collection, view)
    title, title_key = title_of(record_id, record, cfg)
    skip = {title_key} if title_key else set()
    link = link or cfg.get("link")
    if link:
        skip.add(link)
    sub_keys = [k for k in (cfg.get("subtitle") or []) if k in record and record[k] not in (None, "")]
    skip |= set(sub_keys)
    badge_skip = set(sub_keys)
    status = badges_html(record, badge_skip)
    status_keys = {k for k, v in record.items() if kind_of(k, v) == "status" and isinstance(v, (str, int, float))}
    chips = "".join(f'<span class="xd-chip">{esc(record[k])}</span>' for k in sub_keys)
    if owner:
        chips = f'<span class="xd-chip">{esc(owner)}</span>' + chips
    ident = f'<span class="xd-id">#{esc(record_id)}</span>' if title != record_id else ""
    head = f'<div class="xd-head"><span class="xd-ico">{cfg["icon"]}</span><span class="xd-title">{esc(title)}</span>{ident}{status}</div>'
    sub = f'<div class="xd-sub">{chips}</div>' if chips else ""
    body = grid_html(record, cfg, skip | status_keys)
    return f'<div class="xd-card acc-{esc(cfg["accent"])}">{head}{sub}{body}</div>'


def profile_html(collection: str, record_id: str, record: dict, view: dict | None = None) -> str:
    """The big card for a primary record: name, subtitle, contact tiles, details."""
    cfg = collection_config(collection, view)
    title, title_key = title_of(record_id, record, cfg)
    skip = {title_key} if title_key else set()
    sub_keys = [k for k in (cfg.get("subtitle") or []) if k in record and record[k] not in (None, "")]
    skip |= set(sub_keys)
    tile_keys = [k for k, v in record.items()
                 if kind_of(k, v) in TILE_KINDS and k not in (cfg.get("hide") or []) and not isinstance(v, (dict, list))]
    status_keys = [k for k, v in record.items() if kind_of(k, v) == "status" and isinstance(v, (str, int, float))]
    skip |= set(tile_keys) | set(status_keys)
    labels = cfg.get("labels")
    chips = "".join(f'<span class="xd-chip">{esc(record[k])}</span>' for k in sub_keys)
    badges = badges_html(record)
    head = (f'<div class="xd-head"><span class="xd-ico">{cfg["icon"]}</span><span class="xd-big">{esc(title)}</span>'
            f'<span class="xd-id">#{esc(record_id)}</span>{badges}</div>')
    sub = f'<div class="xd-sub">{chips}</div>' if chips else ""
    tiles = "".join(
        f'<div class="xd-tile"><span class="xd-ico">{ICONS[kind_of(k, record[k])]}</span><div><div class="xd-lab">{esc(humanize(k, labels))}</div>'
        f'<div class="xd-val">{value_html(k, record[k], record, cfg)}</div></div></div>' for k in tile_keys)
    tiles = f'<div class="xd-tiles">{tiles}</div>' if tiles else ""
    details = grid_html(record, cfg, skip)
    return f'<div class="xd-card acc-{esc(cfg["accent"])}">{head}{sub}{tiles}{details}</div>'


def link_field(name: str, records: dict, primary: str, view: dict | None = None) -> str | None:
    """The field of a collection that holds the primary record's id: from view.json, else
    <primary, singular>_id (customers -> customer_id) when the records have it."""
    configured = collection_config(name, view).get("link")
    if configured:
        return configured
    guess = f"{singular(primary).replace(' ', '_')}_id"
    return guess if any(guess in rec for rec in records.values()) else None


def related_html(primary: str, record_id: str, data: dict, view: dict | None = None) -> str:
    """One section per collection that links to this primary record, each with its records as cards."""
    out = []
    for name, records in data.items():
        if name == primary:
            continue
        link = link_field(name, records, primary, view)
        if not link:
            continue
        cfg = collection_config(name, view)
        mine = {rid: rec for rid, rec in records.items() if rec.get(link) == record_id}
        if not mine:
            continue
        out.append(section_header(cfg["icon"], cfg["label"], cfg["accent"], len(mine), anchor=name))
        out.extend(record_card(name, rid, rec, view, link=link) for rid, rec in mine.items())
    return "".join(out)


def primary_of(data: dict, view: dict | None = None) -> str | None:
    """The collection that others point to: view.json, else "customers", else the first one."""
    wanted = (view or {}).get("primary")
    if wanted in data:
        return wanted
    if "customers" in data:
        return "customers"
    return next(iter(data), None)


def record_text(record_id: str, record: dict) -> str:
    """Lower-case text of a record, for the search box."""
    return (record_id + " " + " ".join(str(v) for v in _flatten(record))).lower()


def _flatten(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _flatten(v)
    elif isinstance(value, list):
        for v in value:
            yield from _flatten(v)
    elif value is not None:
        yield value
