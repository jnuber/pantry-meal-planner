-- Run this in the Supabase SQL editor if the tables do not already exist.

create table if not exists pantry_inventory (
    id bigint generated always as identity primary key,
    ingredient_name text unique not null,
    category text,
    quantity numeric,
    unit text,
    unit_system text,
    updated_at timestamptz default now()
);

create table if not exists saved_recipes (
    id bigint generated always as identity primary key,
    title text,
    ingredients text,
    instructions text,
    rating smallint,
    created_at timestamptz default now(),
    constraint saved_recipes_rating_check check (rating is null or (rating >= 1 and rating <= 5))
);

create table if not exists custom_tags (
    kind text not null,
    name text not null,
    primary key (kind, name)
);

alter table pantry_inventory enable row level security;
alter table saved_recipes enable row level security;
alter table custom_tags enable row level security;

drop policy if exists "pantry_inventory_all" on pantry_inventory;
create policy "pantry_inventory_all" on pantry_inventory for all using (true) with check (true);

drop policy if exists "saved_recipes_all" on saved_recipes;
create policy "saved_recipes_all" on saved_recipes for all using (true) with check (true);

drop policy if exists "custom_tags_all" on custom_tags;
create policy "custom_tags_all" on custom_tags for all using (true) with check (true);

-- Existing projects: add favorite ratings if the column is missing.
alter table saved_recipes add column if not exists rating smallint;
alter table saved_recipes drop constraint if exists saved_recipes_rating_check;
alter table saved_recipes
    add constraint saved_recipes_rating_check
    check (rating is null or (rating >= 1 and rating <= 5));
