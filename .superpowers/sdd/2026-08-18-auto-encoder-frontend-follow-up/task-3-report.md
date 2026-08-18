# Task 3 report: preset preview and selected import

## Delivered

- Added `POST /api/encoder/presets/preview`. It parses HandBrake leaf presets,
  checks encoder capability, reports each leaf's name, encoder, support status,
  and unsupported reason, and never writes to the preset store.
- Extended `POST /api/encoder/presets` with optional `include_names`.
  Explicit selections import only supported selected leaves, report selected
  unsupported leaves as `skipped`, and list every non-selected leaf in
  `unselected`. Unknown selected names receive the existing flat
  `invalid_preset` error envelope.
- Preserved omitted-selection compatibility: it considers all leaves and keeps
  the existing all-unsupported rejection behavior.
- Kept HandBrake leaf bodies intact through the selected import path.
- Added typed frontend preview and import-result interfaces plus
  `previewEncoderPresets(document)` and
  `importEncoderPresets(document, includeNames?)` API helpers.

## Test-first evidence

The initial focused route test run failed only for the intended missing
behaviour: preview returned 405, selected import still skipped unselected
leaves, and unknown names were accepted. After implementation:

- `pytest tests/test_encoder_routes.py -q --basetemp .pytest-tmp-task3-green`:
  59 passed, 1 skipped.
- `pytest tests/test_encoder_presets.py -q --basetemp .pytest-tmp-task3-presets`:
  9 passed.
- `npm test -- --run src/__tests__/encoderApi.test.ts`: 8 passed.
- `npm run build`: passed.

The backend runs emitted the pre-existing FastAPI/TestClient deprecation
warning. `git diff --check` reported no whitespace errors.
