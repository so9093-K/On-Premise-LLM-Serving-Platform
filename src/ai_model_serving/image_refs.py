from __future__ import annotations

import re

_REGISTRY_DIGEST_IMAGE_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_LOCAL_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def is_registry_digest_image_ref(value: str) -> bool:
    return bool(_REGISTRY_DIGEST_IMAGE_RE.fullmatch(value))


def is_local_image_id(value: str) -> bool:
    return bool(_LOCAL_IMAGE_ID_RE.fullmatch(value))


def is_immutable_image_ref(value: str) -> bool:
    return is_registry_digest_image_ref(value) or is_local_image_id(value)
