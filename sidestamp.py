#!/usr/bin/env python
"""Check each side of a road centerline against the polygon that side actually fronts.

A centerline has no single containing polygon. It runs BETWEEN them, and it
carries a separate attribute for each side: a left ZIP and a right ZIP, a left
ESN and a right ESN, a left city and a right city. Point-in-polygon validation
answers a question the geometry cannot answer, so it never finds the road that
runs along a ZIP boundary with both sides stamped 34475 while the west side
fronts 34482.

This steps perpendicular from the segment's midpoint to the left and to the
right of the direction of travel, finds the polygon containing each probe
point, and compares that polygon's value with the value stored for THAT side.
It tiers a known placeholder apart from a genuine disagreement, counts a blank
stored value at a coverage gap as neither a pass nor a failure, and refuses to
report a run clean when no segment in it had two different sides, because such
a run cannot show that the probe reached the correct side at all.

Why not a spatial join. ArcGIS Pro's Spatial Join and QGIS's Join Attributes by
Location both relate a line to the polygons it touches, and Esri's Data Reviewer
ships an attribute-to-containing-polygon check. All three are correct and fast
for points. For a line they return the polygon it intersects, or the largest
overlap, and neither of those is "the polygon on the left". The sided question
needs a probe point on a side, and that is what this builds.

    python sidestamp.py --self-test
    python sidestamp.py --streets streets.geojson --rules rules.json
    python sidestamp.py --streets streets.geojson --rules rules.json --dense
    python sidestamp.py --streets streets.geojson --rules rules.json --out report.json --apply

Exit codes: 0 the run was clean and could have failed, 1 findings at ERROR or a
flipped probe, 2 an input could not be read, 3 the run proved nothing, 64 usage
error.
"""

from __future__ import print_function

import argparse
import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
from collections import defaultdict

# =============================================================================
# CONFIGURATION. Deliberately not flags. Change here, not at the call site.
# =============================================================================

# Distance stepped perpendicular from the centerline to reach a probe point, in
# the units of the data. 10 feet clears the pavement of a two-lane road and
# still lands inside the parcel fabric on both sides.
DEFAULT_OFFSET = 10.0

# Length of the chord used to measure the direction of travel at the sample
# point, in the units of the data. The chord straddles the sample point, so a
# curved or dog-legged centerline gets the normal of the curve AT that point
# instead of the normal of the chord across the whole segment.
#
# The tool this came from stepped one unit either way, which is a two unit
# chord described in its own comment as a one unit chord. Here the number names
# the whole chord and the step is half of it.
DEFAULT_CHORD = 1.0

# Fractions along the segment sampled by --dense. Nine samples at every tenth,
# skipping the endpoints, where a probe lands at an intersection.
DENSE_FRACTIONS = tuple(i / 10.0 for i in range(1, 10))

# Sample OBJECTIDs carried per rollup group. Enough to open a few and see the
# pattern, few enough that the report stays one screen.
DEFAULT_SAMPLE = 5

# Coordinates are treated as geographic when every vertex fits inside the
# lat/lon envelope. A ten unit offset in degrees is 690 miles, so an offset
# this large on geographic data is refused rather than probed.
GEOGRAPHIC_OFFSET_LIMIT = 0.01

# Distance within which a point counts as lying ON a polygon edge rather than
# beside it, in the units of the data. Sub-millimetre at any survey scale.
ON_EDGE_EPS = 1e-9

SEVERITIES = ("ERROR", "WARNING", "INFO")

# Tier -> the rule key that sets its severity. A tier is a class of finding
# that an analyst fixes as one batch, so each gets its own severity and none
# can be switched off on its own.
TIER_KEYS = {
    "mismatch": "severity",
    "placeholder": "placeholder_severity",
    "blank": "blank_severity",
}

RULE_KEYS = frozenset([
    "id", "label", "left_field", "right_field", "polygons", "polygon_field",
    "outside_default", "placeholder", "enabled",
    "severity", "placeholder_severity", "blank_severity",
])

REQUIRED_RULE_KEYS = ("id", "left_field", "right_field", "polygon_field")

SIDE_LABEL = {"L": "left", "R": "right"}


# ----------------------------------------------------------------- pure core

def _clean(value):
    """Normalize a stored or polygon value to a stripped string.

    None, an empty cell and a cell holding only spaces are the same fact: no
    value. A number arrives as a number from JSON and as text from a shapefile,
    so both are compared as text. A float that is a whole number prints its
    trailing .0, and ZIP 34475.0 would never equal ZIP 34475.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        if value == int(value):
            return str(int(value))
        return repr(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).strip()


def polyline_length(coords):
    """Total plane length of a vertex list."""
    total = 0.0
    for i in range(len(coords) - 1):
        total += math.hypot(coords[i + 1][0] - coords[i][0],
                            coords[i + 1][1] - coords[i][1])
    return total


def point_at_distance(coords, target):
    """The point `target` units along the vertex list, measured from the start.

    Clamped at both ends, so a caller may step past either end of a short
    segment without checking first.
    """
    if not coords:
        raise ValueError("a segment with no vertices has no points along it")
    if target <= 0.0:
        return coords[0]
    travelled = 0.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg <= 0.0:
            continue
        if travelled + seg >= target:
            t = (target - travelled) / seg
            return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        travelled += seg
    return coords[-1]


def side_points(coords, offset, fraction=0.5, chord=DEFAULT_CHORD):
    """Probe points to the left and right of travel at `fraction` along a segment.

    Returns (left_xy, right_xy), or None when no direction can be measured.

    Left of travel is the +90 degree (counter-clockwise) normal (-dy, dx). That
    is the handedness NG911 gives LEFT and RIGHT: standing at the from node and
    looking toward the to node, left is the LEFT side. Get this backwards and
    every finding is real, every finding is on the wrong side, and the report
    looks plausible. The sign check exists to catch exactly that.
    """
    total = polyline_length(coords)
    if total <= 0.0:
        # Zero length and single vertex geometry has no direction, so no side
        # can be determined for it. The caller counts these rather than
        # guessing a bearing.
        return None
    at = total * fraction
    half = chord / 2.0
    p1 = point_at_distance(coords, max(0.0, at - half))
    p2 = point_at_distance(coords, min(total, at + half))
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    n = math.hypot(dx, dy)
    if n <= 0.0:
        # The chord collapsed onto one point. A segment that doubles back on
        # itself, an out-and-back spur digitized there and back, folds exactly
        # at its own midpoint, and the two ends of the chord land on the same
        # coordinate. There is no direction of travel there and so no side.
        return None
    dx /= n
    dy /= n
    mid = point_at_distance(coords, at)
    return ((mid[0] - dy * offset, mid[1] + dx * offset),
            (mid[0] + dy * offset, mid[1] - dx * offset))


def point_on_ring(px, py, ring):
    """True when the point lies on an edge of the ring, within ON_EDGE_EPS."""
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg <= 0.0:
            if math.hypot(px - x1, py - y1) <= ON_EDGE_EPS:
                return True
            continue
        if abs(cross) / seg > ON_EDGE_EPS:
            continue
        if (min(x1, x2) - ON_EDGE_EPS <= px <= max(x1, x2) + ON_EDGE_EPS and
                min(y1, y2) - ON_EDGE_EPS <= py <= max(y1, y2) + ON_EDGE_EPS):
            return True
    return False


def point_in_ring(px, py, ring):
    """Crossing-number test. Points exactly on an edge are not decided here.

    The half-open comparison (y1 > py) != (y2 > py) counts each crossing once,
    so a ray that passes through a shared vertex, or that runs along a
    horizontal edge, still gives the right answer.
    """
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xint:
                inside = not inside
    return inside


def point_in_polygon(px, py, rings):
    """True when the point is inside the outer ring and outside every hole.

    A point on any edge, the outer one or a hole's, counts as inside. A probe
    point that lands on a shared boundary belongs to a polygon rather than to
    nothing, and reporting it as inside no polygon would invent a coverage gap.
    """
    if not rings:
        return False
    if point_on_ring(px, py, rings[0]):
        return True
    if not point_in_ring(px, py, rings[0]):
        return False
    for hole in rings[1:]:
        if point_on_ring(px, py, hole):
            return True
        if point_in_ring(px, py, hole):
            return False
    return True


def containing_value(polys, point):
    """Value of the first polygon containing the point.

    Returns None when the point is inside no polygon, and "" when it is inside
    a polygon whose key field is null or blank. Those are different facts: one
    says the layer does not cover this ground, the other says it does and the
    attribute is missing. Collapsing them turns a data gap into a coverage gap.
    """
    px, py = point
    for value, rings_list in polys:
        for rings in rings_list:
            if point_in_polygon(px, py, rings):
                # Normalized here and nowhere else. A polygon whose key field
                # is null must come back as "", not as None, or "inside a
                # polygon with no ZIP" reads as "inside no polygon" and an
                # attribute gap is reported as a coverage gap.
                return _clean(value).upper()
    return None


def side_values(polys, coords, offset, side, dense, chord=DEFAULT_CHORD):
    """Every distinct polygon value the given side fronts.

    Returns (values, gaps, sampled). `values` is the set of non-None values
    seen, `gaps` counts samples that fell inside no polygon, and `sampled` is
    how many probe points were actually built. In single-sample mode that is
    one point at the midpoint; with `dense` it is the nine-tenths sweep, which
    is what makes a side spanning two polygons visible at all.
    """
    fractions = DENSE_FRACTIONS if dense else (0.5,)
    index = 0 if side == "L" else 1
    values = set()
    gaps = 0
    sampled = 0
    for fraction in fractions:
        pair = side_points(coords, offset, fraction, chord)
        if pair is None:
            continue
        sampled += 1
        found = containing_value(polys, pair[index])
        if found is None:
            gaps += 1
        else:
            values.add(found)
    return values, gaps, sampled


def describe_side(field, side, stored, values, gaps, layer_label,
                  outside_default, placeholder):
    """Compare one side of one segment. Returns (description, tier).

    Tiers:
      "placeholder"  the stored value is a known non-code sentinel
      "blank"        the field is empty and the polygon can name it
      "mismatch"     the stored value names a place this side does not front
      "gap"          the field is empty and the layer covers nothing here
      None           the stored value agrees with the polygon

    Only "gap" and None carry no description. A gap is the conservative miss: a
    blank where the layer has no polygon cannot be contradicted, and it is not
    an agreement either. Counting it as a pass is how a coverage hole reads as
    a clean run.
    """
    if placeholder is not None and stored == placeholder:
        return ("%s '%s' is a placeholder, not a real %s"
                % (field, stored, layer_label), "placeholder")

    if not values:
        # Inside no polygon on every sample. The layer's outside_default says
        # whether that has a meaning. A municipal layer covering only the
        # incorporated places means UNINCORPORATED out here; a ZIP layer with a
        # ragged edge means nothing at all.
        if outside_default is None:
            if stored == "":
                return None, "gap"
            return ("%s side is inside no %s; stored %s '%s'"
                    % (SIDE_LABEL[side], layer_label, field, stored), "mismatch")
        if stored == "":
            return ("%s is blank but the %s side is outside every %s, which is '%s'"
                    % (field, SIDE_LABEL[side], layer_label, outside_default),
                    "blank")
        if stored != outside_default:
            return ("%s '%s' != %s on the %s side, which is outside every "
                    "polygon and so '%s'"
                    % (field, stored, layer_label, SIDE_LABEL[side],
                       outside_default), "mismatch")
        return None, None

    if stored == "":
        return ("%s is blank but the %s side is inside %s '%s'"
                % (field, SIDE_LABEL[side], layer_label,
                   "' or '".join(sorted(values))), "blank")

    # Asymmetric verdict. A non-empty stored value must name a polygon
    # genuinely on this side. With one sample that is an equality test; with
    # the dense sweep the side may front several polygons and any of them is
    # acceptable, because the stamp describes the side and not the midpoint.
    if stored in values:
        return None, None
    if gaps and len(values) == 1:
        # Part of this side is uncovered and the rest names one polygon. Still
        # a mismatch, but say the side is partly uncovered so nobody hunts for
        # a polygon that does not exist.
        return ("%s '%s' != %s on the %s side '%s', and %d sample(s) of that "
                "side are inside no polygon"
                % (field, stored, layer_label, SIDE_LABEL[side],
                   sorted(values)[0], gaps), "mismatch")
    return ("%s '%s' != %s on the %s side '%s'"
            % (field, stored, layer_label, SIDE_LABEL[side],
               "' or '".join(sorted(values))), "mismatch")


def _effective(values, outside_default):
    """The containing value a rollup groups by, or None when there is none.

    Called only for a side that fronts at most one polygon. A side fronting
    several is reported ambiguous instead of grouped, because a rollup row
    naming two polygons is not a unit of work.
    """
    if not values:
        return outside_default
    return sorted(values)[0]


def evaluate(streets, rules, polys_by_rule, offset=DEFAULT_OFFSET, dense=False,
             chord=DEFAULT_CHORD):
    """Run every enabled rule over every segment. Returns a report dict.

    Pure: streets and polygons are already parsed, nothing is read or written.
    """
    findings = []
    ambiguous = []
    counts = {"segments": 0, "sides": 0, "agree": 0, "gap": 0, "skipped": 0,
              "unsampled": 0, "ambiguous": 0, "ambiguous_defensible": 0}
    sign = {"discriminating": 0, "right_match": 0, "left_as_right": 0}
    active = [r for r in rules if r.get("enabled", True)]

    for street in streets:
        counts["segments"] += 1
        coords = street["coords"]
        if polyline_length(coords) <= 0.0:
            # No length, so no direction and no side. Counted, never guessed.
            counts["skipped"] += 1
            continue
        for rule in active:
            polys = polys_by_rule[rule["id"]]
            label = rule.get("label") or rule["polygon_field"]
            probes = {}
            for side, field in (("L", rule["left_field"]),
                                ("R", rule["right_field"])):
                values, gaps, sampled = side_values(polys, coords, offset, side,
                                                    dense, chord)
                probes[side] = (values, gaps)
                if sampled == 0:
                    # The segment has length but no probe point could be built
                    # on it. Counted apart from the sides that were compared.
                    counts["unsampled"] += 1
                    continue
                counts["sides"] += 1
                stored = _clean(street["props"].get(field)).upper()
                desc, tier = describe_side(
                    field, side, stored, values, gaps, label,
                    rule.get("outside_default"), rule.get("placeholder"))
                spans = len(values) > 1
                if spans:
                    # The side fronts more than one polygon along its length,
                    # so no single value describes it and the segment should be
                    # split at the boundary. This is a property of the input,
                    # not a verdict, so it is recorded whether or not the stamp
                    # happens to name one of them.
                    defensible = stored in values
                    counts["ambiguous"] += 1
                    if defensible:
                        counts["ambiguous_defensible"] += 1
                    ambiguous.append({
                        "rule": rule["id"], "oid": street["oid"], "side": side,
                        "field": field, "label": label, "stored": stored,
                        "values": sorted(values), "defensible": defensible,
                        "desc": "%s side spans %d %s polygons (%s) and is "
                                "stamped '%s', which is %s of them; split the "
                                "centerline at the boundary"
                                % (SIDE_LABEL[side], len(values), label,
                                   ", ".join(sorted(values)), stored,
                                   "one" if defensible else "neither"),
                    })
                if tier == "gap":
                    counts["gap"] += 1
                    continue
                if desc is None:
                    counts["agree"] += 1
                    continue
                if spans and tier in ("mismatch", "blank"):
                    # No single right answer to name, so no verdict. The
                    # ambiguous record above already carries the remedy.
                    continue
                findings.append({
                    "rule": rule["id"], "oid": street["oid"], "side": side,
                    "field": field, "label": label, "stored": stored,
                    "containing": _effective(values, rule.get("outside_default")),
                    "tier": tier, "desc": desc,
                    "severity": rule.get(TIER_KEYS[tier]) or rule["severity"],
                })
            _tally_sign(sign, street, rule, probes)

    return {"findings": findings, "ambiguous": ambiguous, "counts": counts,
            "sign": sign, "rollup": rollup(findings)}


def _tally_sign(sign, street, rule, probes):
    """Confirm the probe reached the correct side, on segments that can show it.

    Only a segment whose two sides front DIFFERENT polygons can prove the
    handedness of the offset. On every other segment a left/right swap produces
    exactly the same answer, so a run made entirely of such segments proves
    nothing about the probe, however clean it looks.
    """
    left = probes.get("L", (set(), 0))[0]
    right = probes.get("R", (set(), 0))[0]
    if len(left) != 1 or len(right) != 1:
        return
    lval = sorted(left)[0]
    rval = sorted(right)[0]
    if lval == rval:
        return
    stored_r = _clean(street["props"].get(rule["right_field"])).upper()
    if stored_r == "":
        return
    sign["discriminating"] += 1
    if stored_r == rval:
        sign["right_match"] += 1
    elif stored_r == lval:
        sign["left_as_right"] += 1


def rollup(findings, sample=DEFAULT_SAMPLE):
    """Collapse findings into the groups an analyst fixes at once.

    One row saying 96 sides fronting ZIP 34482 are stamped 34475 is the unit of
    work. Sorted by count descending, so systematic drift sits above the
    one-off boundary segments.
    """
    groups = defaultdict(lambda: {"count": 0, "ids": []})
    for f in findings:
        key = (f["rule"], f["field"], f["tier"],
               f["containing"] if f["containing"] is not None else "(outside all)",
               f["stored"])
        g = groups[key]
        g["count"] += 1
        if len(g["ids"]) < sample:
            g["ids"].append(f["oid"])
    out = [{"rule": k[0], "field": k[1], "tier": k[2], "containing": k[3],
            "stored": k[4], "count": v["count"], "sample_ids": v["ids"]}
           for k, v in groups.items()]
    # Secondary keys so the order is total. Ordering only by count leaves ties
    # in dict order, and a report whose rows move between runs cannot be diffed.
    out.sort(key=lambda r: (-r["count"], r["rule"], r["field"],
                            str(r["containing"]), r["stored"]))
    return out


def sign_verdict(sign):
    """PASS, FLIPPED or INSUFFICIENT for the probe's handedness.

    The evidence is the segments whose stored right value matched one side or
    the other, not every segment that could have carried evidence. A segment
    whose right value names neither of the polygons it runs between is wrong
    twice over and says nothing about which side the probe reached.
    """
    if sign["left_as_right"] > sign["right_match"]:
        return "FLIPPED"
    if sign["left_as_right"] == sign["right_match"]:
        # Equal counts are not evidence. Zero and zero is a run that carried no
        # evidence at all, and one and one is a run whose evidence contradicts
        # itself: as many segments say the probe reached the wrong side as say
        # it reached the right one. Neither has shown that the findings are on
        # the side they name, and calling either PASS would let a run through
        # on the one thing this tool exists to rule out. Both are INSUFFICIENT,
        # which is why there is no separate test for the empty case.
        return "INSUFFICIENT"
    return "PASS"


def gate(report):
    """Exit code for a finished run.

    3 outranks 0 and yields to 1. A run with no discriminating segment cannot
    be called clean, but a run that already found errors has something to say
    and says it.
    """
    if any(f["severity"] == "ERROR" for f in report["findings"]):
        return 1
    if sign_verdict(report["sign"]) == "FLIPPED":
        return 1
    if sign_verdict(report["sign"]) == "INSUFFICIENT":
        return 3
    return 0


def agreement_rate(counts):
    """Fraction of decided sides that agreed, or None when none were decided.

    Gaps are excluded from both halves. A conservative miss is neither a pass
    nor a failure, and counting it as either moves the rate without moving the
    data.
    """
    decided = counts["agree"] + counts["_findings"]
    if decided == 0:
        return None
    return float(counts["agree"]) / decided


def describe(report, dense):
    """The printed report, as a list of lines."""
    counts = dict(report["counts"])
    counts["_findings"] = len(report["findings"])
    out = []
    out.append("segments read: %d" % counts["segments"])
    if counts["skipped"]:
        out.append("  %d skipped: zero length or a single vertex, so no side"
                   % counts["skipped"])
    out.append("sides compared: %d" % counts["sides"])
    rate = agreement_rate(counts)
    if rate is None:
        out.append("  agreement rate: none, no side was decided")
    else:
        out.append("  agreement: %d of %d decided sides, %.2f%%"
                   % (counts["agree"], counts["agree"] + counts["_findings"],
                      rate * 100.0))
    if counts["unsampled"]:
        out.append("  %d side(s) of a segment that folds back on itself, which "
                   "has no direction at its midpoint" % counts["unsampled"])
    out.append("  conservative misses: %d blank sides where the layer covers "
               "nothing" % counts["gap"])
    out.append("  ambiguous: %d side(s) spanning more than one polygon%s"
               % (counts["ambiguous"],
                  "" if dense else " (single sample, run --dense to find these)"))
    if counts["ambiguous"]:
        out.append("    %d of them are stamped with one of the polygons they "
                   "span, so the stamp is defensible and the split is still "
                   "owed" % counts["ambiguous_defensible"])

    by_sev = defaultdict(int)
    for f in report["findings"]:
        by_sev[f["severity"]] += 1
    out.append("")
    out.append("findings: %d" % len(report["findings"]))
    for sev in SEVERITIES:
        if by_sev[sev]:
            out.append("  %-8s %d" % (sev, by_sev[sev]))

    if report["rollup"]:
        out.append("")
        out.append("groups, largest first:")
        for g in report["rollup"][:20]:
            out.append("  %4d  %s %s stamped '%s' where the side is '%s' [%s]"
                       % (g["count"], g["rule"], g["field"], g["stored"],
                          g["containing"], g["tier"]))
            out.append("        sample ids: %s"
                       % ", ".join(str(o) for o in g["sample_ids"]))

    if report["ambiguous"]:
        out.append("")
        out.append("ambiguous sides, no verdict given:")
        for a in report["ambiguous"][:10]:
            out.append("  OBJECTID %s %s: %s" % (a["oid"], a["rule"], a["desc"]))

    sign = report["sign"]
    verdict = sign_verdict(sign)
    out.append("")
    out.append("probe sign check: %s" % verdict)
    out.append("  %d segment(s) front different polygons on their two sides and "
               "so could disprove the offset direction" % sign["discriminating"])
    if verdict == "INSUFFICIENT" and sign["discriminating"]:
        out.append("  None of them carries a right value naming either "
                   "polygon, so none of them settles the question.")
    if verdict == "INSUFFICIENT":
        out.append("  Nothing in this run could have failed on side direction. "
                   "A left/right swap would give the same answer.")
    else:
        out.append("  %d of those match the right side, %d match the left"
                   % (sign["right_match"], sign["left_as_right"]))
    if verdict == "FLIPPED":
        out.append("  The stored right values match the LEFT probe more often "
                   "than the right. Every finding here is on the wrong side.")
    return out


# ------------------------------------------------------------------ rules i/o

def validate_rule(raw):
    """Check one rule and return it with defaults filled in.

    Raises ValueError, naming the rule, rather than ignoring anything. A
    misspelled key that is silently dropped switches a check off and reports
    clean, which is the one outcome this tool exists to prevent.
    """
    if not isinstance(raw, dict):
        raise ValueError("each rule must be an object, got %r" % (raw,))
    rid = raw.get("id")
    if not rid or not isinstance(rid, str):
        raise ValueError("every rule needs a non-empty string 'id'")
    unknown = sorted(set(raw) - RULE_KEYS)
    if unknown:
        raise ValueError("rule %s: unknown key(s) %s. Valid keys: %s"
                         % (rid, ", ".join(unknown), ", ".join(sorted(RULE_KEYS))))
    for key in REQUIRED_RULE_KEYS:
        if not raw.get(key):
            raise ValueError("rule %s: '%s' is required" % (rid, key))
    rule = dict(raw)
    rule.setdefault("severity", "ERROR")
    rule.setdefault("placeholder_severity", "WARNING")
    rule.setdefault("blank_severity", None)
    for tier, key in sorted(TIER_KEYS.items()):
        value = rule.get(key)
        if value is None:
            continue
        text = str(value).strip().upper()
        if text in ("OFF", "FALSE", "NONE", "NO"):
            # Naming the rule that emits the records matters. Telling an
            # analyst to switch off a tier sends them looking for a knob that
            # does not exist, and leaves them believing the check is quieter
            # than it is.
            raise ValueError(
                "rule %s: '%s' cannot be switched off, because %s emits those "
                "records. Set \"enabled\": false to silence the whole rule, or "
                "set '%s' to INFO to demote the %s tier instead."
                % (rid, key, rid, key, tier))
        if text not in SEVERITIES:
            raise ValueError("rule %s: '%s' expected one of %s, got %r"
                             % (rid, key, "/".join(SEVERITIES), value))
        rule[key] = text
    if rule.get("placeholder") is not None:
        rule["placeholder"] = _clean(rule["placeholder"]).upper()
    if rule.get("outside_default") is not None:
        rule["outside_default"] = _clean(rule["outside_default"]).upper()
    if rule["left_field"] == rule["right_field"]:
        raise ValueError("rule %s: left_field and right_field are both '%s', so "
                         "the two sides cannot disagree" % (rid, rule["left_field"]))
    return rule


def load_rules(obj):
    """Validate a parsed rules document and return the list of rules."""
    if isinstance(obj, list):
        raw_rules = obj
    elif isinstance(obj, dict):
        unknown = sorted(set(obj) - set(["checks"]))
        if unknown:
            raise ValueError("rules file: unknown top-level key(s) %s. Only "
                             "'checks' is valid." % ", ".join(unknown))
        raw_rules = obj.get("checks")
        if raw_rules is None:
            raise ValueError("rules file: no 'checks' list")
    else:
        raise ValueError("rules file: expected an object or a list, got %s"
                         % type(obj).__name__)
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("rules file: 'checks' must be a non-empty list")
    rules = [validate_rule(r) for r in raw_rules]
    seen = {}
    for rule in rules:
        if rule["id"] in seen:
            raise ValueError("rules file: rule id %s appears twice, so one of "
                             "them would be reported under the other's name"
                             % rule["id"])
        seen[rule["id"]] = True
    return rules


# ---------------------------------------------------------------- geojson i/o

def _features(obj):
    """Feature list of a FeatureCollection, a bare Feature or a list."""
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        raise ValueError("expected GeoJSON, got %s" % type(obj).__name__)
    kind = obj.get("type")
    if kind == "FeatureCollection":
        feats = obj.get("features")
        if not isinstance(feats, list):
            raise ValueError("FeatureCollection has no features list")
        return feats
    if kind == "Feature":
        return [obj]
    raise ValueError("expected a FeatureCollection or a Feature, got %r" % (kind,))


def streets_from_geojson(obj, id_field=None):
    """Segments as {"oid", "coords", "props"}.

    A MultiLineString is refused rather than flattened. Its parts have no
    single midpoint, and probing the longest part would stamp a verdict on the
    whole feature from one arbitrary piece of it.
    """
    out = []
    for i, feat in enumerate(_features(obj)):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        props = feat.get("properties") or {}
        if gtype == "MultiLineString":
            raise ValueError(
                "feature %d is a MultiLineString. Explode it to single part "
                "lines first: its parts have no common midpoint and no common "
                "direction of travel." % i)
        if gtype != "LineString":
            raise ValueError("feature %d has geometry type %r, expected "
                             "LineString" % (i, gtype))
        coords = [(float(c[0]), float(c[1])) for c in geom.get("coordinates") or []]
        if id_field:
            oid = props.get(id_field)
            if oid is None:
                raise ValueError("feature %d has no '%s' property" % (i, id_field))
        else:
            oid = feat.get("id", props.get("OBJECTID", i + 1))
        out.append({"oid": oid, "coords": coords, "props": props})
    return out


def polygons_from_geojson(obj, field):
    """[(value, [rings, ...]), ...] for one polygon layer.

    `value` is "" when the key field is null or missing on the feature, which
    containing_value passes through as "inside a polygon with no value".
    """
    out = []
    for i, feat in enumerate(_features(obj)):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        props = feat.get("properties") or {}
        raw = geom.get("coordinates") or []
        if gtype == "Polygon":
            parts = [raw]
        elif gtype == "MultiPolygon":
            parts = raw
        else:
            raise ValueError("polygon feature %d has geometry type %r, "
                             "expected Polygon or MultiPolygon" % (i, gtype))
        rings_list = []
        for part in parts:
            rings = [[(float(c[0]), float(c[1])) for c in ring] for ring in part]
            if rings and len(rings[0]) >= 3:
                rings_list.append(rings)
        if not rings_list:
            continue
        out.append((props.get(field), rings_list))
    if not out:
        raise ValueError("no usable polygon in the layer for field %r. An "
                         "empty layer would report every side as inside no "
                         "polygon." % field)
    return out


def looks_geographic(streets):
    """True when every vertex fits inside the lat/lon envelope.

    A false positive is possible: a projected system in metres with an origin
    near zero could fit. It costs a refusal that names the remedy, against a
    run that probes 690 miles sideways and reports the neighbouring county.
    """
    seen = False
    for street in streets:
        for x, y in street["coords"]:
            seen = True
            if abs(x) > 180.0 or abs(y) > 90.0:
                return False
    return seen


def read_json(path):
    """Parsed JSON from a file, with the BOM Esri and Excel both write removed."""
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


# ------------------------------------------------------------------ self-test

def self_test():
    """Assertions over the decision core. No file outside a temp dir, no
    network, no credentials, no arcpy."""
    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label):
        try:
            fn()
        except ValueError:
            check(True, label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    print("sidestamp self-test: no file, no network, no arcpy")
    print("-" * 70)

    # ---- geometry: length and position along a polyline
    line = [(0.0, 0.0), (100.0, 0.0)]
    check(polyline_length(line) == 100.0, "a straight 100 unit segment is 100 long")
    check(polyline_length([(0.0, 0.0), (3.0, 4.0)]) == 5.0,
          "a 3-4-5 segment is 5 long")
    check(polyline_length([(0.0, 0.0)]) == 0.0, "a single vertex has no length")
    check(point_at_distance(line, 50.0) == (50.0, 0.0),
          "the halfway point of a straight segment is its midpoint")
    check(point_at_distance(line, -5.0) == (0.0, 0.0),
          "stepping before the start clamps to the first vertex")
    check(point_at_distance(line, 500.0) == (100.0, 0.0),
          "stepping past the end clamps to the last vertex")
    bent = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    check(point_at_distance(bent, 15.0) == (10.0, 5.0),
          "distance is measured along the bend, not across it")
    check(point_at_distance([(0.0, 0.0), (0.0, 0.0), (6.0, 0.0)], 3.0) == (3.0, 0.0),
          "a duplicated vertex does not consume any of the distance")
    raises(lambda: point_at_distance([], 1.0),
           "a segment with no vertices is refused, not read as the origin")

    # ---- geometry: the side probe and its handedness
    pair = side_points(line, 10.0)
    check(pair is not None and pair[0] == (50.0, 10.0),
          "left of travel along +X is +Y  <-- pinned defect")
    check(pair is not None and pair[1] == (50.0, -10.0),
          "right of travel along +X is -Y  <-- pinned defect")
    back = side_points([(100.0, 0.0), (0.0, 0.0)], 10.0)
    check(back is not None and back[0] == (50.0, -10.0),
          "the same road digitized backwards puts left on the other side")
    check(side_points([(0.0, 0.0), (0.0, 0.0)], 10.0) is None,
          "a zero length segment has no direction and no side")
    check(side_points([(5.0, 5.0)], 10.0) is None,
          "a single vertex has no direction and no side")
    check(side_points([], 10.0) is None,
          "and a feature with no coordinates at all is counted, not raised on"
          "  <-- pinned defect")
    check(side_points([(0.0, 0.0), (0.0, 0.0), (0.0, 50.0)], 10.0) is not None,
          "a segment with duplicate vertices at the midpoint still gets a side")
    spur = [(0.0, 0.0), (10.0, 0.0), (0.0, 0.0)]
    check(side_points(spur, 10.0) is None,
          "an out-and-back spur folds at its own midpoint and has no side there"
          "  <-- pinned defect")
    check(side_points(spur, 10.0, 0.25) is not None,
          "but a quarter of the way along that spur does have one")

    # The tangent is measured over a short chord straddling the sample point,
    # so an elbow gets the direction of the curve AT the midpoint. Measuring
    # end to end would give the 45 degree chord across the corner instead.
    dogleg = [(0.0, 0.0), (0.0, 120.0), (100.0, 120.0)]
    chorded = side_points(dogleg, 10.0, 0.5, 1.0)
    check(chorded is not None and abs(chorded[0][0] - (-10.0)) < 1e-9,
          "a dog-leg gets the normal of the leg the midpoint is on")
    wide = side_points(dogleg, 10.0, 0.5, 440.0)
    check(wide is not None and abs(wide[0][0] - (-10.0)) > 2.0,
          "a chord spanning the whole segment gives the diagonal across the "
          "dog-leg instead  <-- pinned defect")

    # The chord constant names the WHOLE chord and the step is half of it. The
    # tool this came from stepped one unit either way and called that a one
    # unit chord, so it measured the tangent over twice the span it documented.
    # This elbow is short enough that the difference moves the probe point: the
    # midpoint sits half a unit past the bend, so a half-unit step stays on the
    # upper leg and a full-unit step reaches back around the corner. The values
    # are absolute, so a change to DEFAULT_CHORD fails here rather than reading
    # the constant back to itself.
    elbow = [(0.0, 0.0), (1.0, 0.0), (1.0, 2.0)]
    check(side_points(elbow, 10.0) == ((-9.0, 0.5), (11.0, 0.5)),
          "the default chord steps half a unit either way, not a whole one"
          "  <-- pinned defect")
    check(side_points(elbow, 10.0, 0.5, 2.0) != side_points(elbow, 10.0),
          "and a two unit chord on the same elbow lands somewhere else")

    # ---- geometry: point in polygon
    square = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
    check(point_in_polygon(5.0, 5.0, square), "a point in the middle is inside")
    check(not point_in_polygon(15.0, 5.0, square), "a point outside is outside")
    check(point_in_polygon(0.0, 0.0, square),
          "a point on a vertex is inside, not a coverage gap  <-- pinned defect")
    check(point_in_polygon(5.0, 0.0, square),
          "a point on a horizontal edge is inside  <-- pinned defect")
    check(point_in_polygon(0.0, 5.0, square), "a point on a vertical edge is inside")
    check(point_in_polygon(10.0, 10.0, square), "the far vertex is inside too")
    check(not point_in_polygon(5.0, 5.0, []), "a polygon with no ring contains nothing")
    donut = [[(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)],
             [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0), (10.0, 10.0)]]
    check(point_in_polygon(5.0, 15.0, donut), "a point in the ring is inside")
    check(not point_in_polygon(15.0, 15.0, donut), "a point in the hole is outside")
    check(point_in_polygon(15.0, 10.0, donut),
          "a point on the hole's edge is inside  <-- pinned defect")
    spike = [[(0.0, 0.0), (10.0, 10.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0),
              (0.0, 0.0)]]
    check(not point_in_polygon(10.0, 5.0, spike),
          "a ray through a shared vertex still gives the right answer")
    degenerate = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)]
    check(point_on_ring(1.0, 1.0, degenerate),
          "a ring collapsed to one point still contains that point")
    check(not point_on_ring(2.0, 2.0, degenerate),
          "and contains nothing else")
    check(not point_on_ring(20.0, 0.0, square[0]),
          "a point on an edge's line but past its end is not on the edge"
          "  <-- pinned defect")
    check(not point_on_ring(5.0, 0.001, square[0]),
          "a thousandth of a unit off an edge is beside it, not on it. The "
          "tolerance is sub-millimetre, so only a point the producer meant to "
          "put on the line reads as on it  <-- pinned defect")
    check(point_on_ring(5.0, 0.0, square[0]),
          "and the edge itself still reads as on it")
    # "Within ON_EDGE_EPS" includes the tolerance itself. A producer that
    # rounds a shared boundary to the last representable digit puts its points
    # at the tolerance, not inside it, and an exclusive test would drop exactly
    # those into a coverage gap.
    tall = [(0.0, 0.0), (0.0, 10.0), (4.0, 10.0), (4.0, 0.0)]
    check(point_on_ring(ON_EDGE_EPS, 5.0, tall),
          "a point exactly one tolerance off an edge is still on it"
          "  <-- pinned defect")
    check(not point_on_ring(ON_EDGE_EPS * 10.0, 5.0, tall),
          "and ten tolerances off is not")

    # The crossing-number rule counts a crossing when the ray leaves the point
    # to the right, so a point sitting exactly on a vertical edge is inside
    # when that edge is on its left and outside when it is on its right. The
    # two answers are not symmetric, and point_in_polygon is what makes a
    # shared boundary belong to a polygon rather than to neither. Pinned at the
    # ring level because point_in_polygon answers on-edge before it gets here.
    check(point_in_ring(0.0, 5.0, square[0]),
          "on the left edge the crossing number says inside  <-- pinned defect")
    check(not point_in_ring(10.0, 5.0, square[0]),
          "and on the right edge it says outside, which is why the on-edge "
          "test runs first  <-- pinned defect")

    # GeoJSON says a ring repeats its first vertex last, and most producers do.
    # Nothing here closes the ring on read, so the closing edge has to come
    # from the index wrapping round. An unclosed ring that lost its last edge
    # would leave a polygon open along that side and read as a coverage gap.
    open_ring = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    check(point_on_ring(0.0, 5.0, open_ring),
          "an unclosed ring still has its closing edge  <-- pinned defect")
    check(point_in_polygon(5.0, 5.0, [open_ring]),
          "and still contains its own middle")

    # ---- _clean, the normalization every comparison depends on
    check(_clean(None) == "", "a null value cleans to empty")
    check(_clean("  34475 ") == "34475", "a padded value is stripped")
    check(_clean(34475) == "34475", "an integer cleans to its digits")
    check(_clean(34475.0) == "34475",
          "a float ZIP loses its trailing .0, or it never equals the text ZIP"
          "  <-- pinned defect")
    check(_clean(1.5) == "1.5", "a genuine fraction keeps its decimal part")
    check(_clean(float("nan")) == "",
          "a NaN is absent, not a value  <-- pinned defect")
    check(_clean(float("inf")) == "", "an infinity is absent, not a value")
    check(_clean(True) == "TRUE", "a boolean cleans to text, not to '1'")

    # ---- containing_value: three outcomes, not two
    layer = [("A", [square]),
             (None, [[[(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0),
                       (20.0, 0.0)]]])]
    check(containing_value(layer, (5.0, 5.0)) == "A",
          "a point inside a keyed polygon returns its value")
    check(containing_value(layer, (25.0, 5.0)) == "",
          "a point inside a null-keyed polygon returns empty, not None"
          "  <-- pinned defect")
    check(containing_value(layer, (99.0, 99.0)) is None,
          "a point inside no polygon returns None")
    # Stored values are upper-cased before comparison, so polygon values must
    # be too. A municipality layer carrying "Riverton" against a street stamped
    # "RIVERTON" would otherwise report every side in the city as a mismatch.
    check(containing_value([("Riverton", [square])], (5.0, 5.0)) == "RIVERTON",
          "a mixed case polygon value is folded up to match the stored side"
          "  <-- pinned defect")
    check(containing_value([("  34475 ", [square])], (5.0, 5.0)) == "34475",
          "and a padded one is stripped by the same normalizer")

    # ---- _effective: what the rollup groups a finding under
    check(_effective(set(), "UNINCORPORATED") == "UNINCORPORATED",
          "a side inside no polygon groups under the layer's outside default")
    check(_effective(set(), None) is None,
          "and under nothing when the layer has no outside default")
    check(_effective(set(["34482", "34475"]), None) == "34475",
          "a side fronting two polygons groups under the lowest, so a "
          "placeholder on an ambiguous side still lands in one stable group")

    # ---- side_values reports how many probe points it managed to build
    one_poly = [("A", [[[(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0),
                         (-50.0, 50.0), (-50.0, -50.0)]]])]
    vals, gaps_n, sampled = side_values(one_poly, [(0.0, -10.0), (0.0, 10.0)],
                                        5.0, "L", False)
    check((vals, gaps_n, sampled) == (set(["A"]), 0, 1),
          "a single sample inside one polygon returns one value and one sample")
    vals, gaps_n, sampled = side_values(one_poly, spur, 5.0, "L", True)
    check(sampled == 8,
          "the dense sweep skips the fraction where the spur folds, and says "
          "it took 8 samples rather than 9  <-- pinned defect")
    vals, gaps_n, sampled = side_values(one_poly, [(200.0, 0.0), (300.0, 0.0)],
                                        5.0, "R", False)
    check((vals, gaps_n, sampled) == (set(), 1, 1),
          "a sample outside every polygon is a gap, not a value")

    # ---- the comparison table, from a fixture rule set
    zips = load_rules({"checks": [{
        "id": "SIDE-ZIP", "label": "ZIP boundary",
        "left_field": "ZIP_L", "right_field": "ZIP_R",
        "polygons": "zips.geojson", "polygon_field": "ZIP",
        "placeholder": "99999"}]})[0]
    muni = load_rules({"checks": [{
        "id": "SIDE-MUNI", "label": "municipality",
        "left_field": "MUNI_L", "right_field": "MUNI_R",
        "polygons": "muni.geojson", "polygon_field": "MUNI",
        "outside_default": "UNINCORPORATED"}]})[0]

    def side(rule, stored, values, gaps=0, s="L"):
        return describe_side(rule["left_field"], s, stored, set(values), gaps,
                             rule["label"], rule.get("outside_default"),
                             rule.get("placeholder"))

    check(side(zips, "34475", ["34475"]) == (None, None),
          "a side stamped with the ZIP it fronts agrees")
    desc, tier = side(zips, "34475", ["34482"])
    check(tier == "mismatch" and "34482" in desc,
          "a side stamped 34475 fronting 34482 is a mismatch  <-- pinned defect")
    check("left" in desc, "the finding names which side is wrong")
    desc, tier = side(zips, "99999", ["34482"])
    check(tier == "placeholder",
          "the 99999 placeholder is its own tier, not a wrong ZIP"
          "  <-- pinned defect")
    check("placeholder" in desc, "the placeholder finding says so in words")
    check(side(zips, "99999", ["99999"])[1] == "placeholder",
          "a placeholder is a placeholder even where a polygon carries it")
    desc, tier = side(zips, "", ["34482"])
    check(tier == "blank", "a blank field the polygon can name is the blank tier")
    check(side(zips, "", [])[1] == "gap",
          "a blank field where the layer covers nothing is a conservative miss"
          "  <-- pinned defect")
    check(side(zips, "", [])[0] is None, "and it carries no finding text")
    desc, tier = side(zips, "34475", [])
    check(tier == "mismatch" and "inside no" in desc,
          "a stamped side fronting no polygon at all is a mismatch")

    # ---- outside_default: the same blank is silent on one layer and fires on
    # the other. This is the whole reason the key exists.
    check(side(muni, "", [])[1] == "blank",
          "a blank on a layer declaring outside_default fires"
          "  <-- pinned defect")
    check(side(zips, "", [])[1] == "gap",
          "the same blank on a layer declaring none is silent  <-- pinned defect")
    check("UNINCORPORATED" in side(muni, "", [])[0],
          "the blank finding names the outside value")
    check(side(muni, "UNINCORPORATED", [])[1] is None,
          "stamped UNINCORPORATED outside every municipality agrees")
    desc, tier = side(muni, "RIVERTON", [])
    check(tier == "mismatch" and "UNINCORPORATED" in desc,
          "stamped with a city while outside every city is a mismatch"
          "  <-- pinned defect")
    check(side(muni, "RIVERTON", ["RIVERTON"])[1] is None,
          "stamped with the city it is inside agrees")

    # ---- dense acceptance: a side fronting two ZIPs accepts either
    check(side(zips, "34475", ["34475", "34482"]) == (None, None),
          "a nine sample sweep fronting two ZIPs accepts the first")
    check(side(zips, "34482", ["34475", "34482"]) == (None, None),
          "and accepts the second")
    desc, tier = side(zips, "34479", ["34475", "34482"])
    check(tier == "mismatch" and "34475' or '34482" in desc,
          "a value fronting neither fails and names both  <-- pinned defect")
    desc, tier = side(zips, "34479", ["34482"], gaps=4)
    check(tier == "mismatch" and "4 sample(s)" in desc,
          "a partly uncovered side says so rather than naming a missing polygon")

    # ---- rule validation refuses rather than ignoring
    raises(lambda: load_rules({"checks": [{"id": "A", "left_field": "L",
                                           "right_field": "R",
                                           "polygon_field": "Z",
                                           "placholder": "99"}]}),
           "a misspelled rule key is refused, not dropped  <-- pinned defect")
    raises(lambda: load_rules({"checks": [{"id": "A", "left_field": "L",
                                           "right_field": "R"}]}),
           "a rule with no polygon_field is refused")
    raises(lambda: load_rules({"checks": [{"left_field": "L", "right_field": "R",
                                           "polygon_field": "Z"}]}),
           "a rule with no id is refused")
    raises(lambda: load_rules({"checks": [{"id": "A", "left_field": "L",
                                           "right_field": "L",
                                           "polygon_field": "Z"}]}),
           "a rule whose two sides read one field is refused  <-- pinned defect")
    raises(lambda: load_rules({"checks": [
        {"id": "A", "left_field": "L", "right_field": "R", "polygon_field": "Z"},
        {"id": "A", "left_field": "M", "right_field": "N", "polygon_field": "Y"}]}),
           "two rules sharing an id are refused  <-- pinned defect")
    raises(lambda: load_rules({"chekcs": []}),
           "a misspelled top-level key is refused")
    raises(lambda: load_rules({"checks": []}), "an empty checks list is refused")
    raises(lambda: load_rules({}), "a rules file with no checks list is refused")
    raises(lambda: load_rules("checks"), "a rules file that is not an object is refused")
    raises(lambda: load_rules([["id", "A"]]), "a rule that is not an object is refused")
    raises(lambda: load_rules({"checks": [{"id": "A", "left_field": "L",
                                           "right_field": "R",
                                           "polygon_field": "Z",
                                           "severity": "LOUD"}]}),
           "an unknown severity is refused")

    # The tier severity cannot be switched off on its own. Telling an analyst
    # to write "placeholder_severity: off" would send them to a knob that does
    # not exist, and leave the rule emitting at full volume.
    try:
        load_rules({"checks": [{"id": "SIDE-ZIP", "left_field": "L",
                                "right_field": "R", "polygon_field": "Z",
                                "placeholder_severity": "off"}]})
        check(False, "switching off a tier severity is refused  <-- pinned defect")
    except ValueError as exc:
        check("SIDE-ZIP emits those records" in str(exc),
              "switching off a tier severity is refused  <-- pinned defect")
        check("\"enabled\": false" in str(exc),
              "and the refusal names the knob that does work")
        check("INFO" in str(exc), "and names demotion as the other way out")

    bare = load_rules([{"id": "A", "left_field": "L", "right_field": "R",
                        "polygon_field": "Z"}])
    check(len(bare) == 1 and bare[0]["severity"] == "ERROR",
          "a bare list of rules is accepted and defaults to ERROR")
    check(bare[0]["placeholder_severity"] == "WARNING",
          "the placeholder tier defaults to WARNING, so a backlog cannot bury "
          "the genuine mismatches")
    check(bare[0]["blank_severity"] is None,
          "the blank tier has no default and falls back to the rule severity")
    demoted = load_rules([{"id": "A", "left_field": "L", "right_field": "R",
                           "polygon_field": "Z", "placeholder_severity": "info"}])
    check(demoted[0]["placeholder_severity"] == "INFO",
          "a severity is read case insensitively")
    check(load_rules([{"id": "A", "left_field": "L", "right_field": "R",
                       "polygon_field": "Z", "placeholder": 99}])[0]["placeholder"]
          == "99",
          "a numeric placeholder is normalized to text")
    check(load_rules([{"id": "A", "left_field": "L", "right_field": "R",
                       "polygon_field": "Z",
                       "outside_default": "unincorporated"}])[0]["outside_default"]
          == "UNINCORPORATED", "an outside_default is upper-cased once, at load")

    # ---- a whole run over a fixture
    def poly(value, x0, y0, x1, y1, field="ZIP"):
        return {"type": "Feature", "properties": {field: value},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}

    def street(oid, coords, props):
        return {"type": "Feature", "id": oid, "properties": props,
                "geometry": {"type": "LineString", "coordinates":
                             [list(c) for c in coords]}}

    # Two ZIP polygons meeting on the y axis. Every road below runs north along
    # that seam, so its left side is 34482 and its right side is 34475.
    zip_layer = polygons_from_geojson(
        {"type": "FeatureCollection",
         "features": [poly("34482", -500.0, -500.0, 0.0, 500.0),
                      poly("34475", 0.0, -500.0, 500.0, 500.0)]}, "ZIP")
    check(len(zip_layer) == 2, "the fixture ZIP layer loaded two polygons")
    check(containing_value(zip_layer, (-10.0, 0.0)) == "34482",
          "west of the seam is 34482")
    check(containing_value(zip_layer, (10.0, 0.0)) == "34475",
          "east of the seam is 34475")

    seam = [(0.0, -100.0), (0.0, 100.0)]
    good = street(1, seam, {"ZIP_L": "34482", "ZIP_R": "34475"})
    bad = street(2, seam, {"ZIP_L": "34475", "ZIP_R": "34475"})
    holder = street(3, seam, {"ZIP_L": "99999", "ZIP_R": "34475"})
    blank = street(4, seam, {"ZIP_L": "", "ZIP_R": "34475"})

    def run(features, rule=zips, layer=None, **kw):
        streets = streets_from_geojson({"type": "FeatureCollection",
                                        "features": features})
        return evaluate(streets, [rule],
                        {rule["id"]: zip_layer if layer is None else layer}, **kw)

    rep = run([good])
    check(rep["counts"]["sides"] == 2 and not rep["findings"],
          "a correctly stamped seam road produces no finding")
    check(rep["counts"]["agree"] == 2, "and both of its sides agree")
    check(sign_verdict(rep["sign"]) == "PASS",
          "its two sides differ, so it proves the probe reached the right side")
    check(gate(rep) == 0, "and the run exits 0")

    # The default offset is a measured distance, not a free parameter: ten
    # units clears the pavement and still lands in the parcel fabric either
    # side. These two bands sit between five and fifteen units out, so the
    # default reaches them and a thirty unit step sails past both into open
    # ground. A run with no offset argument must land inside.
    bands = polygons_from_geojson(
        {"type": "FeatureCollection",
         "features": [poly("34482", -15.0, -500.0, -5.0, 500.0),
                      poly("34475", 5.0, -500.0, 15.0, 500.0)]}, "ZIP")
    rep = run([good], layer=bands)
    check(rep["counts"]["agree"] == 2 and rep["counts"]["gap"] == 0,
          "the default offset lands inside a band ten units out"
          "  <-- pinned defect")
    rep = run([good], layer=bands, offset=30.0)
    check(rep["counts"]["agree"] == 0 and len(rep["findings"]) == 2,
          "and a thirty unit offset steps past both bands onto ground no "
          "polygon covers")

    # Both halves of the comparison are folded up, so a layer and a street that
    # disagree only in case still agree. A shapefile carrying "Riverton" against a
    # street stamped "RIVERTON" would otherwise report every side in the city.
    muni_mixed = polygons_from_geojson(
        {"type": "FeatureCollection",
         "features": [poly("Riverton", -500.0, -500.0, 0.0, 500.0, "MUNI"),
                      poly("lakeview", 0.0, -500.0, 500.0, 500.0, "MUNI")]},
        "MUNI")
    rep = run([street(5, seam, {"MUNI_L": "riverton", "MUNI_R": "LAKEVIEW"})],
              rule=muni, layer=muni_mixed)
    check(rep["counts"]["agree"] == 2 and not rep["findings"],
          "a side and its polygon that differ only in case agree"
          "  <-- pinned defect")
    rep = run([street(6, seam, {"MUNI_L": "  riverton  ", "MUNI_R": "LAKEVIEW"})],
              rule=muni, layer=muni_mixed)
    check(rep["counts"]["agree"] == 2,
          "and a padded stored value is stripped before the comparison")

    rep = run([bad])
    check(len(rep["findings"]) == 1,
          "the road stamped 34475 on both sides has exactly one wrong side")
    f = rep["findings"][0]
    check(f["side"] == "L" and f["stored"] == "34475" and f["containing"] == "34482",
          "and it is the left side, fronting 34482, stamped 34475"
          "  <-- pinned defect")
    check(f["severity"] == "ERROR", "a genuine mismatch is an ERROR")
    check(gate(rep) == 1, "a run with an ERROR exits 1")

    rep = run([holder])
    check(len(rep["findings"]) == 1 and rep["findings"][0]["tier"] == "placeholder",
          "the placeholder side is a placeholder finding")
    check(rep["findings"][0]["severity"] == "WARNING",
          "and it emits at WARNING, not ERROR")
    check(gate(rep) == 0, "so a run of placeholders alone exits 0")

    rep = run([blank])
    check(len(rep["findings"]) == 1 and rep["findings"][0]["tier"] == "blank",
          "the blank side fires because the polygon can name it")
    check(rep["findings"][0]["severity"] == "ERROR",
          "and inherits the rule severity, having no blank_severity")

    # A placeholder demote and a genuine mismatch in ONE run, which is the
    # reason the tiers exist. A "not assigned" sentinel is usually carried on
    # thousands of sides while the sides that genuinely disagree number in the
    # hundreds, and at one severity the backlog buries the findings.
    rep = run([bad, holder])
    sevs = sorted(f["severity"] for f in rep["findings"])
    check(sevs == ["ERROR", "WARNING"],
          "a placeholder demote and a genuine mismatch land in one run"
          "  <-- pinned defect")
    check(gate(rep) == 1, "and the ERROR still decides the exit code")

    # ---- the rollup groups by containing/stored and sorts by count
    many = [street(10 + i, seam, {"ZIP_L": "34475", "ZIP_R": "34475"})
            for i in range(6)]
    off_seam = [(300.0, -100.0), (300.0, 100.0)]
    odd = [street(50, off_seam, {"ZIP_L": "34479", "ZIP_R": "34479"}),
           street(51, off_seam, {"ZIP_L": "34479", "ZIP_R": "34479"})]
    rep = run(many + odd)
    groups = rep["rollup"]
    check(len(groups) == 3,
          "the left and right sides of the one-offs group separately, because "
          "they are different fields to fix")
    check(groups[0]["count"] == 6 and groups[0]["stored"] == "34475",
          "the systematic drift of 6 sides sorts above the one-offs"
          "  <-- pinned defect")
    check(groups[0]["containing"] == "34482",
          "and the group names the ZIP those sides actually front")
    check([g["count"] for g in groups[1:]] == [2, 2],
          "the one-off sides make two groups of two below it")
    check(groups[1]["field"] == "ZIP_L" and groups[2]["field"] == "ZIP_R",
          "and tied groups are ordered by field, so two runs diff cleanly"
          "  <-- pinned defect")
    check(groups[0]["sample_ids"] == [10, 11, 12, 13, 14],
          "a group carries the first five ids, not all six")
    check(len(rollup(rep["findings"], sample=2)[0]["sample_ids"]) == 2,
          "the sample size is honoured")
    check(rollup([]) == [], "no findings roll up to no groups")

    # ---- a stamped side outside every polygon groups under "(outside all)"
    rep = run([street(85, [(10000.0, -100.0), (10000.0, 100.0)],
                      {"ZIP_L": "34475", "ZIP_R": "34475"})])
    check(len(rep["findings"]) == 2 and
          rep["findings"][0]["containing"] is None,
          "a stamped side outside every polygon has no containing value")
    check(rep["rollup"][0]["containing"] == "(outside all)",
          "and the rollup names that rather than printing None"
          "  <-- pinned defect")

    # ---- the sign check: only discriminating segments can prove handedness
    rep = run([good])
    check(rep["sign"]["discriminating"] == 1 and rep["sign"]["right_match"] == 1,
          "the seam road counts as one discriminating segment that matched")
    interior = [(100.0, -100.0), (100.0, 100.0)]
    same = street(60, interior, {"ZIP_L": "34475", "ZIP_R": "34475"})
    rep = run([same] * 100)
    check(rep["sign"]["discriminating"] == 0,
          "100 segments with the same ZIP on both sides discriminate nothing"
          "  <-- pinned defect")
    check(sign_verdict(rep["sign"]) == "INSUFFICIENT",
          "so the probe sign check is INSUFFICIENT, not PASS  <-- pinned defect")
    check(not rep["findings"], "and the run itself found nothing wrong")
    check(gate(rep) == 3,
          "a clean run that could not have failed exits 3, not 0"
          "  <-- pinned defect")
    flipped = street(61, seam, {"ZIP_L": "34475", "ZIP_R": "34482"})
    rep = run([flipped])
    check(rep["sign"]["left_as_right"] == 1 and rep["sign"]["right_match"] == 0,
          "a road stamped with its sides swapped matches left as right")
    check(sign_verdict(rep["sign"]) == "FLIPPED",
          "and the sign check reports FLIPPED  <-- pinned defect")
    check(gate(rep) == 1, "a flipped probe exits 1 even without an ERROR tier")
    rep = run([flipped, good, good])
    check(sign_verdict(rep["sign"]) == "PASS",
          "one swapped road among three does not flip the verdict")
    rep = run([flipped, good])
    check(rep["sign"]["right_match"] == 1 and rep["sign"]["left_as_right"] == 1,
          "one road each way leaves the evidence exactly balanced")
    check(sign_verdict(rep["sign"]) == "INSUFFICIENT",
          "a tie is contradictory evidence, so the sign is not settled and the "
          "verdict is not PASS  <-- pinned defect")
    check(gate(rep) == 1,
          "this tie is gated at 1 anyway, because the swapped road is also an "
          "ERROR finding")
    rep = run([street(62, seam, {"ZIP_L": "34482", "ZIP_R": ""})])
    check(rep["sign"]["discriminating"] == 0,
          "a segment with no stored right value cannot prove the sign either")
    rep = run([street(63, seam, {"ZIP_L": "34482", "ZIP_R": "34479"})])
    check(rep["sign"]["discriminating"] == 1,
          "a right value naming neither side still counts as an opportunity")
    check(rep["sign"]["right_match"] == 0 and rep["sign"]["left_as_right"] == 0,
          "but it settles nothing either way")
    check(sign_verdict(rep["sign"]) == "INSUFFICIENT",
          "so the sign check counts evidence, not opportunities"
          "  <-- pinned defect")

    # A flipped probe with no ERROR finding. The sides are swapped and the rule
    # emits at WARNING, so nothing but the sign check can fail the run.
    warn_rule = dict(zips)
    warn_rule["severity"] = "WARNING"
    rep = run([flipped], rule=warn_rule)
    check(all(f["severity"] == "WARNING" for f in rep["findings"]),
          "a rule set to WARNING emits no ERROR")
    check(gate(rep) == 1,
          "a flipped probe fails the run on its own, with no ERROR present"
          "  <-- pinned defect")
    rep = run([flipped, good], rule=warn_rule)
    check(sign_verdict(rep["sign"]) == "INSUFFICIENT",
          "one road each way ties, and a tie settles nothing")
    check(gate(rep) == 3,
          "so a tied run with no ERROR exits 3, not 0  <-- pinned defect")

    # ---- ambiguity: a side spanning two polygons gets no verdict
    # This road runs east along y=0 with 34482 to its west and 34475 to its
    # east, so its NORTH (left) side spans both.
    crossing = street(70, [(-100.0, 0.0), (100.0, 0.0)],
                      {"ZIP_L": "34482", "ZIP_R": "34482"})
    rep = run([crossing])
    check(rep["counts"]["ambiguous"] == 0,
          "a single midpoint sample cannot see that a side spans two polygons")
    rep = run([crossing], dense=True)
    check(rep["counts"]["agree"] == 2 and not rep["findings"],
          "the dense sweep accepts a stamp naming one of the two ZIPs the "
          "side fronts  <-- pinned defect")
    check(rep["counts"]["ambiguous"] == 2 and
          rep["counts"]["ambiguous_defensible"] == 2,
          "and still reports both sides as spanning two polygons, because the "
          "split is owed whether or not the stamp is defensible"
          "  <-- pinned defect")
    check(rep["ambiguous"][0]["defensible"] is True and
          "which is one of them" in rep["ambiguous"][0]["desc"],
          "the record says the stamp names one of the two")

    # The same road stamped with a ZIP it fronts nowhere. Now there is no
    # single right answer to report, so it is ambiguous rather than a verdict.
    nowhere = street(71, [(-100.0, 5.0), (100.0, 5.0)],
                     {"ZIP_L": "34479", "ZIP_R": "34479"})
    rep = run([nowhere], dense=True)
    check(rep["counts"]["ambiguous"] == 2 and
          rep["counts"]["ambiguous_defensible"] == 0,
          "a stamp naming neither ZIP on a side spanning two is ambiguous and "
          "not defensible  <-- pinned defect")
    check("which is neither of them" in rep["ambiguous"][0]["desc"],
          "and the record says so")
    check(rep["ambiguous"][0]["values"] == ["34475", "34482"],
          "and the record names the two polygons the side spans")
    check("split the centerline" in rep["ambiguous"][0]["desc"],
          "the ambiguous record carries the remedy, not a verdict"
          "  <-- pinned defect")
    check(not rep["findings"],
          "an ambiguous side produces no finding, right or wrong"
          "  <-- pinned defect")
    check(rep["counts"]["sides"] == 2,
          "it was still compared, so it counts as a side compared")
    rep = run([nowhere])
    check(rep["counts"]["ambiguous"] == 0 and len(rep["findings"]) == 2,
          "the same road under a single sample reads as two plain mismatches")
    rep = run([street(72, [(-100.0, 5.0), (100.0, 5.0)], {})], dense=True)
    check(rep["counts"]["ambiguous"] == 2,
          "a blank side spanning two polygons is ambiguous, not a blank finding")

    # ---- skipped geometry is counted, never guessed
    rep = run([street(80, [(50.0, 50.0), (50.0, 50.0)], {"ZIP_L": "34475"})])
    check(rep["counts"]["skipped"] == 1 and rep["counts"]["sides"] == 0,
          "a zero length segment is skipped and counted  <-- pinned defect")
    check(rep["counts"]["segments"] == 1, "and still counts as a segment read")
    rep = run([street(81, [(0.0, -50.0), (0.0, 50.0), (0.0, -50.0)],
                      {"ZIP_L": "34482", "ZIP_R": "34475"})])
    check(rep["counts"]["skipped"] == 0 and rep["counts"]["unsampled"] == 2,
          "a spur that folds at its midpoint has length, so its two sides are "
          "counted unsampled rather than the segment skipped  <-- pinned defect")
    check(rep["counts"]["sides"] == 0 and not rep["findings"],
          "and neither side is compared")

    # ---- disabled rules
    off_rule = dict(zips)
    off_rule["enabled"] = False
    rep = run([bad], rule=off_rule)
    check(not rep["findings"] and rep["counts"]["sides"] == 0,
          "a rule with enabled false runs nothing at all")

    # ---- the agreement rate excludes conservative misses from both halves
    counts = {"agree": 9, "_findings": 1, "gap": 500}
    check(abs(agreement_rate(counts) - 0.9) < 1e-12,
          "500 conservative misses do not move the agreement rate"
          "  <-- pinned defect")
    check(agreement_rate({"agree": 0, "_findings": 0, "gap": 7}) is None,
          "a run that decided nothing has no agreement rate, not 100 percent"
          "  <-- pinned defect")
    check(agreement_rate({"agree": 0, "_findings": 4, "gap": 0}) == 0.0,
          "a run where everything disagreed rates zero")

    # ---- describe(), the printed report
    rep = run([bad, holder])
    text = "\n".join(describe(rep, dense=False))
    check("sides compared: 4" in text, "the report counts the sides compared")
    check("run --dense to find these" in text,
          "and says ambiguity is invisible without the dense sweep")
    check("probe sign check: PASS" in text, "the report prints the sign verdict")
    check("agreement:" in text, "and states an agreement rate")
    dense_text = "\n".join(describe(run([nowhere], dense=True), dense=True))
    check("run --dense" not in dense_text,
          "the dense hint is absent once --dense was used")
    check("split the centerline" in dense_text,
          "and the ambiguous sides are printed with their remedy")
    insufficient = "\n".join(describe(run([same] * 3), dense=False))
    check("INSUFFICIENT" in insufficient and
          "would give the same answer" in insufficient,
          "an INSUFFICIENT run says why in words, not only in the exit code")
    unsettled = "\n".join(describe(
        run([street(64, seam, {"ZIP_L": "34482", "ZIP_R": "34479"})]), dense=False))
    check("settles the question" in unsettled,
          "an INSUFFICIENT run that had opportunities says why they failed")
    spur_text = "\n".join(describe(
        run([street(65, [(0.0, -50.0), (0.0, 50.0), (0.0, -50.0)], {})]),
        dense=False))
    check("folds back on itself" in spur_text,
          "and a folded spur's sides are reported as unsampled")
    flip_text = "\n".join(describe(run([flipped]), dense=False))
    check("wrong side" in flip_text, "a FLIPPED run says every finding is wrong")
    far_away = street(90, [(10000.0, -100.0), (10000.0, 100.0)], {})
    empty_text = "\n".join(describe(run([far_away]), dense=False))
    check("agreement rate: none" in empty_text,
          "a run of blank sides outside every polygon has no agreement rate")
    check("conservative misses: 2" in empty_text,
          "and reports both sides as conservative misses  <-- pinned defect")
    skip_text = "\n".join(describe(
        run([street(91, [(1.0, 1.0), (1.0, 1.0)], {})]), dense=False))
    check("1 skipped" in skip_text, "and a skipped segment is reported")

    # ---- geojson parsing refuses what it cannot probe
    raises(lambda: streets_from_geojson({"type": "FeatureCollection", "features": [
        {"geometry": {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]]]},
         "properties": {}}]}),
           "a MultiLineString is refused, not flattened  <-- pinned defect")
    raises(lambda: streets_from_geojson({"type": "FeatureCollection", "features": [
        {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}]}),
           "a point in the street layer is refused")
    raises(lambda: streets_from_geojson({"type": "Topology"}),
           "a document that is not a FeatureCollection is refused")
    raises(lambda: streets_from_geojson("streets"),
           "a string where GeoJSON was expected is refused")
    raises(lambda: streets_from_geojson({"type": "FeatureCollection"}),
           "a FeatureCollection with no features list is refused")
    one = streets_from_geojson({"type": "Feature", "id": 7, "properties": {},
                                "geometry": {"type": "LineString",
                                             "coordinates": [[0, 0], [1, 1]]}})
    check(len(one) == 1 and one[0]["oid"] == 7, "a bare Feature is accepted")
    numbered = streets_from_geojson({"type": "FeatureCollection", "features": [
        {"properties": {"OBJECTID": 44}, "geometry": {"type": "LineString",
                                                      "coordinates": [[0, 0], [1, 0]]}}]})
    check(numbered[0]["oid"] == 44, "OBJECTID is used when the feature has no id")
    indexed = streets_from_geojson([{"properties": {}, "geometry": {
        "type": "LineString", "coordinates": [[0, 0], [1, 0]]}}])
    check(indexed[0]["oid"] == 1,
          "a feature with neither is numbered from one, not from zero")
    named = streets_from_geojson([{"properties": {"SEGID": "A7"}, "geometry": {
        "type": "LineString", "coordinates": [[0, 0], [1, 0]]}}], id_field="SEGID")
    check(named[0]["oid"] == "A7", "--id-field picks the property to report by")
    raises(lambda: streets_from_geojson([{"properties": {}, "geometry": {
        "type": "LineString", "coordinates": [[0, 0], [1, 0]]}}], id_field="SEGID"),
           "a missing --id-field property is refused, not silently numbered")

    raises(lambda: polygons_from_geojson({"type": "FeatureCollection", "features": [
        {"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
         "properties": {}}]}, "ZIP"),
           "a line in the polygon layer is refused")
    raises(lambda: polygons_from_geojson({"type": "FeatureCollection",
                                          "features": []}, "ZIP"),
           "an empty polygon layer is refused, not read as total coverage"
           "  <-- pinned defect")
    raises(lambda: polygons_from_geojson({"type": "FeatureCollection", "features": [
        {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]},
         "properties": {"ZIP": "1"}}]}, "ZIP"),
           "a polygon layer holding only a two vertex ring is refused")
    multi = polygons_from_geojson({"type": "FeatureCollection", "features": [
        {"properties": {"ZIP": "34475"}, "geometry": {"type": "MultiPolygon",
         "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                         [[[9, 9], [10, 9], [10, 10], [9, 10], [9, 9]]]]}}]}, "ZIP")
    check(containing_value(multi, (9.5, 9.5)) == "34475",
          "the second part of a MultiPolygon contains points too"
          "  <-- pinned defect")
    check(containing_value(multi, (5.0, 5.0)) is None,
          "and the space between its parts contains none")

    # ---- the geographic-coordinate refusal
    degrees = streets_from_geojson([{"properties": {}, "geometry": {
        "type": "LineString", "coordinates": [[-82.14, 29.18], [-82.13, 29.19]]}}])
    check(looks_geographic(degrees), "lat/lon coordinates are detected")
    feet = streets_from_geojson([{"properties": {}, "geometry": {
        "type": "LineString", "coordinates": [[556000, 1600000], [556100, 1600100]]}}])
    check(not looks_geographic(feet), "state plane feet are not")
    check(not looks_geographic([]), "an empty layer is not called geographic")
    check(not looks_geographic([{"coords": []}]),
          "nor is a layer whose features have no vertices")

    # ---- argument parsing: the dangerous flags are off unless asked for
    args = _parse(["--streets", "s.geojson", "--rules", "r.json"])
    check(args.apply is False, "--apply defaults off  <-- pinned defect")
    check(args.dense is False, "--dense defaults off")
    check(args.self_test is False, "--self-test defaults off")
    check(args.out is None, "--out defaults to nothing")
    # Absolute values, not the constants that set them. Reading a default back
    # against its own constant passes whatever the constant says, so it proves
    # the flag exists and nothing about what it is.
    check(args.offset == 10.0, "--offset defaults to ten units  <-- pinned defect")
    check(args.chord == 1.0, "--chord defaults to one unit  <-- pinned defect")
    check(args.id_field is None, "--id-field defaults to nothing")
    check(args.sample == 5, "--sample defaults to five ids a group")
    check(_parse(["--streets", "s", "--rules", "r", "--apply"]).apply is True,
          "--apply is read")
    check(_parse(["--streets", "s", "--rules", "r", "--dense"]).dense is True,
          "--dense is read")
    check(_parse(["--self-test"]).self_test is True, "--self-test is read")
    check(_parse(["--streets", "a", "--rules", "r"]).streets == "a",
          "--streets is read")
    check(_parse(["--streets", "a", "--rules", "b"]).rules == "b",
          "--rules is read")
    check(_parse(["--streets", "a", "--rules", "r", "--out", "o.json"]).out
          == "o.json", "--out is read")
    check(_parse(["--streets", "a", "--rules", "r", "--offset", "25"]).offset
          == 25.0, "--offset is read")
    check(_parse(["--streets", "a", "--rules", "r", "--chord", "4"]).chord == 4.0,
          "--chord is read")
    check(_parse(["--streets", "a", "--rules", "r", "--sample", "2"]).sample == 2,
          "--sample is read")
    check(_parse(["--streets", "a", "--rules", "r", "--id-field", "SEGID"]).id_field
          == "SEGID", "--id-field is read")

    # ---- the cli, end to end, against files in a temp directory
    tmp = tempfile.mkdtemp(prefix="sidestamp_")
    try:
        def put(name, obj):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(obj, handle)
            return path

        zip_path = put("zips.geojson", {"type": "FeatureCollection", "features": [
            poly("34482", -500.0, -500.0, 0.0, 500.0),
            poly("34475", 0.0, -500.0, 500.0, 500.0)]})
        rules_path = put("rules.json", {"checks": [{
            "id": "SIDE-ZIP", "label": "ZIP boundary", "left_field": "ZIP_L",
            "right_field": "ZIP_R", "polygons": "zips.geojson",
            "polygon_field": "ZIP", "placeholder": "99999"}]})
        clean_path = put("clean.geojson", {"type": "FeatureCollection",
                                           "features": [good]})
        dirty_path = put("dirty.geojson", {"type": "FeatureCollection",
                                           "features": [good, bad, holder]})
        flat_path = put("flat.geojson", {"type": "FeatureCollection",
                                         "features": [same, same]})
        degrees_path = put("degrees.geojson", {"type": "FeatureCollection",
                                               "features": [
            street(1, [(-82.14, 29.18), (-82.14, 29.19)],
                   {"ZIP_L": "34482", "ZIP_R": "34475"})]})
        out_path = os.path.join(tmp, "report.json")

        def run_cli(extra):
            # The report goes to a buffer. A nested run printing its own
            # verdict into the self-test's output would make the pass/fail
            # list unreadable, and the README quotes that list.
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    return main(["--streets"] + extra)

        def run_main(argv):
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    return main(argv)

        check(run_cli([clean_path, "--rules", rules_path]) == 0,
              "a clean run over real files on disk exits 0")
        check(run_cli([dirty_path, "--rules", rules_path]) == 1,
              "a run with a genuine mismatch exits 1")
        check(run_cli([flat_path, "--rules", rules_path]) == 3,
              "a run that could not have failed exits 3  <-- pinned defect")
        check(run_cli([dirty_path, "--rules", rules_path, "--dense"]) == 1,
              "--dense runs end to end over real files")
        check(run_cli([dirty_path, "--rules", rules_path, "--offset", "40",
                       "--chord", "2", "--sample", "2"]) == 1,
              "--offset, --chord and --sample run end to end together")
        check(run_cli([dirty_path, "--rules", rules_path,
                       "--id-field", "OBJECTID"]) == 2,
              "--id-field naming a property no feature carries exits 2")
        multi_path = put("multi.geojson", {"type": "FeatureCollection", "features": [
            {"properties": {}, "geometry": {"type": "MultiLineString",
                                            "coordinates": [[[0, 0], [0, 10]]]}}]})
        check(run_cli([multi_path, "--rules", rules_path]) == 2,
              "a MultiLineString streets file exits 2, not 1  <-- pinned defect")

        # --out writes nothing without --apply.
        check(run_cli([dirty_path, "--rules", rules_path, "--out", out_path]) == 1,
              "--out alone still reports")
        check(not os.path.exists(out_path),
              "--out without --apply writes nothing at all  <-- pinned defect")
        check(run_cli([dirty_path, "--rules", rules_path, "--out", out_path,
                       "--apply"]) == 1, "--apply writes the report and still gates")
        check(os.path.exists(out_path), "and the file is there")
        with open(out_path, "r", encoding="utf-8") as handle:
            written = json.load(handle)
        check(written["counts"]["sides"] == 6,
              "the written report carries the counts")
        check(len(written["findings"]) == 2 and written["rollup"],
              "and the findings and the rollup")
        check(written["sign"]["discriminating"] == 3,
              "and the discriminating segment count")
        check(run_cli([degrees_path, "--rules", rules_path]) == 64,
              "a ten unit offset on lat/lon data is refused  <-- pinned defect")
        check(run_cli([degrees_path, "--rules", rules_path,
                       "--offset", "0.0001"]) in (0, 1, 3),
              "and a degree-sized offset on the same data is allowed")

        check(run_main(["--rules", rules_path]) == 64, "--streets is required")
        check(run_main(["--streets", clean_path]) == 64, "--rules is required")
        check(run_cli([clean_path, "--rules", rules_path, "--apply"]) == 64,
              "--apply without --out is a usage error  <-- pinned defect")
        check(run_cli([clean_path, "--rules", rules_path, "--offset", "0"]) == 64,
              "a zero offset probes the centerline itself and is refused")
        check(run_cli([clean_path, "--rules", rules_path, "--chord", "0"]) == 64,
              "a zero chord has no direction and is refused")
        check(run_cli([clean_path, "--rules", rules_path, "--offset", "-5"]) == 64,
              "a negative offset is refused")
        # A NaN is false against every comparison, so it passes a bare `> 0`
        # test and probes a point that is inside no polygon anywhere. The run
        # then reports every side as inside no polygon and reads like a
        # measurement of a coverage gap that is not there.
        check(run_cli([clean_path, "--rules", rules_path, "--offset", "nan"]) == 64,
              "a NaN offset is refused, not probed  <-- pinned defect")
        check(run_cli([clean_path, "--rules", rules_path, "--chord", "nan"]) == 64,
              "and a NaN chord is refused too  <-- pinned defect")
        check(run_cli([clean_path, "--rules", rules_path, "--offset", "inf"]) == 64,
              "an infinite offset is refused  <-- pinned defect")
        check(run_cli([clean_path, "--rules", rules_path, "--chord", "inf"]) == 64,
              "and an infinite chord is refused")
        check(run_cli([clean_path, "--rules", rules_path, "--sample", "0"]) == 64,
              "a zero rollup sample is refused")
        check(run_cli([os.path.join(tmp, "nope.geojson"), "--rules", rules_path])
              == 2, "an unreadable streets file exits 2, not 1")
        check(run_cli([clean_path, "--rules", os.path.join(tmp, "nope.json")]) == 2,
              "an unreadable rules file exits 2")

        broken = put("broken.json", {"checks": [{"id": "A", "left_field": "L",
                                                 "right_field": "R",
                                                 "polygon_field": "Z",
                                                 "polygons": "zips.geojson",
                                                 "typo": 1}]})
        check(run_cli([clean_path, "--rules", broken]) == 64,
              "a rules file with an unknown key exits 64, not 2")
        missing_layer = put("missing.json", {"checks": [{
            "id": "A", "left_field": "ZIP_L", "right_field": "ZIP_R",
            "polygon_field": "ZIP", "polygons": "gone.geojson"}]})
        check(run_cli([clean_path, "--rules", missing_layer]) == 2,
              "a rules file naming a layer that is not there exits 2")
        no_polygons = put("nopolys.json", {"checks": [{
            "id": "A", "left_field": "ZIP_L", "right_field": "ZIP_R",
            "polygon_field": "ZIP"}]})
        check(run_cli([clean_path, "--rules", no_polygons]) == 64,
              "a rule with no polygons path is refused")
        with open(os.path.join(tmp, "bad.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        check(run_cli([clean_path, "--rules", os.path.join(tmp, "bad.json")]) == 2,
              "a rules file that is not JSON exits 2")

        # An absolute polygons path is used as given, rather than joined to the
        # rules directory. os.path.join already does this; the assertion pins
        # it, because a wrong answer here only shows on one platform.
        absolute = put("absolute.json", {"checks": [{
            "id": "A", "left_field": "ZIP_L", "right_field": "ZIP_R",
            "polygon_field": "ZIP", "polygons": zip_path}]})
        check(run_cli([clean_path, "--rules", absolute]) == 0,
              "an absolute polygons path is read as given  <-- pinned defect")

        # A disabled rule needs no layer on disk at all, so switching one off
        # is enough to run without the layer it reads.
        mixed = put("mixed.json", {"checks": [
            {"id": "LIVE", "left_field": "ZIP_L", "right_field": "ZIP_R",
             "polygon_field": "ZIP", "polygons": "zips.geojson"},
            {"id": "OFF", "left_field": "ZIP_L", "right_field": "ZIP_R",
             "polygon_field": "ZIP", "polygons": "gone.geojson",
             "enabled": False}]})
        check(run_cli([clean_path, "--rules", mixed]) == 0,
              "a disabled rule does not need its layer to exist"
              "  <-- pinned defect")

        # A write that fails is not a batch that failed the gate. Writing the
        # report over a directory is the cheapest real failure to provoke.
        check(run_cli([dirty_path, "--rules", rules_path, "--out", tmp,
                       "--apply"]) == 2,
              "a report that could not be written exits 2, not 1"
              "  <-- pinned defect")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 70)
    total = passed[0] + len(failed)
    if failed:
        print("%d assertions, %d failed" % (total, len(failed)))
        for item in failed:
            print("  FAILED: %s" % item)
        return 1
    print("%d assertions, 0 failed" % total)
    return 0


# ----------------------------------------------------------------------- cli

def _parse(argv):
    ap = argparse.ArgumentParser(
        prog="sidestamp.py",
        description="Check each side of a centerline against the polygon that "
                    "side actually fronts.",
        epilog="Nothing is written without --apply. Exit codes: 0 clean and "
               "provable, 1 findings, 2 unreadable input, 3 the run proved "
               "nothing, 64 usage error.",
    )
    ap.add_argument("--streets", help="GeoJSON of single part LineString "
                                      "centerlines carrying the sided fields")
    ap.add_argument("--rules", help="JSON file describing the sided checks")
    ap.add_argument("--offset", type=float, default=DEFAULT_OFFSET,
                    help="distance stepped perpendicular to reach a probe "
                         "point, in the units of the data (default %.1f)"
                         % DEFAULT_OFFSET)
    ap.add_argument("--chord", type=float, default=DEFAULT_CHORD,
                    help="chord straddling the sample point used to measure "
                         "the direction of travel (default %.1f)" % DEFAULT_CHORD)
    ap.add_argument("--dense", action="store_true",
                    help="sample nine points along each side instead of one. "
                         "Finds sides that span two polygons. Off by default "
                         "because it is nine times the work.")
    ap.add_argument("--id-field", dest="id_field",
                    help="street property to report segments by. Default: the "
                         "GeoJSON feature id, then OBJECTID, then the position")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help="sample OBJECTIDs carried per rollup group "
                         "(default %d)" % DEFAULT_SAMPLE)
    ap.add_argument("--out", help="path for the JSON report")
    ap.add_argument("--apply", action="store_true",
                    help="write --out. Without this nothing is written.")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the offline assertions and exit")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.self_test:
        return self_test()

    if not args.streets:
        print("error: --streets is required. Use --self-test to verify the "
              "tool without data.", file=sys.stderr)
        return 64
    if not args.rules:
        print("error: --rules is required.", file=sys.stderr)
        return 64
    # Every comparison below one of these is False for a NaN, so a NaN slips
    # through a bare `<= 0` test and lands in the arithmetic, where it produces
    # a probe point of (nan, nan). That point is inside no polygon, so the run
    # reports every side as inside no polygon and finishes looking like a
    # measurement. An infinity does the same thing. Both are refused here
    # rather than at the point of use, so the operator gets a usage error
    # instead of a report built from nothing.
    for flag, value in (("--offset", args.offset), ("--chord", args.chord)):
        if not (value > 0.0) or value in (float("inf"), float("-inf")):
            print("error: %s must be a finite number greater than zero. A zero "
                  "offset probes the centerline itself, which is on neither "
                  "side, and a NaN or an infinity probes nothing at all."
                  % flag, file=sys.stderr)
            return 64
    if args.sample < 1:
        print("error: --sample must be at least 1.", file=sys.stderr)
        return 64
    if args.apply and not args.out:
        print("error: --apply needs --out.", file=sys.stderr)
        return 64

    try:
        raw_rules = read_json(args.rules)
    except (IOError, OSError, ValueError) as exc:
        # json.JSONDecodeError is a ValueError, so a rules file that is not
        # JSON lands here rather than in the validator below. A file that
        # cannot be parsed is an unreadable input; a file that parses but does
        # not validate is something the operator wrote wrong. Only the exit
        # code tells a scheduled job which of the two it has to fix.
        print("error: %s" % exc, file=sys.stderr)
        return 2
    try:
        rules = load_rules(raw_rules)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 64

    rules_dir = os.path.dirname(os.path.abspath(args.rules))
    polys_by_rule = {}
    try:
        streets = streets_from_geojson(read_json(args.streets), args.id_field)
        for rule in rules:
            if not rule.get("enabled", True):
                polys_by_rule[rule["id"]] = []
                continue
            if not rule.get("polygons"):
                print("error: rule %s has no 'polygons' path, so there is "
                      "nothing to compare against." % rule["id"], file=sys.stderr)
                return 64
            # Relative to the rules file, so a rules file and its layers move
            # together. os.path.join leaves an absolute path alone.
            path = os.path.join(rules_dir, rule["polygons"])
            polys_by_rule[rule["id"]] = polygons_from_geojson(
                read_json(path), rule["polygon_field"])
    except (IOError, OSError, ValueError) as exc:
        # Geometry this tool cannot probe is an unreadable input, not a usage
        # error. The operator's command line was fine; the data is not.
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if looks_geographic(streets) and args.offset >= GEOGRAPHIC_OFFSET_LIMIT:
        # A ten unit offset in degrees is about 690 miles. The run would
        # complete, report every side as inside no polygon or inside the wrong
        # one, and look like a data problem.
        print("error: the streets look like lat/lon degrees and --offset is "
              "%g, which is about %d miles. Reproject to a projected system in "
              "feet or metres, or pass an offset in degrees."
              % (args.offset, int(args.offset * 69)), file=sys.stderr)
        return 64

    report = evaluate(streets, rules, polys_by_rule, args.offset, args.dense,
                      args.chord)
    report["rollup"] = rollup(report["findings"], args.sample)

    for line in describe(report, args.dense):
        print(line)

    if args.out:
        if args.apply:
            try:
                with open(args.out, "w", encoding="utf-8") as handle:
                    json.dump(report, handle, indent=2, sort_keys=True)
            except (IOError, OSError, TypeError) as exc:
                print("error: could not write %s: %s" % (args.out, exc),
                      file=sys.stderr)
                return 2
            print("\nwrote %s" % args.out)
        else:
            print("\nCheck only. %s was not written. Re-run with --apply."
                  % args.out)

    code = gate(report)
    print("")
    if code == 0:
        print("PASS: every side agrees, and the run contained segments that "
              "could have shown otherwise.")
    elif code == 3:
        print("INSUFFICIENT: no side disagreed, and no segment in this run "
              "could have disagreed. This is not a clean run.")
    else:
        print("FAIL: sides disagree with the polygons they front.")
    return code


if __name__ == "__main__":
    sys.exit(main())
