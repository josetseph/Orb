# Orb — agent instructions

Read `Docs/27-development-guide.md` (conventions) and `Docs/26-decisions-and-constraints.md` (decisions) before changing behaviour.

## Rules that apply to every change

- **Every long task resumes from its last finished step.** Ingesting, transcribing, reading a PDF, answering, re-indexing, migrating, downloading: write the job down before it starts (`services/jobs.py`), save each step's output keyed by its inputs (`ingestion_checkpoint.memo`), make writes safe to repeat, resume on start, save successes only, and show "Resuming" in the UI. Nothing finished is done twice. See 26 D14 and the "Long work resumes" section of 27.
- **Model output is never repaired.** No regex extraction, no normalisation, no JSON repair: a malformed reply is a counted failure (26 D12).
- **No `.env` or environment variables for user settings.** Everything a user sets is set in the app.
- **Never touch the real data directory from tests or scripts.** Set both `ORB_DATA_DIR` and `ORB_PATHS_FILE` to a scratch location before importing or booting the backend.
