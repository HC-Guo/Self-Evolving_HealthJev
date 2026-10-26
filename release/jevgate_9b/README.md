# JevGate-9B

JevGate-9B is the System-One admission gate of HealthJev: a frozen `Qwen/Qwen3.5-9B-Base` backbone with a LoRA adapter (r = 16, alpha = 32) and a 256-dim pointer head that scores whether a distilled experience should be admitted to the agent's long-term memory.

## Contents

| File | Description |
|------|-------------|
| `head.pt` | Pointer head (8,393,087 bytes) |
| `adapter_config.json` | LoRA configuration |
| `release.json` | Per-task deployment thresholds, evaluation threshold, and SHA-256 checksums |
| `LICENSE` | Apache-2.0 |

The 166 MB LoRA adapter weights (`adapter_model.safetensors`) are hosted on Hugging Face: <https://huggingface.co/lihaoran51/JevGate-9B>

## Download the adapter weights

```bash
pip install -U huggingface_hub
hf download lihaoran51/JevGate-9B adapter_model.safetensors --local-dir .
```

## Verify

```bash
sha256sum adapter_model.safetensors
# cf1827f87615b821e78ae1f5b2957df79494829cda9209993d587186e55fc0a3  adapter_model.safetensors
```

All released checksums (adapter, pointer head, config) are recorded in `release.json`.

## Load

```python
import torch
from huggingface_hub import hf_hub_download
from peft import PeftModel
from transformers import AutoModelForCausalLM

repo = "lihaoran51/JevGate-9B"
base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.5-9B-Base", torch_dtype=torch.bfloat16)
model = PeftModel.from_pretrained(base, repo)
head = torch.load(hf_hub_download(repo, "head.pt"), map_location="cpu")
```

Loading is base model + released LoRA + released pointer head; no second upstream adapter is involved.

## Deployment thresholds

Evaluation threshold: 0.5. Per-task deployment thresholds are listed in `release.json` (`deployment_thresholds`): diabetes_readmission 0.88, geneturing 0.38, odir5k 0.90, icu_sofa 0.50, mimic_icd 0.52, medcalc 0.30.
