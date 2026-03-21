# Resource

This directory is the shared resource root for the single-repository, multi-service layout.

Rules:

- `resource/config/`: configuration templates and service-scoped env examples
- `resource/assets/`: read-only shared assets
- `resource/state/`: durable mutable state, separated by environment and service
- `resource/runtime/`: pid/log/temp/runtime files, separated by environment and service
- `resource/mounts/`: mount-point conventions for object storage and external disks
- `resource/scripts/`: cross-service operational helpers

This directory does not contain service business code.

## Ownership

- `gateway`: no business state, runtime only
- `public-service`: auth/conversation/upload/document truth data
- `highThinkingQA`: thinking-mode QA execution state only
- `fastQA`: fast-mode QA execution state only
- `patent`: patent QA execution state only

## Current Phase

- The directory skeleton exists.
- Existing root-level `highThinking` runtime paths have not yet been migrated here.
- `gateway/` and `public-service/` still run as independent subprojects.
