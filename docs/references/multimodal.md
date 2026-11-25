<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Multimodal message content

A message is a string, or a list of parts. A message stays a string when it
has no media. Never rewrite a string as a list: that would change cache keys
and run hashes.

A part is JSON:

```json
{"type": "text", "text": "..."}
```

or

```json
{"type": "image", "media_type": "image/png", "sha256": "<hex>", "name": "chart.png", "bytes": 12345}
```

`file` uses the same fields and is PDF only. There is no base64, path or URL
on the message. Bytes live in a media store keyed by sha256. Audio, video,
and non-PDF files are refused on the live path, not only at construction.
Media is accepted on user turns only; system and assistant stay text.

## Media store

The host builds the store and hands it to the LLM protocol layer the same way
it hands `secret_resolver` and `inner`: a constructor argument on
`HttpLlmProtocol`, filled from `wrap` / `from_infra_spec`. mas-ctl uses a
directory named `media/` next to the run's events (or under the XDG state
root when there is no events file). Layers read bytes only when they build
the provider request.

A checkpoint holds the parts (digest, type, name, size). Restore needs the
same media directory; the checkpoint file does not embed the bytes.

## Capability check

`ModelCatalog.accepts_modality` decides whether a model takes `image` or `pdf`.
The live engine checks every user message before it calls the provider. The
HTTP protocol layer checks the same way, so a direct `achat_completion` is
gated too. An unknown model, or a catalog entry with no `modalities` field,
still accepts image and PDF; it does not accept audio, video, or other files.
A mismatch raises `ModalityNotSupported` (fatal, not retryable) and no
provider is called. A missing digest in the media store raises `MediaMissing`.
The directory store reads only a 64-character hex digest under its root.

## Layers

Every protocol layer maps image and PDF file parts from one shared helper.
Chat completions emit `image_url` / `file`. Open Responses emit `input_image`
/ `input_file`. Anthropic Messages emit `image` / `document` base64 sources.
Gemini and Vertex emit `inline_data`. Bedrock Converse emit `image` /
`document` blocks. Tool results that return media, audio, video, fetching
URLs, and a multimodal judge are out of scope.

The prompt-size estimate counts text as before and adds 256 tokens per media
part. That number is an estimate. Provider usage is the billable count.

Dataset syntax is in [dataset.md](../manifests/dataset.md). The feature matrix
is in [llm-providers.md](../manifests/llm-providers.md).
