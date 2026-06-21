# CS2 Template Assets

Place cropped template images here for visual CS2 event detection.

Expected MVP files:

- `scope_overlay.png`: A representative CS2 sniper scope overlay.
- `molotov_icon.png`: The HUD or weapon-selection icon for molotov/incendiary.
- `grenade_icon.png`: The HUD or weapon-selection icon for HE/flash/smoke utility.

The agent will not crash if these files are missing. Missing templates simply
score `0.0` confidence and emit a warning once, which keeps dry-run tuning safe
while the assets are still being collected.
