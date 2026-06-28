# SAP C2C Contact Finder (local web app)

Paste your resume → it detects your SAP module → pulls matching C2C postings from
corptocorp.org → extracts the recruiter's **name, email, and phone**.

## Run it
1. Install Python 3.9+ (python.org).
2. In this folder, open a terminal and run:

   ```
   pip install -r requirements.txt
   python app.py
   ```

3. Open your browser to **http://localhost:8000**
4. Paste a resume, click the button. Matched jobs with recruiter contacts appear.

## How it works
- Module detection: same logic as the finder (EWM, GRC, FICO, ABAP, MM, etc.).
- Source: corptocorp.org C2C hotlists — these postings include the recruiter's
  contact inline, which job-board APIs (Dice/Indeed) don't expose.
- Contacts are scraped from each posting; always verify before reaching out.

## Notes
- This runs locally so it has the network access the in-app panel doesn't.
- Some postings don't include a direct contact — those are skipped.
- Want Dice/LinkedIn breadth or Apollo enrichment? That's the paid Layer 2.
