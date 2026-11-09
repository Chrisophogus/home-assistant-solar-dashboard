# Home Assistant Solar Dashboard

**Sanitized private-repository source: `home-assistant-solar-dashboard`. Prepared and tested locally; published to this private repository. Publication does not deploy changes or restart Home Assistant.**

**Source-review candidate only, not a deployment or experimental-worker activation release.** Test/scan evidence is included under `provenance/`.

This source backup does **not** isolate the installed card from HACS updates. No HACS migration, resource change, rename or update protection has been deployed.

## Included

* `custom_components/solar_finance/`: full current integration, including the deployed continuous corrected-view repair. Installation-specific defaults/evidence are sanitized in this copy. Domain/component names unchanged.
* `www/solar-dashboard/sigenergy-flow-card.js`: full modified current card, based on HACS **v52**. Six finance tiles (two periods × three columns), weather, flows, reserve controls and Sigen iframe. Identifying artwork/vehicle bindings removed; generic SVG included.
* Portable synthetic Python and Node tests. No recorded household samples/captures or database.
* `experimental/predbat_shadow/`: our optimiser wrappers, receipt-time archive/budget, conserved replay and matched fourth-card reducer. **NOT activation-ready. No connected fourth tile, upstream code, binaries or runtime.**

This is not a byte-identical production backup. [SANITIZATION.md](SANITIZATION.md) records repository-only edits; `provenance/source-inputs.json` records original source hashes. Packaged hashes and scan report accompany the ZIP.

## Review and later installation

See [installation](docs/INSTALLATION.md), [example card](examples/dashboard.yaml), [limitations](docs/LIMITATIONS.md) and [attribution](THIRD_PARTY_NOTICES.md). Do not copy the entire repository into HA. Integration installation needs a restart **only if separately authorised later**. Defaults here are synthetic, not evidence for money or hardware limits.

## Offline tests

Python 3.13 and Node 22+, no HA credentials/server required:

```sh
python3 -B -m unittest discover -s custom_components/solar_finance/tests -p 'test*.py'
python3 -B -m unittest discover -s experimental/predbat_shadow -p 'test*.py'
node --check www/solar-dashboard/sigenergy-flow-card.js
node tests/frontend.cjs
python3 tools/privacy_scan.py
```

Tests create tiny synthetic in-memory/temp SQLite databases; none are distributed. No test calls Solcast or starts/downloads Predbat. Actual optimiser needs separately fetched/licence-reviewed pinned upstream and a confined runtime.

## Privacy/licensing

No HA config/storage, photos, credentials, observations, logs or backups should ever be committed, even privately. `.gitignore` and scans are defence in depth, not guarantees.

Card copyright © 2025–2026 Jean-Yves Avenard, Apache-2.0; exact pinned licence preserved. Modifications identified. No blanket licence has been assigned to locally authored finance/shadow work: applicable owners retain their rights pending owner decision. Private publication is not a relicensing grant. Predbat personal-use terms are separate; its implementation is excluded.
