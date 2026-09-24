-- Personal Finance Dashboard — ledger schema (Supabase Postgres)
--
-- Beancount-style double-entry: every transaction's postings balance to zero.
-- `accounts` holds both real bank/card accounts and category "accounts"
-- (Assets:*, Liabilities:*, Income:*, Expenses:*) as colon-separated paths,
-- e.g. 'Assets:ANZ:AccessAdvantage', 'Expenses:Food:Groceries'.

create table accounts (
    id            bigint generated always as identity primary key,
    name          text not null unique,
    root_type     text not null check (root_type in ('asset', 'liability', 'income', 'expense')),
    credit_limit  numeric(12, 2),  -- nullable; only set on liability credit-card accounts
    created_at    timestamptz not null default now()
);

create table transactions (
    id                bigint generated always as identity primary key,
    date              date not null,
    description       text not null,
    source_file       text,  -- provenance: originating statement PDF
    source_page       int,
    -- Display-only metadata, never used in arithmetic (the `amount` posting is
    -- always the AUD amount actually charged). Stored as text, not numeric:
    -- Amex's Foreign Spend Amount column doesn't always use AUD's
    -- comma-thousands/dot-decimal format (e.g. '1.239,00' for some European
    -- currencies), and no currency code is printed per row to know which
    -- convention applies, so converting it would mean guessing a locale.
    foreign_amount    text,
    foreign_currency  text,
    created_at        timestamptz not null default now()  -- MAX(created_at) drives the Data Freshness callout
);

create table postings (
    id              bigint generated always as identity primary key,
    transaction_id  bigint not null references transactions(id) on delete cascade,
    account_id      bigint not null references accounts(id),
    amount          numeric(12, 2) not null  -- positive increases the account's own normal balance side
);

create index postings_transaction_id_idx on postings(transaction_id);
create index postings_account_id_idx on postings(account_id);
create index transactions_date_idx on transactions(date);

-- Views -----------------------------------------------------------------

-- Current balance per account.
create view v_account_balances as
select
    a.id as account_id,
    a.name,
    a.root_type,
    a.credit_limit,
    coalesce(sum(p.amount), 0) as balance
from accounts a
left join postings p on p.account_id = a.id
group by a.id, a.name, a.root_type, a.credit_limit;

-- Per-category totals by calendar month — category trend chart and the
-- top-level Spending by Category breakdown.
create view v_monthly_category_totals as
select
    date_trunc('month', t.date)::date as month,
    a.id as account_id,
    a.name as category,
    sum(p.amount) as total
from postings p
join transactions t on t.id = p.transaction_id
join accounts a on a.id = p.account_id
where a.root_type = 'expense'
group by 1, 2, 3;

-- Monthly income vs. expense totals — Overview grouped bar chart; savings
-- rate is computed client-side from these two figures. Income postings are
-- stored credit-normal (negative), the opposite convention from expense
-- postings (positive), which is what lets a transaction's postings actually
-- balance to zero — flipped here to a positive figure for display, same as
-- any standard ledger report would.
create view v_monthly_income_expense as
select
    date_trunc('month', t.date)::date as month,
    a.root_type,
    sum(case when a.root_type = 'income' then -p.amount else p.amount end) as total
from postings p
join transactions t on t.id = p.transaction_id
join accounts a on a.id = p.account_id
where a.root_type in ('income', 'expense')
group by 1, 2;

-- Daily total spend — daily-spend calendar heatmap and weekday pattern.
create view v_daily_spend as
select
    t.date,
    sum(p.amount) as total
from postings p
join transactions t on t.id = p.transaction_id
join accounts a on a.id = p.account_id
where a.root_type = 'expense'
group by t.date;

-- Daily per-category spend, deliberately left at daily grain rather than
-- pre-aggregated to a fixed period. Category movers compares whatever date
-- range is selected on the dashboard against the immediately preceding
-- range of equal length — that range is chosen at query time, not fixed to
-- a calendar month, so the frontend sums whichever two windows it needs
-- from this view rather than the view assuming the period itself.
create view v_category_movers as
select
    a.id as account_id,
    a.name as category,
    t.date,
    sum(p.amount) as total
from postings p
join transactions t on t.id = p.transaction_id
join accounts a on a.id = p.account_id
where a.root_type = 'expense'
group by a.id, a.name, t.date;

-- Access ------------------------------------------------------------------

-- Read-only for the frontend's anon key. v1 is read-only in both the demo
-- and personal deployments — plain GRANT/REVOKE, not row-level policies,
-- since there's no per-row ownership split to enforce. Writes only ever
-- happen locally via the pipeline, using the service-role key.
grant select on all tables in schema public to anon;
grant select on all tables in schema public to authenticated;
