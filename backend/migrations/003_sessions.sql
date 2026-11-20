-- Slice 4: anonymous sessions. profiles.session_id stores sha256(token), never the token itself,
-- so a database leak cannot be replayed as a session. Rows untouched for 30 days are pruned at startup.
ALTER TABLE profiles ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX profiles_updated ON profiles (updated_at);
CREATE INDEX messages_session_day ON messages (session_id, role, created_at);
