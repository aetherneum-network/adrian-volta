"""The invented world: three fictitious companies and the vocabulary their topologies are drawn from.

Everything here is made up. Domains are under ``.example`` (reserved for documentation by
RFC 2606), service names are generic words, no host, address, key or path of any real system
appears. The lab topology does not describe any real infrastructure.
"""
from __future__ import annotations

COMPANIES = [
    {"name": "Cartiera Valdora S.p.A.", "slug": "valdora", "domain": "valdora.example"},
    {"name": "Molino Serrabruna S.r.l.", "slug": "serrabruna", "domain": "serrabruna.example"},
    {"name": "Vetreria Altofonte S.r.l.", "slug": "altofonte", "domain": "altofonte.example"},
]

PUBLIC_POOL = ["orders", "catalog", "status", "shop", "docs", "press", "careers", "tracking", "quotes",
               "newsletter", "suppliers", "gallery"]
ADMIN_POOL = ["console", "metrics", "registry", "ledger-view", "wiki", "mailroom", "scheduler", "audit-log"]
GHOST_SUFFIXES = ["-v2", "-new", "-blue", "-next"]
PORTS = [3000, 5000, 8000, 8080, 8081, 8443, 9000, 9090]
HEALTH_PATHS = ["/healthz", "/health", "/ping"]
EXTRA_PREFIXES = ["/api", "/status", "/docs"]
RESTART = ["always", "on-failure", "unless-stopped"]
ROUTE_GROUPS = ["internal", "shared", "team-ops"]
LEGACY_DIRS = ["old", "archive-2025", "routes-before-migration", "to-review"]
EXPORT_KINDS = ["ledger", "orders", "stock"]
LOCAL_TZ = "Europe/Rome"

# File names to back up: accents and spaces on purpose (lesson L8: enumerate what is really there).
FILE_NAMES = [
    "Relazione qualità.txt", "Inventario metà anno.csv", "Procedura già approvata.md", "Elenco città e sedi.txt",
    "Verbale più recente.txt", "Listino prezzi.csv", "Contatti fornitori.txt", "Note di produzione.md",
    "Calendario manutenzioni.csv", "Registro lotti.txt", "Scheda tecnica così com'è.txt", "Piano turni.csv",
    "Capacità impianto.txt", "Riepilogo attività.md",
]
DIRS = ["documenti", "documenti/qualità", "archivio", "archivio/2026", "produzione", "produzione/linea-1", "amministrazione"]
LONG_PARTS = [
    "documentazione-tecnica-della-linea-di-produzione-numero-tre-revisione-finale-approvata",
    "verbali-delle-riunioni-settimanali-del-reparto-manutenzione-e-qualità-anno-corrente",
    "schede-di-lavorazione-con-allegati-fotografici-e-note-del-responsabile-di-turno",
    "corrispondenza-interna-tra-magazzino-spedizioni-e-ufficio-acquisti-ordinata-per-mese",
    "inventari-periodici-con-conteggi-doppi-e-riconciliazioni-firmate-dal-capo-reparto",
]
WORDS = ["lotto", "turno", "linea", "carta", "farina", "vetro", "forno", "bobina", "sacco", "lastra", "ordine", "quota",
         "misura", "taglio", "peso", "cassa", "collo", "bancale", "scarto", "resa", "prova", "campione", "nota", "riga"]
