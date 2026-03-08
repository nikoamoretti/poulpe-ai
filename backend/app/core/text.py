import re


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    slug = _NON_ALNUM_RE.sub("-", normalized).strip("-")
    return slug or "project"

