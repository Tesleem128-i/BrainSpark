# TODO - Quiz generation update

- [ ] Update `templates/quiz.html` to set min question count to 50 (default 50) and improve UI text.
- [ ] Fix topic selection “Generate Questions” button enable/disable logic when `selectedTopics` is `'all'`.
- [ ] Update `app.py`:
  - [ ] Compute and store a `pdf_source_hash` for the uploaded PDF text.
  - [ ] Enforce minimum 50 and maximum 100 question_count when uploading.
  - [ ] Update `/api/generate-questions` to dedupe generated questions server-side for the same user + source_hash using `GeneratedQuestion`.
  - [ ] Ensure final stored/stored-in-session quiz has at least 50 unique questions (never repeats for same PDF/user).
  - [ ] Persist newly generated questions to `GeneratedQuestion` with source_hash.
- [ ] Run a quick smoke test by starting the app and hitting `/quiz` flow.

