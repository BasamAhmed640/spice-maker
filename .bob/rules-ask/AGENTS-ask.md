# Spice Maker: Ask mode

- Explain the actual verification chain: provider proposes text, the application writes the candidate, runs selected LTspice with a fixed argument list, reads `.raw` and `.log`, and compares measurements with frozen datasheet rows.
- Say when a result is UNKNOWN or BLOCKED. Do not describe quick structural checks or untested rows as electrically verified.
- Give Windows PowerShell commands for the project-local `.venv` when explaining source setup; do not suggest changing the user's global Python or extensions.
- Explain that the user chooses LTspice in SETUP with BROWSE or supplies its path explicitly; the application does not discover it.
