# Render Deployment TODO

## Steps
- [x] Step 1: Update requirements.txt (add psycopg2-binary)
- [x] Step 2: Update app.py (PostgreSQL config, env vars, production settings)
- [x] Step 3: Create render.yaml
- [x] Step 4: Create Procfile
- [x] Step 5: Create .env.example
- [x] Step 6: Create runtime.txt

## Deployment Ready!

### Files Modified
- `requirements.txt` - Added `psycopg2-binary==2.9.9`
- `app.py` - Auto-detects PostgreSQL via DATABASE_URL, env-based SECRET_KEY, PORT binding

### Files Created
- `render.yaml` - Render blueprint configuration
- `Procfile` - Gunicorn start command
- `.env.example` - Environment variable documentation
- `runtime.txt` - Python 3.11.6

### Render Dashboard Environment Variables to Set
```
DATABASE_URL      (auto-provided by Render PostgreSQL)
SECRET_KEY        (auto-generate or set manually)
GOOGLE_AI_API_KEY (your Gemini API key)
MAIL_USERNAME     (Gmail address)
MAIL_PASSWORD     (Gmail app password)
```

### Next Steps
1. Push code to GitHub
2. Connect Render to your GitHub repo
3. Add your PostgreSQL database (already have the URL)
4. Set environment variables in Render dashboard
5. Deploy!
