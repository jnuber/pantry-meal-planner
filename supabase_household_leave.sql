-- Pantry Chef 2.0.6: leave / delete household. Run in the EXISTING project SQL editor.

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

grant execute on function leave_household() to authenticated;
grant execute on function delete_household() to authenticated;

notify pgrst, 'reload schema';
