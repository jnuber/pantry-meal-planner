-- Pantry Chef: fix Supabase lint 0028 (anon can execute SECURITY DEFINER RPCs).
-- Run this in the EXISTING project SQL editor. Does not change app.py.
--
-- Keep SECURITY DEFINER + GRANT EXECUTE TO authenticated. The household RPCs
-- must run as definer because signed-in users only have SELECT on
-- households / household_members. Lint 0029 (authenticated can execute them)
-- is expected and required for create/join/leave/prefs to work.

revoke all on function public.current_household_ids() from public, anon;
revoke all on function public.create_household(text) from public, anon;
revoke all on function public.join_household(text) from public, anon;
revoke all on function public.leave_household() from public, anon;
revoke all on function public.delete_household() from public, anon;
revoke all on function public.save_household_prefs(jsonb, jsonb, text, integer) from public, anon;

grant execute on function public.current_household_ids() to authenticated;
grant execute on function public.create_household(text) to authenticated;
grant execute on function public.join_household(text) to authenticated;
grant execute on function public.leave_household() to authenticated;
grant execute on function public.delete_household() to authenticated;
grant execute on function public.save_household_prefs(jsonb, jsonb, text, integer) to authenticated;

notify pgrst, 'reload schema';
