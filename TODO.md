# Task: Support up to 100 Questions + Deduplication via Database Storage

## Plan Steps

- [x] **Step 1**: Add `GeneratedQuestion` model to `models.py` (new table for storing questions)
- [x] **Step 2**: Update `app.py`:
  - Import `hashlib` and `GeneratedQuestion`
  - In `/upload_notes` endpoint: validate max 100 questions, compute `source_hash`, query existing user questions, inject
    them into the Gemini prompt to avoid duplicates, filter AI response against DB, store new unique questions in DB
- [x] **Step 3**: Update `templates/dashboard.html` — expand `#question-count` dropdown to include 25,30,40,50,75,100
- [x] **Step 4**: Update `templates/quiz.html` — replace 4 fixed question-count buttons with a number input (1–100)
- [x] **Step 5**: Update `templates/index.html` — update marketing text from "10-50" to "up to 100"
- [x] **Step 6**: Update `migrate_db.py` — add migration to create the `generated_question` table for existing DBs
- [ ] **Step 7**: Run migration, restart app, test end-to-end

## Notes
- All generated questions are stored per-user so the same question is never repeated across uploads.
- Deduplication is done both via the AI prompt (`Do NOT generate these existing questions`) and a post-generation DB
  exact-match filter on `question_text`.


