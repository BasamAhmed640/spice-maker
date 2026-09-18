# SYNTHETIC SWITCH CONTRACT

**This is a test fixture, not device data.** There is no real part number, no
vendor, and no silicon behind any number in this file. Everything here exists so
that BoardModeler's board-level verification path can be exercised end to end
under version control, and so that the pipeline's refusal to present synthetic
data as device data is itself testable.

Every requirement extracted from this file carries:

* `origin = TEST_FIXTURE`
* `evidence.extraction = synthetic_fixture`
* a `doc_id` of `doc_synthetic_switch_contract`

Requirements that a real device would have to satisfy are deliberately declared
**as assumptions or as gaps** where the fixture cannot know them (see
§7 Clock availability). A fixture is not evidence about any real part, and no
report may imply otherwise.

---

## 1. Device

|Item|Value|
|---|---|
|Function|PCIe-switch-like bring-up fixture (upstream port + one lane group)|
|Part number|`SYNTH-PCIE-SW-0` (fictional)|
|Package|`SYNTH-BGA-64` (fictional)|
|Refdes|`U1`|

## 2. Supply domains

|Domain|Nominal|Allowed range|Notes|
|---|---|---|---|
|`3V3`|3.3 V|3.135 V … 3.465 V|main I/O domain|
|`1V8`|1.8 V|1.710 V … 1.890 V|core/auxiliary domain|

Each domain's pins must sit on a net whose declared domain is that domain. Two
domains shorted together, a domain pin left unconnected, or a pin pulled up to the
wrong domain are all connection errors — §5 of the datasheet-equivalent here.

Per-rail current profile (used to size the loads in the demo board):

|Domain|Static|Step at 2 ms|Peak allowed|
|---|---|---|---|
|`3V3`|250 mA|+150 mA|500 mA|
|`1V8`|400 mA|+200 mA|800 mA|

## 3. Reset (`PERST#`)

|Property|Value|
|---|---|
|Polarity|Active low|
|Direction|Input to `U1`|
|Required|Yes; must not float|
|Hold|Must be held low while **either** domain is outside its allowed range|
|Release|Only after both domains are valid **and** both power-good signals are asserted|
|Release delay|At least 1 ms and at most 100 ms after the last prerequisite|
|Strap sampling|`CONFIG[0..2]` are sampled between 1 ms and 5 ms after `1V8` becomes valid|

## 4. CONFIG straps

|Property|Value|
|---|---|
|Pins|`CONFIG0`, `CONFIG1`, `CONFIG2`|
|Sampling window|1 ms … 5 ms after `1V8` valid|
|Values|`000` … `110` are documented; `111` is **invalid** and must be reported|
|Change after the window|A strap that changes after the window closes is a connection/timing error|

## 5. Sideband (`SMB_CLK`, `SMB_DAT`)

|Property|Value|
|---|---|
|Topology|Open drain|
|Pull-up domain|`3V3`|
|Required|Yes|
|Idle level|Must reach at least 0.9 × 3V3 within 5 µs of being released|
|Wrong-domain pull-up|A pull-up to `1V8` is a connection error even though the link may appear to work|

## 6. Clock request (`CLK_REQ#`)

|Property|Value|
|---|---|
|Polarity|Active low|
|Topology|Open drain|
|Pull-up domain|`3V3`|
|Meaning|Asserted when `U1` requires `REFCLK_100M`|

## 7. Clock availability — an explicit assumption, not a verified path

`REFCLK_100M` is sourced from a component that this fixture **does not model**.
Therefore:

* any conclusion that depends on the clock being present is reported as
  **UNKNOWN** with the reason `clock_availability_assumed`, never as a pass;
* the coverage artifact lists the clock path under "not dynamically covered";
* `CLK_REQ#` may be measured, but "the switch booted" may not be claimed from it.

`PRECONDITIONS_SATISFIED` (below) is published as a **diagnostic signal only**. It
is not a pin of `U1`, and it is not proof that the device booted.

## 8. Unmodelled scope (recorded, not hidden)

* PCIe lanes are **not** modelled; all lane behaviour is outside dynamic coverage.
* The configuration EEPROM/SMBus protocol is not modelled.
* No temperature dependence is modelled anywhere in this fixture.
* The externally-visible electrical characteristics (drive strength, jitter,
  equalisation) are out of scope for a bring-up check.

## 9. Partial power

Any pin on a domain that is unpowered must not be driven externally. A pin driven
while its own domain is dead is the classic back-powering error and must be
reported with the pin, its domain, and the observed net.
