-- Main word, alt definitions, and subfamilies.
-- Safe to run more than once in the Supabase SQL editor.

-- A secondary meaning shown smaller under the main definition.
alter table words add column if not exists alt_english text;

-- Claude's suggestions can now propose an alt definition too.
alter table definition_suggestions add column if not exists original_alt text;
alter table definition_suggestions add column if not exists suggested_alt text;
