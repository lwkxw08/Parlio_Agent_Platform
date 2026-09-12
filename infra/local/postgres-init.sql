-- Local dev only. The API connects as a non-superuser so row-level security is enforced
-- (superusers bypass RLS). Production roles are provisioned by Terraform with real secrets.
CREATE ROLE parlio_app LOGIN NOSUPERUSER PASSWORD 'parlio_app';
ALTER SCHEMA public OWNER TO parlio_app;
