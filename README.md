# Spice Maker

Spice Maker builds first-order LTspice component models from cited datasheet evidence.
The default engine turns verified facts into a typed design, renders SPICE in code,
and tests the exact file against fixed source limits. It produces a library, symbol,
model card and reproducible test records. Missing support or evidence stops the build.

Version **1.8.0**. The General edition uses direct HTTPS AI providers when an AI stage is needed.

## Install on Windows

<!-- install-steps:start -->
Download the curated [Windows 1.8.0 release](https://github.com/BasamAhmed640/spice-maker/releases/tag/v1.8.0).
It contains the installer and its checksum, without the source history and development files.
Python is bundled; LTspice is a separate prerequisite.

Open PowerShell and paste this entire block. It creates one versioned folder in Downloads,
downloads over HTTPS, and stops before setup if the installer checksum is wrong.
If that versioned folder already exists, open its `Start.cmd` or choose a fresh folder name.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $spiceFolder = Join-Path $env:USERPROFILE 'Downloads\SpiceMaker-1.8.0'
    if (Test-Path -LiteralPath $spiceFolder) { throw 'This version folder already exists; use its Start.cmd or choose a fresh folder name.' }
    New-Item -ItemType Directory -Path $spiceFolder | Out-Null
    $spiceZip = Join-Path $spiceFolder 'SpiceMaker-1.8.0-Windows-x64.zip'
    curl.exe --fail --location --proto =https --proto-redir =https -o $spiceZip https://github.com/BasamAhmed640/spice-maker/releases/download/v1.8.0/SpiceMaker-1.8.0-Windows-x64.zip
    if ($LASTEXITCODE -ne 0) { throw 'Download failed; setup was not started.' }
    Expand-Archive -LiteralPath $spiceZip -DestinationPath $spiceFolder
    $spiceInstaller = Join-Path $spiceFolder 'Install.exe'
    if ((Get-FileHash -LiteralPath $spiceInstaller -Algorithm SHA256).Hash -ne '7a0d0378cc5c08a4a1c830a156341f31b36a64efbd1929ec4443914e44130a04') { throw 'Installer checksum mismatch; setup was not started.' }
    & $spiceInstaller
}
```

Installer version **1.8.0**. SHA-256 of `Install.exe`:
`7a0d0378cc5c08a4a1c830a156341f31b36a64efbd1929ec4443914e44130a04`, also published in `SHA256SUMS.txt`.
This unsigned application may trigger a Windows security prompt. The checksum checks the
published bytes; it does not establish a signed publisher identity. These steps do not
change Windows security settings or PowerShell policy.

After setup, use `Start.cmd` in that folder. Keep the General and Bob editions in separate
folders. Setup preserves saved settings and models when updating an existing installation
and checks the installed command-line engine against the packaged desktop engine.
<!-- install-steps:end -->

## Make a model

Open SETUP, choose your LTspice executable with BROWSE and run its check. In the main window,
enter the exact part/package, choose the datasheet and a per-part output folder, and press GO.
Keep **Code-built behavioral** and **FULL VERIFICATION** selected. The compact desktop keeps
the elapsed timer; finished models and their reports stay together in the output folder.

For the supplied UCC28251 Rev. E PDF, choose **UCC28251PW** or **UCC28251PWR** for TSSOP-20.
The reviewed primary-side configuration ties VSENSE to VREF, connects COMP to FB/EA−,
and drives REF/EA+ from the external control signal. Read the generated model card before
connecting a power stage; RGP/QFN and an unspecified package are blocked.

The final installed acceptance builds completed in about **51 seconds** on this machine,
with **15 PASS, 0 FAIL and 21 UNKNOWN** rows and zero AI calls. They deliver a functional
first-order primary-side controller; overall verdict stays **UNKNOWN** because full prebias,
hiccup recovery, synchronization, temperature dependence and other behavior are unqualified.
This single run is not a promise for every part or computer. See
[current source and release evidence](docs/evidence/2026-09-30-pwm-release/REPORT.md) and
[UCC28251 source review](docs/evidence/2026-09-30-ucc28251/REPORT.md).

## Current support

| Component | Reviewed scope |
| --- | --- |
| UCC28251PW / PWR | TSSOP-20, primary-side, resistor-timed, level enable, 25 °C; limited UNKNOWN coverage |
| TPS54332 / TPS54332DDA | DDA buck model with required exposed-pad connection; limited UNKNOWN coverage |
| LM358 | Reviewed common eight-pin D / DGK / P / PS / PW electrical pinout; partial dual-op-amp coverage, no PCB-footprint claim |
| TPS54331 | Reviewed partial evidence; missing essential coverage/package evidence still blocks delivery |
| MCU, FPGA, CPLD, processor, SoC | Excluded on every engine route |

Other parts need their own reviewed source/package evidence, implemented behavior and independent
tests. An extensible engine does not mean most PCB components are implemented today.
PASS describes a measured row under its stated conditions. UNKNOWN is an explicit coverage gap;
a successful simulator exit or correct pinout alone does not prove device accuracy.

## Engine rules

The [engine guide](docs/ENGINE_GUIDE.md) is the implementation contract. The unchanged
[original review page](docs/engine-refactor.html) is preserved for reference and RAG.

- `behavioral` is the default: cited facts → typed design → deterministic library → independent
  LTspice checks. AI extraction is the only default AI stage; exact reviewed evidence or validated
  caches can avoid it. No AI authoring, test planning or repair runs as a silent fallback.
- `pin_only` is an explicit limited pin interface, with no component-function claim.
- `legacy_ai` explicitly enables free-form AI authoring and bounded repair. Extra AI planning
  requires an explicit opt-in. Quick drafts remain electrically unverified.

The same validated facts and renderer version produce identical library bytes. Tests keep their
source limits when the model changes. Delivered provenance binds the design, pinout, source,
frozen tests and simulator evidence to the exact file. Unsupported new builds stop before paid
extraction when the publication gate cannot deliver them. Extraction is bounded; embedded PDF
text is used first, with OCR reserved for pages that need it.

Configure a provider and key only for a requested AI stage. Reviewed UCC28251 evidence needs neither. The General edition does not install or launch a CLI agent.

## Development and history

The source project requires Python 3.14. From a checkout, create `.venv`, install
`requirements.txt` and `requirements-dev.txt`, then run pytest and Ruff. The portable installer
provides its own Python and checksum-verified wheels for normal use. Release verification
compares source, installed-wheel and frozen-desktop engine identities and tests a delivered model.

See [installer details](installer/README.md), [portable storage](docs/PORTABLE_STORAGE.md),
[current status](docs/STATUS.md), [decisions](docs/DECISIONS.md),
[engine acceptance](docs/evidence/2026-09-29-engine-refactor/REPORT.md), and
[source-backed pinout publication](docs/evidence/2026-09-29-m6-pinout/REPORT.md).
The previous long README is retained as [historical notes](docs/archive/README-before-1.8.0.md);
its old install commands and product claims are not current instructions.
