# Optional learned quality models

FRAME's core does not download models or require a network. Learned scoring uses a local scalar-output ONNX IQA model installed at `.frame-data/models/quality/` with `model.json` and its weights file.

The manifest fields are `identity`, `version`, `weights`, `sha256`, `output_min`, and `output_max`. FRAME verifies the checksum before loading, records model/preprocessing/checksum/source metadata with every result, and uses CPU unless an ONNX Runtime CUDA provider is available and selected. A model must accept NCHW 224×224 ImageNet-normalised RGB tensors and return one scalar per image.

No third-party weights are bundled because a compatible model's license and checksum must be reviewed for each release. The current environment has ONNX Runtime and CUDA-capable PyTorch, but no specialised IQA weights, so genuine pretrained IQA inference remains unverified. Classical processing is the permanent offline fallback.
