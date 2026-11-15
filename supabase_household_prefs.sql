-- Pantry Chef 2.1.3: persist household appliances, diets, units, and servings.
-- Run in the EXISTING project SQL editor.

alter table households add column if not exists appliances jsonb not null default '["Oven", "Stovetop"]'::jsonb;
alter table households add column if not exists dietary jsonb not null default '["None"]'::jsonb;
alter table households add column if not exists unit_system text not null default 'Imperial';
alter table households add column if not exists servings integer not null default 4;

alter table households drop constraint if exists households_unit_system_check;
alter table households add constraint households_unit_system_check
    check (unit_system in ('Imperial', 'Metric'));
alter table households drop constraint if exists households_servings_check;
alter table households add constraint households_servings_check
    check (servings >= 1 and servings <= 12);

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

grant execute on function save_household_prefs(jsonb, jsonb, text, integer) to authenticated;

notify pgrst, 'reload schema';
