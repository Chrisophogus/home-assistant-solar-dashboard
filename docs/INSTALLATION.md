# Installation (review guide, not executed)

1. Review licences, sanitization and offline tests. This is a manually maintained custom integration/card, not an official HACS release or ready HACS repository. Back up separately; never commit a backup.
2. If later authorised, copy only `custom_components/solar_finance/` to HA's matching path. Integration code needs an HA **restart**; do not restart during source review. Synthetic CI does not establish full HA compatibility.
3. Add **Solar Finance** via Settings → Devices & services. Select sources. Configure independently verified dated evidence/tariffs/model in options. Example defaults are not household evidence or recommended physical limits.
4. If later authorised, copy `www/solar-dashboard/` to `config/www/solar-dashboard/`, add module resource `/local/solar-dashboard/sigenergy-flow-card.js` via dashboard UI. Review existing resources first; do not load two versions registering the same element.
5. `examples/dashboard.yaml` is a card example, not an exported live dashboard. Generic SVG depicts no real house; installed layout geometry is retained and may require visual review.
6. Set entity overrides using keys in JS `DEFAULT_ENTITIES`. Prefer explicit `entities.ev_soc`; optional `ev_device_id` and `ev_config_entry_id` enable scoped registry discovery only when locally supplied. Never commit actual IDs. `weather_entity` selects current weather; `weather_image_template` optionally selects local `{phase}`/`{category}` files. No photo pack included. Conventional Sigen source defaults remain for interoperability.

## Runtime behaviour

The card retains interactive reserve/phase/charger control methods. **It is not a read-only dashboard**; source export changes no production settings. Review controls and permissions before using it. The Sigen iframe points to the official public application; browser-local sign-in/chat is not exported. Sun calculations consume HA location at runtime, but no coordinates are embedded here. Public licence/namespace/vendor/documentation URLs are retained, not private endpoints.

Do not start/configure the experimental server or add scheduled jobs from this guide. Packaging, credential transport, resource hardening, trusted snapshots and fourth-card integration remain incomplete.
