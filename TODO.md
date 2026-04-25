# TODO - Fix Signup Verification Flow

## Steps
1. [x] Fix `templates/signup.html`
   - Fix JS typo: `queryQuerySelectorAll` → `querySelectorAll`
   - Add `method="POST"`, `action="/signup"`, `enctype="multipart/form-data"` to the form
   - Change success handler to redirect to `/verify-email?email=...`
2. [x] Update `app.py`
   - Add `/verify-email` route to render `verify_email.html`
   - Add `/resend-verification` route
3. [x] Update `templates/verify_email.html`
   - Wire up resend button to call `/resend-verification`


