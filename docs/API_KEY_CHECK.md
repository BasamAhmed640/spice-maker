# Saving and checking a provider key

The setup wizard accepts an API key through a hidden prompt, stdin, or `--key-env NAME`, where NAME is an environment variable's name. The value itself must never be a command argument. The selected key is saved as plain local JSON in this copy's `data/credentials.json`. It is **not encrypted**; anyone who can read this folder can read it. Keep the folder private and never share `data/`.

With Internet access enabled, setup makes one short connection check through the selected provider. It sends no datasheet or model. A successful authenticated inference reply is VERIFIED. An explicit authentication or permission refusal is REJECTED. A timeout, quota, connection, incomplete reply, or model-configuration problem is UNVERIFIED; that result does not prove the key is invalid. Verification does not establish available quota or model accuracy and may consume a small amount of provider credit.

A rejected or unverified check makes the current setup attempt exit nonzero with a plain explanation. The saved key and chosen settings remain in this copy so you can correct the account or connection issue and rerun Setup. The application still refuses a build when the provider cannot be used. With Internet access off, setup saves the key without a network check and says so.

No key value is printed, logged, written to a model, sent to LTspice, or included in the source ZIP. To forget a saved key, delete `data/credentials.json` inside this copy. The app has no telemetry.
