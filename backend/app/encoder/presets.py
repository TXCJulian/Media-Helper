"""Pure parsing of HandBrake preset documents.

A preset file is a tree: folder nodes carry ``"Folder": true`` and hold
children under ``ChildrenArray``; only leaves are usable presets. Nothing here
touches the filesystem or the network.

Deliberately duplicated from the encoder service's module of the same name
rather than shared. The two repos release independently, and a shared package
would couple their release cycles for eighty lines of pure tree-walking.
"""

from dataclasses import dataclass
from typing import Any


class PresetError(ValueError):
    """Raised when a preset document is malformed or a preset is unusable."""


@dataclass(frozen=True)
class NamedPreset:
    """One usable preset, plus the verbatim leaf to hand the encoder."""

    name: str
    encoder: str
    video_preset: str
    file_format: str
    body: dict


def iter_presets(doc: dict) -> list[dict]:
    """Every leaf preset in *doc*, depth-first, in document order."""
    found: list[dict] = []

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("Folder"):
                walk(node.get("ChildrenArray"))
            else:
                found.append(node)

    walk(doc.get("PresetList"))
    return found


def parse_document(doc: dict) -> list[NamedPreset]:
    """Every leaf preset, with the fields this feature needs extracted.

    ``body`` is the original leaf object, not a reconstruction: HandBrake
    presets carry denoise/deblock filter chains, audio copy masks and HDR
    passthrough behaviour that this code never models and must not drop.
    """
    presets: list[NamedPreset] = []
    seen: set[str] = set()
    for leaf in iter_presets(doc):
        name = leaf.get("PresetName")
        if not name or not isinstance(name, str):
            raise PresetError(f"Preset has no PresetName: {leaf!r}")
        if name in seen:
            raise PresetError(
                f"Duplicate preset name {name!r}; presets are dispatched by name, "
                "so names must be unique within a document"
            )
        seen.add(name)

        encoder = leaf.get("VideoEncoder")
        if not encoder or not isinstance(encoder, str):
            raise PresetError(f"Preset {name!r} has no VideoEncoder field")

        video_preset = leaf.get("VideoPreset")
        presets.append(
            NamedPreset(
                name=name,
                encoder=encoder,
                video_preset=(
                    video_preset.strip() if isinstance(video_preset, str) else ""
                ),
                file_format=str(leaf.get("FileFormat") or ""),
                body=leaf,
            )
        )
    return presets
