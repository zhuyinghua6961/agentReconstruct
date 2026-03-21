# FastQA Config

Service-level env templates for the future `fastQA` backend live here.

This service should own only QA execution configuration.

Expected runtime contract:
- `FASTQA_SERVICE_CONFIG_ROOT`
- `FASTQA_SERVICE_STATE_ROOT`
- `FASTQA_SERVICE_RUNTIME_ROOT`
- `FASTQA_SERVICE_ASSET_ROOT`

Phase-1 expectation:
- trust gateway-normalized `route`
- do not require conversation/upload/document modules to boot
