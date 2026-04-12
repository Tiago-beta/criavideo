-- Add ON DELETE CASCADE to publish_jobs.render_id FK
ALTER TABLE publish_jobs DROP CONSTRAINT IF EXISTS publish_jobs_render_id_fkey;
ALTER TABLE publish_jobs ADD CONSTRAINT publish_jobs_render_id_fkey
    FOREIGN KEY (render_id) REFERENCES video_renders(id) ON DELETE CASCADE;
