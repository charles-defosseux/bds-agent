-- BDS treasury backend. Small on purpose: 7 tables.
-- Booleans are 0/1 integers (converted to bool by backend/state.py).
-- Money is stored as REAL euros (amounts are fictional, 2 decimals max).

CREATE TABLE sections (
    name                 TEXT PRIMARY KEY CHECK (name IN ('football', 'tennis', 'padel', 'voile')),
    budget_remaining_eur REAL NOT NULL CHECK (budget_remaining_eur >= 0)
);

CREATE TABLE members (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    email     TEXT NOT NULL UNIQUE,
    section   TEXT NOT NULL REFERENCES sections(name),
    role      TEXT NOT NULL CHECK (role IN ('member', 'captain', 'treasurer', 'president')),
    dues_paid INTEGER NOT NULL DEFAULT 0 CHECK (dues_paid IN (0, 1)),
    iban      TEXT NOT NULL
);

CREATE TABLE events (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    section     TEXT NOT NULL REFERENCES sections(name),
    date        TEXT NOT NULL,               -- ISO date YYYY-MM-DD
    price_eur   REAL NOT NULL CHECK (price_eur >= 0),
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    description TEXT NOT NULL DEFAULT ''     -- free text: untrusted
);

CREATE TABLE registrations (
    event_id        TEXT NOT NULL REFERENCES events(id),
    member_id       TEXT NOT NULL REFERENCES members(id),
    status          TEXT NOT NULL DEFAULT 'registered' CHECK (status IN ('registered', 'refunded')),
    amount_paid_eur REAL NOT NULL CHECK (amount_paid_eur >= 0),
    PRIMARY KEY (event_id, member_id)
);

CREATE TABLE expenses (
    id                 TEXT PRIMARY KEY,
    type               TEXT NOT NULL CHECK (type IN ('vendor_invoice', 'member_reimbursement')),
    section            TEXT NOT NULL REFERENCES sections(name),
    payee              TEXT NOT NULL,        -- vendor name, or member_id for a reimbursement
    vendor_iban        TEXT,                 -- only for vendor invoices
    amount_eur         REAL NOT NULL CHECK (amount_eur > 0),
    status             TEXT NOT NULL DEFAULT 'submitted'
                       CHECK (status IN ('submitted', 'validated', 'paid', 'rejected')),
    has_receipt        INTEGER NOT NULL DEFAULT 0 CHECK (has_receipt IN (0, 1)),
    president_approved INTEGER NOT NULL DEFAULT 0 CHECK (president_approved IN (0, 1)),
    vendor_blocked     INTEGER NOT NULL DEFAULT 0 CHECK (vendor_blocked IN (0, 1)),  -- rule 5
    note               TEXT NOT NULL DEFAULT ''  -- free text: untrusted
);

CREATE TABLE ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL CHECK (type IN ('dues', 'expense_payment', 'reimbursement', 'event_refund')),
    amount_eur      REAL NOT NULL CHECK (amount_eur > 0),  -- direction is given by `type`
    reference       TEXT NOT NULL,           -- member_id, expense id or event id
    idempotency_key TEXT NOT NULL UNIQUE,
    actor_id        TEXT NOT NULL,           -- treasurer, injected by the harness
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recipients      TEXT NOT NULL,           -- JSON list of member ids
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    actor_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
