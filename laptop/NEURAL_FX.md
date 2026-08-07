# Neural FX (note for the `laptop/` tree)

The Neural FX engine lives at the repo root - [`../neural_fx.py`](../neural_fx.py), served by [`../server.py`](../server.py) - and is documented in [`../NEURAL_FX.md`](../NEURAL_FX.md).

The root engine loads depth weights from the **root** [`models/`](../models/) directory. The fetch helper kept here ([`fetch_aihub_models.ps1`](fetch_aihub_models.ps1)) writes into `laptop/models/`, which the engine does **not** read - prefer the root `fetch_aihub_models.ps1`, or copy the downloaded `.onnx`/`.data` files into the root `models/` afterwards.
