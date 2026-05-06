"""Active fragment detection via `dumpsys activity <component>`.

Most AndroidX FragmentManager-based apps log their fragment tree under the
resumed activity dump. We pick the top-level visible (mHidden=false +
mState=7 RESUMED) fragment to identify the on-screen tab/screen, then walk
its children for an optional sub-screen.

Compose-only or fragment-less apps return None — there's no system-level
record of Compose routes.
"""

from __future__ import annotations

import re

from adbutils import AdbDevice

# Match a fragment block header + the mState/mAdded/mHidden lines that
# follow within the next few lines. Indentation captures nesting depth.
_FRAGMENT_RE = re.compile(
    r"^(?P<indent>\s+)(?P<name>[A-Z][A-Za-z0-9_]*Fragment)\{[0-9a-f]+\}.*?\n"
    r"(?:.*?\n){0,4}?\s+mState=(?P<state>\d+).*?\n"
    r"\s+mAdded=(?P<added>true|false)\s+mRemoving=\S+\s+mFromLayout=\S+\s+mInLayout=\S+\s*\n"
    r"\s+mHidden=(?P<hidden>true|false)",
    re.MULTILINE,
)

# AndroidX FragmentManager state constants. 7 = RESUMED in modern versions
# (uses Lifecycle.State ordinal). Older support-fragment used 5; we accept
# both since the user-visible answer is the same.
_RESUMED_STATES = {"5", "7"}


def parse_active_fragment(output: str) -> str | None:
    """Pick the top-level visible+resumed fragment, plus the deepest visible
    descendant under it. Returns ``"Parent / Child"`` or ``"Parent"`` or None.
    """
    matches = []
    for m in _FRAGMENT_RE.finditer(output):
        matches.append({
            "depth": len(m.group("indent")),
            "name": m.group("name"),
            "state": m.group("state"),
            "added": m.group("added") == "true",
            "hidden": m.group("hidden") == "true",
        })

    if not matches:
        return None

    # Synthetic fragments injected by androidx.lifecycle for lifecycle-event
    # plumbing — never user-visible, always skip.
    matches = [m for m in matches if not m["name"].startswith("ReportFragment")]

    visible = [m for m in matches if m["added"] and not m["hidden"] and m["state"] in _RESUMED_STATES]
    if not visible:
        return None

    top_depth = min(m["depth"] for m in visible)
    top = next(m for m in visible if m["depth"] == top_depth)

    # Pick the deepest descendant of `top` (consecutive matches following
    # `top` whose depth > top_depth, until depth drops back to top_depth).
    children = []
    seen_top = False
    for m in matches:
        if m is top:
            seen_top = True
            continue
        if not seen_top:
            continue
        if m["depth"] <= top_depth:
            break
        if m["added"] and not m["hidden"] and m["state"] in _RESUMED_STATES:
            children.append(m)

    if not children:
        return top["name"]
    deepest = max(children, key=lambda m: m["depth"])
    if deepest["name"] == top["name"]:
        return top["name"]
    return f"{top['name']} / {deepest['name']}"


def current_fragment(device: AdbDevice, activity_component: str | None) -> str | None:
    """Return the active fragment label under the resumed activity, or None
    if no resumed activity is known or the app uses no fragments."""
    if not activity_component:
        return None
    out = device.shell(f"dumpsys activity {activity_component}")
    return parse_active_fragment(out)
