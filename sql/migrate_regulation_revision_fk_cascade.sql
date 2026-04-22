-- Migration: add ON DELETE CASCADE to regulation_revision FKs
--
-- regulation_revision has two composite FKs referencing regulation_version,
-- but both were defined without ON DELETE CASCADE. This causes the cascade
-- chain from complex → regulation_version to be blocked when a complex is
-- deleted. The DELETE /admin/api/complexes/{id} handler works around this by
-- explicitly deleting regulation_revision rows first; this migration removes
-- the need for that workaround.
--
-- Run once against the target DB. Idempotent via DROP IF EXISTS + re-add.

ALTER TABLE regulation_revision
    DROP CONSTRAINT IF EXISTS regulation_revision_complex_id_from_version_fkey,
    DROP CONSTRAINT IF EXISTS regulation_revision_complex_id_to_version_fkey;

ALTER TABLE regulation_revision
    ADD FOREIGN KEY (complex_id, from_version)
        REFERENCES regulation_version(complex_id, version) ON DELETE CASCADE,
    ADD FOREIGN KEY (complex_id, to_version)
        REFERENCES regulation_version(complex_id, version) ON DELETE CASCADE;
