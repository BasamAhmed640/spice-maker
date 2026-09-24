# Spice Maker project rules

- Work in this repository and its project-local `.venv` only. Use `requirements.txt` for the standard Python setup.
- Use PowerShell syntax for Windows instructions. Do not suggest a downloaded script piped into a shell.
- Do not search the computer for LTspice. A user must choose an executable with SETUP's BROWSE button or pass an explicit path to a simulator call.
- The model author is a proposal source. Only application-owned LTspice runs and observed `.raw` and `.log` artifacts may produce a PASS verdict.
- A quick structural check must remain electrically unverified. Record unavailable tests as UNKNOWN or BLOCKED with the reason.
- Keep generated files, credentials, and temporary work inside the extracted project folder. Do not log API keys.
- Before changing command execution or network behavior, inspect the exact call site and keep a fixed argument list, a bounded timeout, and an explicit child environment.
