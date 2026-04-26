# Quiz Fix TODO

## Issue 1: Correct Answers Marked Wrong
- [x] Added `getOptionLetter()` helper in quiz.html
- [x] Fixed `calculateScore()` to compare letters
- [x] Fixed `generateReview()` to compare letters

## Issue 2: Only 10 Questions Generated
- [x] Added question count input to dashboard.html upload form
- [x] Updated `uploadNotes()` to pass selected count
- [x] Increased backend limit in app.py from 20 to 100

## New Feature: Section-Based Question Generation
- [ ] Add `/api/analyze-pdf` endpoint in app.py
- [ ] Update dashboard.html with section selection UI
- [ ] Update `/upload_notes` to accept section parameter
- [ ] Test end-to-end flow
