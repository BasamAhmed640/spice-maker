# Saving and checking a key

SETUP's **SAVE & CHECK KEY** stores the entered key in a local file inside this folder's
data directory,
clears the password field, and starts a background connection check. The window
stays responsive and stops waiting after 15 seconds. Closing SETUP or changing
provider discards the old check's result.

The check sends only a short request to acknowledge the connection. It does not
send a datasheet or model. One request is made, with no automatic retries; it may
use a small amount of provider credit. The selected provider, model and reasoning
settings are retained. HTTP verification caps the response budget at 256 tokens;
exhausting this small budget can still establish that the service accepted the
key and selected model. This is not a model-accuracy or available-quota guarantee.

- VERIFIED: an authenticated inference response was observed.
- REJECTED: the provider refused authentication or permissions.
- UNVERIFIED: connection, timeout, quota, model configuration, incomplete response,
  or a missing dependency prevented a conclusion. This does not mean the key is invalid.

The key remains saved after an inconclusive or rejected check so the user can
resolve account or connection issues. Paste a replacement and save again to retry.
Verification is not a prerequisite that blocks a later model build.

IBM Bob verification uses Bob Shell 2.x with a one-turn limit, a 0.05 Bobcoin cap,
an empty temporary workspace, and all documented tool groups disabled. IBM's
license must first be accepted by the user. An Inference API key avoids needing
the additional team ID required by General keys. The check never accepts the
license automatically and never puts the key in command arguments.

One key per edition is saved in the extracted folder's own `data/credentials.json`
(Bob edition: `data/credentials.bob.json`) as ordinary local JSON holding only the format
version, provider identifier and key. Nothing is bound to Windows: no DPAPI ciphertext,
no registry entry, no Credential Manager entry and no user-profile location, and no
configuration or credential is ever written outside this folder. The file is not
encrypted; anyone who can read the folder can read the key, so keep the copy private.
Saving another provider replaces the earlier key. Because updates replace `app/` only,
the saved key survives them.

Keys never belong in settings JSON, model output, the repository, installer or ZIP.
An update on the same computer reuses its local file; a fresh download on another
computer has no key. Copying the whole extracted folder carries the key with it.
Programs or users that can read the folder can read the file, so keep the device and
the folder protected. There is no recovery password or cloud backup
managed by this app. To forget the saved key, close the app and delete the credential
file (`data/credentials.json`).

Settings JSON stores the configuration version and non-default preferences only.
Model files, extracted datasheet evidence and simulation results remain in the
chosen model folder for inspection, caching and retesting. The app has no telemetry;
third-party provider tools have their own retention and telemetry settings.

HTTPS uses certificate verification. Credential-bearing HTTP requests do not follow
redirects. The setup UI shows fixed diagnostic messages rather than provider response
bodies or exception strings that could echo a secret. No Windows security policy
needs to be disabled for credential checking.

References: [IBM Bob API keys](https://bob.ibm.com/docs/ide/account/api-keys),
[Bob non-interactive sessions](https://bob.ibm.com/docs/shell/getting-started/start-bobshell-non-interactive),
[Bob settings and optional telemetry](https://bob.ibm.com/docs/shell/configuration/configuring).
