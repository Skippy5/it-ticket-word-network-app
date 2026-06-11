"""Central configuration: stopwords, synonym map, seed phrases, defaults.

Everything here is a *default*. The Streamlit UI surfaces the stopword list and
synonym map as editable text areas, and every numeric/toggle default below as a
sidebar control, so the app can be tuned without touching this file.
"""

# ---------------------------------------------------------------------------
# Column roles (matched case-insensitively against the CSV header)
# ---------------------------------------------------------------------------

ID_COLUMN = "ticket_id"
TEXT_COLUMNS = ["short_description", "work_notes", "close_notes"]
FILTER_COLUMNS = [
    "business_unit",
    "country",
    "state",
    "location",
    "category",
    "subcategory",
    "assignment_group",
    "priority",
    "status",
]
# Geographic cascade order: picking a country narrows states, which narrows locations.
GEO_CASCADE = ["country", "state", "location"]
DATE_COLUMN = "opened_at"

# Required for the app to function at all; everything else degrades gracefully.
REQUIRED_COLUMNS = [ID_COLUMN]

# ---------------------------------------------------------------------------
# Stop words
# ---------------------------------------------------------------------------

# Standard English stopwords (sklearn's ENGLISH_STOP_WORDS is merged in at
# runtime by preprocess.py). This block is the *IT/ticketing* extension list
# from the build prompt plus low-signal ticket boilerplate seen in real notes.
IT_STOPWORDS = """
user please thanks thank ticket incident issue resolved closed called advised
regards hi hello team am pm eod fyi
reports reported report confirms confirm confirmed closing close per policy
found find suspected suspect traced trace caused causing cause successfully
verified verify fixed fix cleared clear applied apply granted grant tested
checked check inspected inspect reviewed review ran run
working works work worked restored restore normal good clean stable known-good
request requested requests needs need new starter
resolve resolves replace replaced advise call
showing flagged normally flowing requester confirmation
""".split()

# Tokens that must never be lemmatized/stemmed (acronyms, product names).
PROTECTED_TERMS = {
    "vpn", "dns", "dhcp", "vlan", "mfa", "sso", "okta", "sccm", "sap",
    "m365", "office365", "os", "ip", "ad", "ram", "usb", "mfp", "ost",
    "wifi", "teams", "windows", "outlook", "exchange", "zoom", "excel",
    "chrome", "adobe", "acrobat", "xerox", "hp", "laserjet",
}

# ---------------------------------------------------------------------------
# Synonym / abbreviation map  (applied token-by-token BEFORE counting)
# ---------------------------------------------------------------------------
# Keys are matched against lowercased tokens. Values may contain spaces; they
# are re-tokenized, so "dl" -> "distribution list" can then be captured by
# phrase detection as a single node.

SYNONYMS = {
    # abbreviations
    "pwd": "password",
    "passwd": "password",
    "dl": "distribution list",
    "m365": "office365",
    "o365": "office365",
    "ms365": "office365",
    "ad": "active directory",
    "config": "configuration",
    "configs": "configuration",
    "msg": "message",
    "acct": "account",
    "auth": "authentication",
    # normalize re- spellings
    "re-install": "reinstall",
    "re-installed": "reinstall",
    "reinstalled": "reinstall",
    "re-image": "reimage",
    "re-imaged": "reimage",
    "reimaged": "reimage",
    "re-added": "readd",
    "re-enrolled": "reenroll",
    "re-enroll": "reenroll",
    # common typos in ticket notes
    "teh": "the",
    "conection": "connection",
    "conections": "connection",
    "wi-fi": "wifi",
    # collapse close variants
    "e-mail": "email",
    "emails": "email",
    "printing": "print",
    "prints": "print",
    "printed": "print",
}

# ---------------------------------------------------------------------------
# Seed phrases: always merged into one node when seen as adjacent tokens.
# Auto-detection (gensim-style scoring) runs on top of these when enabled.
# ---------------------------------------------------------------------------

SEED_PHRASES = [
    "distribution list",
    "active directory",
    "print queue",
    "print spooler",
    "print server",
    "print driver",
    "network drive",
    "network printer",
    "network adapter",
    "blue screen",
    "access point",
    "switch port",
    "shared inbox",
    "meeting invite",
    "paper jam",
    "paper tray",
    "toner cartridge",
    "test page",
    "default printer",
    "power adapter",
    "display cable",
    "display driver",
    "wireless driver",
    "hard drive",
    "docking station",
    "fan vents",
    "security group",
    "outlook web app",
    "vpn portal",
    "vpn client",
    "dhcp lease",
    "dns cache",
    "dns record",
    "ethernet cable",
    "mailbox quota",
    "ost file",
    "outlook profile",
    "autocomplete cache",
    "license key",
    "error 49",
    "add-in",
    "line-of-business app",
    "fan noise",
    "stress test",
    "sign-in",
]

# ---------------------------------------------------------------------------
# Pipeline / graph defaults (all exposed in the UI)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "text_columns": ["short_description", "work_notes", "close_notes"],
    "phrase_detection": True,        # auto bigram detection on/off (seeds always apply)
    "phrase_min_count": 3,           # bigram must appear in >= N docs
    "phrase_threshold": 8.0,         # gensim-style score threshold
    "cooc_scope": "document",        # "document" | "window"
    "window_size": 8,                # tokens, used when scope == "window"
    "weighting": "pmi",              # "count" | "pmi"  (pmi == positive PMI)
    "min_term_freq": 3,              # term must appear in >= N tickets
    "min_edge_weight": 2.0,          # count mode: co-doc count; pmi mode: ppmi value
    "min_edge_weight_pmi": 0.5,
    "max_nodes": 140,                # top-N terms by document frequency
    "max_edges_per_node": 14,        # top-K backbone per node (0 = keep all)
    "louvain_resolution": 1.6,
    "physics": True,
    "seed": 42,
}

# Drill-in: ticket links are rendered with this template ({ticket_id} is replaced).
TICKET_URL_TEMPLATE = (
    "https://servicenow.example.com/incident.do?sysparm_query=number={ticket_id}"
)

# Community color palette (vis.js groups), tab10/tab20-ish, readable on white.
PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1",
    "#76b7b2", "#edc948", "#ff9da7", "#9c755f", "#bab0ac",
    "#86bcb6", "#d37295", "#a0cbe8", "#ffbe7d", "#8cd17d",
]
