# Solis flow overlay

## Scope and trade-offs

Adapt the repository's modified v52 card in an isolated feature branch. Reusing
the existing scene, weather and finance layout preserves the reference design
and avoids a second framework. A separate, manually managed card resource and
unique element name let the preview coexist with the existing dashboard and
avoid HACS replacing the preview. Use the generic artwork until the owner
supplies a photo. Keep household identifiers and the eventual photo out of Git.

## First build

1. Resolve the existing Predbat Solis power and battery entities locally. Map
   solar, household load, grid, battery power and state of charge. Verify units
   and signs against normal operating history and preserve unavailable states.
2. Keep the grid, solar, household and battery branches distinct. React to HA
   state changes. Keep idle paths visible and subdued. Label grid and battery
   values without directional claims until their signs are verified.
3. Make the preview display-only. Keep reserve, charger controls and the Sigen
   iframe out of the Solis view. Include an EV branch only once its measurements
   and meter boundary are established.
4. Test the saved preview against Home Assistant on mobile, desktop and ultrawide.
   Back up affected dashboard configuration before adding resources. Roll back
   by removing the preview dashboard and its dedicated resource.
5. Keep finance separate. Verify common measurement coverage, independently
   dated tariffs and Solis battery settings before enabling comparisons. Review
   Sigenergy-specific reserve bindings and keep hourly reporting separate from
   live visuals. Leave experimental forecasting inactive.
