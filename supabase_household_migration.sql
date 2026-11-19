-- Pantry Chef 2.0: household login. Run this in the EXISTING project SQL editor.
-- After this, the Streamlit SUPABASE_KEY must be the anon (public) key, not service_role.
-- In Authentication > Providers > Email, you can turn off "Confirm email" for easier household signup.

create table if not exists households (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    invite_code text not null unique,
    created_at timestamptz default now()
);

alter table households add column if not exists appliances jsonb not null default '["Oven", "Stovetop"]'::jsonb;
alter table households add column if not exists dietary jsonb not null default '["None"]'::jsonb;
alter table households add column if not exists unit_system text not null default 'Imperial';
alter table households add column if not exists servings integer not null default 4;

create table if not exists household_members (
    household_id uuid not null references households(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    role text not null default 'member',
    created_at timestamptz default now(),
    primary key (household_id, user_id),
    constraint household_members_role_check check (role in ('owner', 'member'))
);

alter table pantry_inventory add column if not exists household_id uuid references households(id) on delete cascade;
alter table saved_recipes add column if not exists household_id uuid references households(id) on delete cascade;
alter table custom_tags add column if not exists household_id uuid references households(id) on delete cascade;
alter table grocery_lists add column if not exists household_id uuid references households(id) on delete cascade;

alter table pantry_inventory drop constraint if exists pantry_inventory_ingredient_name_key;
drop index if exists pantry_inventory_ingredient_name_key;
create unique index if not exists pantry_inventory_household_name
    on pantry_inventory (household_id, ingredient_name);

alter table custom_tags drop constraint if exists custom_tags_pkey;
create unique index if not exists custom_tags_household_kind_name
    on custom_tags (household_id, kind, name);

create or replace function current_household_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
    select household_id
    from household_members
    where user_id = auth.uid();
$$;

drop function if exists create_household(text);
drop function if exists join_household(text);

create or replace function create_household(hname text)
returns table(household_id uuid, household_invite text)
language plpgsql
security definer
set search_path = public
as $$
declare
    hid uuid;
    new_code text;
    cleaned text;
begin
    if auth.uid() is null then
        raise exception 'Not signed in';
    end if;
    if exists (select 1 from household_members hm where hm.user_id = auth.uid()) then
        raise exception 'You are already in a household';
    end if;
    cleaned := nullif(trim(hname), '');
    if cleaned is null then
        cleaned := 'Home';
    end if;
    loop
        new_code := upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
        exit when not exists (
            select 1 from households h where h.invite_code = new_code
        );
    end loop;
    insert into households as h (name, invite_code)
    values (cleaned, new_code)
    returning h.id into hid;
    insert into household_members (household_id, user_id, role)
    values (hid, auth.uid(), 'owner');
    update pantry_inventory set household_id = hid where pantry_inventory.household_id is null;
    update saved_recipes set household_id = hid where saved_recipes.household_id is null;
    update custom_tags set household_id = hid where custom_tags.household_id is null;
    update grocery_lists set household_id = hid where grocery_lists.household_id is null;
    household_id := hid;
    household_invite := new_code;
    return next;
end;
$$;

create or replace function join_household(invite text)
returns table(household_id uuid, household_invite text)
language plpgsql
security definer
set search_path = public
as $$
declare
    hid uuid;
    found_code text;
    cleaned text;
begin
    if auth.uid() is null then
        raise exception 'Not signed in';
    end if;
    if exists (select 1 from household_members hm where hm.user_id = auth.uid()) then
        raise exception 'You are already in a household';
    end if;
    cleaned := upper(trim(invite));
    select h.id, h.invite_code
      into hid, found_code
      from households h
     where h.invite_code = cleaned;
    if hid is null then
        raise exception 'Invalid invite code';
    end if;
    insert into household_members as hm (household_id, user_id, role)
    values (hid, auth.uid(), 'member')
    on conflict do nothing;
    household_id := hid;
    household_invite := found_code;
    return next;
end;
$$;

create or replace function leave_household()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    rec record;
    next_owner uuid;
    leftover int;
begin
    if auth.uid() is null then
        raise exception 'Not signed in';
    end if;
    if not exists (select 1 from household_members hm where hm.user_id = auth.uid()) then
        raise exception 'You are not in a household';
    end if;

    for rec in
        select hm.household_id, (hm.role = 'owner') as was_owner
        from household_members hm
        where hm.user_id = auth.uid()
    loop
        delete from household_members
        where household_id = rec.household_id and user_id = auth.uid();

        select count(*) into leftover
        from household_members
        where household_id = rec.household_id;

        if leftover = 0 then
            delete from households where id = rec.household_id;
        elsif rec.was_owner then
            select hm.user_id
              into next_owner
              from household_members hm
             where hm.household_id = rec.household_id
             order by hm.created_at
             limit 1;
            if next_owner is not null then
                update household_members
                   set role = 'owner'
                 where household_id = rec.household_id and user_id = next_owner;
            end if;
        end if;
    end loop;
    return 'ok';
end;
$$;

create or replace function delete_household()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    hid uuid;
begin
    if auth.uid() is null then
        raise exception 'Not signed in';
    end if;
    select hm.household_id
      into hid
      from household_members hm
     where hm.user_id = auth.uid() and hm.role = 'owner'
     limit 1;
    if hid is null then
        raise exception 'Only the household owner can delete it';
    end if;
    delete from households where id = hid;
    return 'ok';
end;
$$;

drop function if exists save_household_prefs(jsonb, jsonb, text, integer);

create or replace function save_household_prefs(
    p_appliances jsonb,
    p_dietary jsonb,
    p_unit_system text,
    p_servings integer
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    hid uuid;
    units text;
    serve integer;
begin
    if auth.uid() is null then
        raise exception 'Not signed in';
    end if;
    select hm.household_id
      into hid
      from household_members hm
     where hm.user_id = auth.uid()
     limit 1;
    if hid is null then
        raise exception 'You are not in a household';
    end if;
    units := case when p_unit_system in ('Imperial', 'Metric') then p_unit_system else 'Imperial' end;
    serve := least(12, greatest(1, coalesce(p_servings, 4)));
    update households
       set appliances = coalesce(p_appliances, '[]'::jsonb),
           dietary = coalesce(p_dietary, '["None"]'::jsonb),
           unit_system = units,
           servings = serve
     where id = hid;
    return 'ok';
end;
$$;

alter table households enable row level security;
alter table household_members enable row level security;
alter table pantry_inventory enable row level security;
alter table saved_recipes enable row level security;
alter table custom_tags enable row level security;
alter table grocery_lists enable row level security;

drop policy if exists "pantry_inventory_all" on pantry_inventory;
drop policy if exists "saved_recipes_all" on saved_recipes;
drop policy if exists "custom_tags_all" on custom_tags;
drop policy if exists "grocery_lists_all" on grocery_lists;

drop policy if exists "households_select" on households;
create policy "households_select" on households
    for select using (id in (select current_household_ids()));

drop policy if exists "household_members_select" on household_members;
create policy "household_members_select" on household_members
    for select using (household_id in (select current_household_ids()) or user_id = auth.uid());

drop policy if exists "pantry_inventory_household" on pantry_inventory;
create policy "pantry_inventory_household" on pantry_inventory
    for all using (household_id in (select current_household_ids()))
    with check (household_id in (select current_household_ids()));

drop policy if exists "saved_recipes_household" on saved_recipes;
create policy "saved_recipes_household" on saved_recipes
    for all using (household_id in (select current_household_ids()))
    with check (household_id in (select current_household_ids()));

drop policy if exists "custom_tags_household" on custom_tags;
create policy "custom_tags_household" on custom_tags
    for all using (household_id in (select current_household_ids()))
    with check (household_id in (select current_household_ids()));

drop policy if exists "grocery_lists_household" on grocery_lists;
create policy "grocery_lists_household" on grocery_lists
    for all using (household_id in (select current_household_ids()))
    with check (household_id in (select current_household_ids()));

grant usage on schema public to authenticated;
grant select on households to authenticated;
grant select on household_members to authenticated;
grant select, insert, update, delete on pantry_inventory, saved_recipes, custom_tags, grocery_lists to authenticated;
grant execute on function current_household_ids() to authenticated;
grant execute on function create_household(text) to authenticated;
grant execute on function join_household(text) to authenticated;
grant execute on function leave_household() to authenticated;
grant execute on function delete_household() to authenticated;
grant execute on function save_household_prefs(jsonb, jsonb, text, integer) to authenticated;

-- Default Postgres grants EXECUTE to PUBLIC. Revoke from anon/public so the
-- publishable key cannot call these SECURITY DEFINER RPCs until someone is signed in.
revoke all on function current_household_ids() from public, anon;
revoke all on function create_household(text) from public, anon;
revoke all on function join_household(text) from public, anon;
revoke all on function leave_household() from public, anon;
revoke all on function delete_household() from public, anon;
revoke all on function save_household_prefs(jsonb, jsonb, text, integer) from public, anon;
