# sidestamp

Check the ZIP, ESN, city and municipality stamped on each side of a road centerline, or on an
address point, against the polygon it actually fronts, and refuse to call a run clean when nothing
in it could have disagreed.

A road runs north along a ZIP code boundary. The left ZIP field says 10001 and the right ZIP field
says 10001. The west side of that road is in 10003. Every address on it geocodes to a ZIP the
Postal Service does not use for that street, the MSAG range is built from the same attribution, and
the 911 dispatch that reads the centerline sends the call to the wrong area.

Point-in-polygon validation never finds it. A centerline has no single containing polygon. It runs
between them, and it carries a separate attribute for each side. A spatial join relates the line to
the polygons it touches, and the polygon it touches is not the polygon on the left.

Address points have the quieter version of the same trap. A nightly job audits the ZIP stamped on
the address points of one subdivision. All 240 points carry 10001, and all 240 sit in the 10001
polygon. The report says zero mismatches. Point the job at the wrong column, or at a layer that
holds only that one polygon, and it prints the same report. Rename the column in the export, and
a rules file that still names the old one reads every point as blank. Nothing in a run like that
could have failed, so points mode exits 3 for it, and exits 2 when the column the rules name is
not on the points at all.

```
$ python sidestamp.py --self-test
sidestamp self-test: no file, no network, no arcpy
----------------------------------------------------------------------
PASS  left of travel along +X is +Y  <-- pinned defect
PASS  right of travel along +X is -Y  <-- pinned defect
PASS  the same road digitized backwards puts left on the other side
PASS  an out-and-back spur folds at its own midpoint and has no side there  <-- pinned defect
PASS  a chord spanning the whole segment gives the diagonal across the dog-leg instead  <-- pinned defect
...
PASS  a point on a vertex is inside, not a coverage gap  <-- pinned defect
PASS  a point on a horizontal edge is inside  <-- pinned defect
PASS  a point on the hole's edge is inside  <-- pinned defect
PASS  a point on an edge's line but past its end is not on the edge  <-- pinned defect
...
PASS  a float ZIP loses its trailing .0, or it never equals the text ZIP  <-- pinned defect
PASS  a point inside a null-keyed polygon returns empty, not None  <-- pinned defect
PASS  the dense sweep skips the fraction where the spur folds, and says it took 8 samples rather than 9  <-- pinned defect
...
PASS  a side stamped 10001 fronting 10003 is a mismatch  <-- pinned defect
PASS  the 99999 placeholder is its own tier, not a wrong ZIP  <-- pinned defect
PASS  a blank field where the layer covers nothing is a conservative miss  <-- pinned defect
PASS  a blank on a layer declaring outside_default fires  <-- pinned defect
PASS  the same blank on a layer declaring none is silent  <-- pinned defect
PASS  stamped with a city while outside every city is a mismatch  <-- pinned defect
PASS  a value fronting neither fails and names both  <-- pinned defect
...
PASS  a misspelled rule key is refused, not dropped  <-- pinned defect
PASS  a rule whose two sides read one field is refused  <-- pinned defect
PASS  two rules sharing an id are refused  <-- pinned defect
PASS  switching off a tier severity is refused  <-- pinned defect
...
PASS  and it is the left side, fronting 10003, stamped 10001  <-- pinned defect
PASS  a placeholder demote and a genuine mismatch land in one run  <-- pinned defect
PASS  the systematic drift of 6 sides sorts above the one-offs  <-- pinned defect
PASS  and tied groups are ordered by field, so two runs diff cleanly  <-- pinned defect
PASS  and the rollup names that rather than printing None  <-- pinned defect
...
PASS  100 segments with the same ZIP on both sides discriminate nothing  <-- pinned defect
PASS  so the probe sign check is INSUFFICIENT, not PASS  <-- pinned defect
PASS  a clean run that could not have failed exits 3, not 0  <-- pinned defect
PASS  and the sign check reports FLIPPED  <-- pinned defect
PASS  so the sign check counts evidence, not opportunities  <-- pinned defect
PASS  a flipped probe fails the run on its own, with no ERROR present  <-- pinned defect
...
PASS  the dense sweep accepts a stamp naming one of the two ZIPs the side fronts  <-- pinned defect
PASS  and still reports both sides as spanning two polygons, because the split is owed whether or not the stamp is defensible  <-- pinned defect
PASS  a stamp naming neither ZIP on a side spanning two is ambiguous and not defensible  <-- pinned defect
PASS  the ambiguous record carries the remedy, not a verdict  <-- pinned defect
PASS  a stamp naming neither ZIP on a side spanning two is a finding, grouped under both  <-- pinned defect
...
PASS  a zero length segment is skipped and counted  <-- pinned defect
PASS  a spur that folds at its midpoint has length, so its two sides are counted unsampled rather than the segment skipped  <-- pinned defect
PASS  500 conservative misses do not move the agreement rate  <-- pinned defect
PASS  a run that decided nothing has no agreement rate, not 100 percent  <-- pinned defect
...
PASS  a MultiLineString is refused, not flattened  <-- pinned defect
PASS  an empty polygon layer is refused, not read as total coverage  <-- pinned defect
PASS  the second part of a MultiPolygon contains points too  <-- pinned defect
...
PASS  --apply defaults off  <-- pinned defect
PASS  a run that could not have failed exits 3  <-- pinned defect
PASS  a MultiLineString streets file exits 2, not 1  <-- pinned defect
PASS  --out without --apply writes nothing at all  <-- pinned defect
PASS  a ten unit offset on lat/lon data is refused  <-- pinned defect
PASS  --apply without --out is a usage error  <-- pinned defect
PASS  a NaN offset is refused, not probed  <-- pinned defect
PASS  and a NaN chord is refused too  <-- pinned defect
PASS  an infinite offset is refused  <-- pinned defect
PASS  an absolute polygons path is read as given  <-- pinned defect
PASS  a disabled rule does not need its layer to exist  <-- pinned defect
PASS  a report that could not be written exits 2, not 1  <-- pinned defect
PASS  a rules file mixing point and road rules is refused, not half run  <-- pinned defect
PASS  a point layer missing the field a rule reads is refused, naming the rule, the field and the count  <-- pinned defect
PASS  a clean points run that could not have failed exits 3, not 0  <-- pinned defect
PASS  a point 1 unit(s) outside the layer, stamped with the nearest ZIP, is a mismatch and not an agreement  <-- pinned defect
PASS  a point exactly on a shared boundary accepts either stamp, and is counted as on one  <-- pinned defect
PASS  a unique prefix of --points is refused by the parser  <-- pinned defect
PASS  --dense in points mode is refused, not ignored  <-- pinned defect
PASS  and typing the default offset is still refused  <-- pinned defect
...
PASS  a rule that decided no side exits 3 even when another rule settled the sign  <-- pinned defect
PASS  streets with a vertex of nulls exit 2, not a traceback  <-- pinned defect
PASS  a value outside the console code page prints escaped and the run still exits 1, not with a traceback  <-- pinned defect
----------------------------------------------------------------------
390 assertions, 0 failed
```

The same 390 assertions pass on Windows with Python 3.13, 3.12 and 3.9, and on Linux with Python
3.12 and 3.9. The Windows 3.13 and Linux 3.12 runs print identical text once line endings are
normalized. Statement and branch coverage of `sidestamp.py` is 100%, measured with
`coverage run --branch sidestamp.py --self-test` on Windows (see Limits for the excluded lines).

## Requirements

Python 3.9 or later. Standard library only: `argparse`, `collections`, `json`, `math`, `os`, `sys`, and
`contextlib`, `io`, `shutil` and `tempfile` for the self-test's own temporary files. No `arcpy`, no
`shapely`, no `pyproj`, no network and no credentials. It runs the same on ArcGIS Pro's Python and
on a plain `python3`.

```
git clone https://github.com/uhsear/sidestamp.git
python sidestamp.py --self-test
```

## Usage

The streets are a GeoJSON file of single part LineStrings. The rules are a JSON file that names the
sided fields, the polygon layer each one is checked against and the key field on that layer.

```
python sidestamp.py --streets streets.geojson --rules rules.json
python sidestamp.py --streets streets.geojson --rules rules.json --dense
python sidestamp.py --streets streets.geojson --rules rules.json --out report.json --apply
```

A rules file. The `polygons` path is read relative to the rules file, so a rules file and the layers
it names move together.

```json
{
  "checks": [
    {
      "id": "SIDE-ZIP",
      "label": "ZIP boundary",
      "left_field": "ZIP_L",
      "right_field": "ZIP_R",
      "polygons": "zips.geojson",
      "polygon_field": "ZIP",
      "placeholder": "99999"
    },
    {
      "id": "SIDE-MUNI",
      "label": "municipality",
      "left_field": "MUNI_L",
      "right_field": "MUNI_R",
      "polygons": "muni.geojson",
      "polygon_field": "MUNI",
      "outside_default": "UNINCORPORATED"
    }
  ]
}
```

| Rule key | Default | What it does |
|---|---|---|
| `id` | required | Name the findings are reported under. Must be unique. |
| `left_field` / `right_field` | required | Street columns holding the stored value for each side. |
| `polygon_field` | required | Key field read from the polygon under the probe point. |
| `polygons` | required | Polygon layer, relative to the rules file or absolute. |
| `label` | `polygon_field` | Words used for the layer in the report text. |
| `outside_default` | none | Value the layer effectively has outside every polygon. |
| `placeholder` | none | Stored value meaning "not a real code", tiered on its own. |
| `enabled` | `true` | `false` runs nothing and needs no layer on disk. |
| `severity` | `ERROR` | Severity of a genuine mismatch. |
| `placeholder_severity` | `WARNING` | Severity of the placeholder tier. |
| `blank_severity` | `severity` | Severity of a blank field the polygon can name. |

A real run against 360 synthetic segments, 96 of them stamped with the ZIP on the far side of the
road. The fixture is generated, not real data.

```
$ python sidestamp.py --streets streets.geojson --rules rules.json --dense
segments read: 360
  1 skipped: zero length or a single vertex, so no side
sides compared: 1436
  agreement: 1302 of 1436 decided sides, 90.67%
  conservative misses: 0 blank sides where the layer covers nothing
  ambiguous: 8 side(s) spanning more than one polygon
    8 of them are stamped with one of the polygons they span, so the stamp is defensible and the split is still owed

findings: 134
  ERROR    106
  WARNING  28

groups, largest first:
    96  SIDE-ZIP ZIP_L stamped '10001' where the side is '10003' [mismatch]
        sample ids: 1, 2, 3, 4, 5
    14  SIDE-ZIP ZIP_L stamped '99999' where the side is '10001' [placeholder]
        sample ids: 337, 338, 339, 340, 341
    14  SIDE-ZIP ZIP_R stamped '99999' where the side is '10001' [placeholder]
        sample ids: 337, 338, 339, 340, 341
     3  SIDE-MUNI MUNI_L stamped 'RIVERTON' where the side is 'UNINCORPORATED' [mismatch]
        sample ids: 351, 352, 353
     3  SIDE-MUNI MUNI_R stamped 'RIVERTON' where the side is 'UNINCORPORATED' [mismatch]
        sample ids: 351, 352, 353
     2  SIDE-MUNI MUNI_L stamped '' where the side is 'RIVERTON' [blank]
        sample ids: 354, 355
     2  SIDE-MUNI MUNI_R stamped '' where the side is 'RIVERTON' [blank]
        sample ids: 354, 355

rule check: PASS
  SIDE-MUNI: 718 decided side(s) [PASS]
  SIDE-ZIP: 718 decided side(s) [PASS]

sides spanning more than one polygon, split owed:
  OBJECTID 356 SIDE-ZIP: left side spans 2 ZIP boundary polygons (10001, 10003) and is stamped '10001', which is one of them; split the centerline at the boundary
  ...

probe sign check: PASS
  96 segment(s) front different polygons on their two sides and so could disprove the offset direction
  96 of those match the right side, 0 match the left

FAIL: sides disagree with the polygons they front.
```

The same rules over the 240 interior segments of that file, every one of them correctly stamped:

```
$ python sidestamp.py --streets interior.geojson --rules rules.json
segments read: 240
sides compared: 960
  agreement: 960 of 960 decided sides, 100.00%
  conservative misses: 0 blank sides where the layer covers nothing
  ambiguous: 0 side(s) spanning more than one polygon (single sample, run --dense to find these)

findings: 0

rule check: PASS
  SIDE-MUNI: 480 decided side(s) [PASS]
  SIDE-ZIP: 480 decided side(s) [PASS]

probe sign check: INSUFFICIENT
  0 segment(s) front different polygons on their two sides and so could disprove the offset direction
  Nothing in this run could have failed on side direction. A left/right swap would give the same answer.

INSUFFICIENT: no side disagreed, and the run could not have shown one: no segment fronted two different polygons, or a rule decided no side. This is not a clean run.
```

That second run is the point of the tool. Nothing in it is wrong, and it proves nothing. Every
segment carries the same value on both sides, so swapping left for right would print the same
report. It exits 3, not 0.

The probe sign check is global, because the offset direction does not depend on the rule. Whether a
rule reached any side does. This run holds five correctly stamped seam roads, so the sign check
passes. Its second rule names street fields that are not on the file, over a layer that covers none
of the streets, so every side it reads is a conservative miss:

```
$ python sidestamp.py --streets seam_ok.geojson --rules typo_rules.json
segments read: 5
sides compared: 20
  agreement: 10 of 10 decided sides, 100.00%
  conservative misses: 10 blank sides where the layer covers nothing
  ambiguous: 0 side(s) spanning more than one polygon (single sample, run --dense to find these)

findings: 0

rule check: INSUFFICIENT
  SIDE-MUNI: 0 decided side(s) [INSUFFICIENT]
  SIDE-ZIP: 10 decided side(s) [PASS]
  A rule that decided no side would print the same report if its fields were misspelled or its layer missed the streets.

probe sign check: PASS
  5 segment(s) front different polygons on their two sides and so could disprove the offset direction
  5 of those match the right side, 0 match the left

INSUFFICIENT: no side disagreed, and the run could not have shown one: no segment fronted two different polygons, or a rule decided no side. This is not a clean run.
```

One rule that decides sides does not vouch for another that decides none. It exits 3, not 0. When
the layer does cover the streets, the same typo reads every side as blank and fails the run.

| Flag | Default | What it does |
|---|---|---|
| `--streets` | none | GeoJSON of single part LineString centerlines. One of `--streets` or `--points` is required. |
| `--points` | none | GeoJSON of Point address points, checked with point rules. Not allowed with `--streets`. |
| `--rules` | none | JSON file describing the sided checks. Required. |
| `--offset` | `10.0` | Distance stepped perpendicular to reach a probe point, in the units of the data. Roads only: refused with `--points`, even when it equals the default. |
| `--chord` | `1.0` | Chord straddling the sample point used to measure the direction of travel. Roads only. |
| `--dense` | off | Sample nine points along each side instead of one. Roads only. |
| `--id-field` | none | Street or point property to report features by. Default: the feature id, then `OBJECTID`, then the position. |
| `--sample` | `5` | Sample ids carried per rollup group. |
| `--out` | none | Path for the JSON report. |
| `--apply` | off | Write `--out`. Without it nothing is written. |
| `--self-test` | off | Run the offline assertions and exit. |

Exit codes: 0 the run was clean and could have failed, 1 findings at ERROR or a flipped probe,
2 an input could not be read, 3 the run proved nothing, 64 usage error.

An input counts as unreadable when the file is missing, is not JSON, or is JSON that is not the GeoJSON
this tool reads, such as a feature that is null or a position of nulls.

### Points mode

Address points have one stamped value each and no sides. Point rules name a `point_field` instead
of the two side fields. A rules file holds road rules or point rules, never both, and a mismatch
between the rules and `--streets` or `--points` is a usage error.

```
python sidestamp.py --points address_points.geojson --rules point_rules.json
python sidestamp.py --points address_points.geojson --rules point_rules.json --out report.json --apply
```

```json
{
  "checks": [
    {
      "id": "PT-ZIP",
      "label": "ZIP boundary",
      "point_field": "ZIP",
      "polygons": "zips.geojson",
      "polygon_field": "ZIP",
      "placeholder": "99999"
    },
    {
      "id": "PT-MUNI",
      "label": "municipality",
      "point_field": "MUNI",
      "polygons": "muni.geojson",
      "polygon_field": "MUNI",
      "outside_default": "UNINCORPORATED"
    }
  ]
}
```

`id`, `label`, `polygons`, `polygon_field`, `outside_default`, `placeholder`, `enabled`, `severity`,
`placeholder_severity` and `blank_severity` mean the same as in the table above. `point_field` is
required and replaces `left_field` and `right_field`.

A real run over 375 synthetic address points: 40 of them stamped with the neighbouring ZIP, 12 with
the 99999 placeholder, 8 blank, 6 with no location, 4 on a shared boundary and 5 outside the ZIP
layer, stamped with the ZIP of the nearest polygon. The fixture is generated, not real data.

```
$ python sidestamp.py --points address_points.geojson --rules point_rules.json
points read: 375
  6 skipped: no location (null or empty geometry, or a NaN or infinite coordinate)
stamps compared: 738
  agreement: 673 of 738 decided stamps, 91.19%
  conservative misses: 0 blank stamps where the layer covers nothing
  4 stamp(s) on a shared boundary, where either polygon's value is accepted

findings: 65
  ERROR    53
  WARNING  12

groups, largest first:
    40  PT-ZIP ZIP stamped '10003' where the point is '10001' [mismatch]
        sample ids: 1, 2, 3, 4, 5
    12  PT-ZIP ZIP stamped '99999' where the point is '10001' [placeholder]
        sample ids: 41, 42, 43, 44, 45
     8  PT-ZIP ZIP stamped '' where the point is '10001' [blank]
        sample ids: 53, 54, 55, 56, 57
     5  PT-ZIP ZIP stamped '10002' where the point is '(outside all)' [mismatch]
        sample ids: 65, 66, 67, 68, 69

discrimination check: PASS
  PT-MUNI: agreeing points front 2 distinct value(s) (RIVERTON, UNINCORPORATED), 2 needed [PASS]
  PT-ZIP: agreeing points front 3 distinct value(s) (10001, 10002, 10003), 2 needed [PASS]

FAIL: stamps disagree with the polygons the points fall in.
```

The same rules over 240 points that are every one correctly stamped, all in one ZIP and none in the municipality:

```
$ python sidestamp.py --points interior_points.geojson --rules point_rules.json
points read: 240
stamps compared: 480
  agreement: 480 of 480 decided stamps, 100.00%
  conservative misses: 0 blank stamps where the layer covers nothing

findings: 0

discrimination check: INSUFFICIENT
  PT-MUNI: agreeing points front 1 distinct value(s) (UNINCORPORATED), 2 needed [INSUFFICIENT]
  PT-ZIP: agreeing points front 1 distinct value(s) (10001), 2 needed [INSUFFICIENT]
  A rule marked INSUFFICIENT would print the same report for a stamp read from the wrong column.

INSUFFICIENT: no stamp disagreed, and the points could not have shown a wrong column or a wrong layer. This is not a clean run.
```

Nothing in that run is wrong, and it proves nothing. It exits 3, not 0.

The same rules over a layer exported with the column renamed to `ZIPCODE`:

```
$ python sidestamp.py --points renamed_points.geojson --rules point_rules.json
error: rule PT-ZIP: point field 'ZIP' is missing from 3 of 3 point(s), first ids: 1, 2, 3. A point without the field reads as blank, so the whole layer would look unstamped. Fix the field name in the rules or export the column.
```

It exits 2 and writes nothing. Without this assertion the 3 points would read as blank.

## What it checks

- **The value on each side, against the polygon that side fronts.** From the segment's midpoint the
  tool steps `--offset` perpendicular to the direction of travel, left and right, and reads the
  polygon containing each probe point. Left of travel is the counter-clockwise normal, which is the
  handedness NG911 gives LEFT and RIGHT.
- **The direction of travel at the sample point, not across the segment.** The tangent is measured
  over a `--chord` length chord straddling the sample point. A curved or dog-legged centerline then
  gets the normal of the curve at the midpoint rather than the normal of the chord across it.
- **Placeholders, on their own tier.** A `placeholder` value is a known "not assigned" sentinel, not
  a wrong code. It reports at `placeholder_severity`, which defaults to WARNING. Thousands of
  placeholder sides at ERROR would bury the hundreds that genuinely disagree.
- **Blanks, against what the layer means outside itself.** A layer covering only the incorporated
  places has a meaning outside every polygon, and `outside_default` names it. A blank side is then a
  finding. A layer with a ragged edge has no such meaning, so the same blank is silent.
- **The conservative miss, counted apart from both.** A blank stored value where the layer covers
  nothing is neither a pass nor a failure. It is excluded from the agreement rate on both sides of
  the fraction, so a coverage hole cannot move the rate without moving the data.
- **Sides that span more than one polygon.** With `--dense`, nine points are sampled along each
  side. A side fronting two polygons has no single right answer and the centerline should be split
  at the boundary. The report lists these sides and says the split is owed. A stamp naming one of
  the two is accepted, because the stamp describes the side and not the midpoint. A stamp naming
  neither is an ERROR finding, grouped under both polygons.
- **Whether the run could have failed at all.** Only a segment whose two sides front different
  polygons can show that the probe reached the correct side. On every other segment a left for
  right swap gives the same answer. The run reports how many such segments it held, how many of
  them settled the question, and exits 3 when the answer is none.
- **Whether each road rule decided anything.** A rule must decide at least one side, meaning one
  that agreed or became a finding. A rule whose side fields are not on the streets, or whose layer
  covers none of them, decides nothing, and the run exits 3 naming it even when another rule
  settled the sign.
- **The probe's handedness.** Among the segments that settle it, the stored right value should match
  the right probe. When it matches the left more often, the report says FLIPPED and the run fails,
  because every finding in it is on the wrong side. Equal counts are not a pass. As many segments
  say the probe reached the wrong side as say it reached the right one, so the question is open and
  the run exits 3.
- **Address points, by containment.** In points mode the stamped value is compared with the polygon
  the point is in. The same tiers apply: placeholder, blank against `outside_default`, and the
  conservative miss. A point outside every polygon is outside. It is never given the nearest
  polygon's value, so a stamp copied from the nearest ZIP onto a point 1 unit or 4,500 units outside
  the layer is a mismatch.
- **A shared boundary.** A point exactly on the edge between two polygons is in both. Either
  polygon's stamp agrees, the report counts these points, and a third value fails naming both.
- **Whether a points run could have failed.** For each rule, the points that agree must front at
  least two distinct values. A run whose agreeing points all sit in one polygon would print the same
  report for a stamp read from the wrong column, and exits 3. A point that fails does not count as
  evidence, and a placeholder can never agree.
- **The field must be on the points.** A rule's `point_field` has to be a property of every point in
  the file. A missing property exits 2, naming the rule, the field and the first ids. A property
  that is present and null is a real blank.
- **Points with no location.** A null geometry, an empty position, or a NaN or infinite coordinate is
  skipped and counted. A MultiPoint is refused.
- **Groups, not rows.** Findings collapse into the distinct pairs of stored value and containing
  value, sorted by count descending, so one row saying 96 sides fronting ZIP 10003 are stamped
  10001 sits above the one-off boundary segments. Ties are ordered by rule and field, so two runs
  can be diffed.

Run a check for a road crossing a boundary without being split before this one. This tool samples
one point per side by default and assumes the segment does not span a boundary of the layer it is
checked against. An unsplit crossing makes that sample unrepresentative of the whole segment.
`--dense` finds the crossings but does not fix them.

## Why not a spatial join

ArcGIS Pro's Spatial Join and QGIS's Join Attributes by Location relate a feature to the features it
touches, and both are the right tool for a point in a polygon. They are fast, supported, and they
keep the whole join in the tool you already use. For address points, a join and a comparison of the
two columns does what points mode does.

They answer a question a centerline cannot be asked. A line running along a boundary meets both
polygons. Spatial Join offers a one to one join that merges the matches through a field map rule, and
a Largest overlap option. QGIS offers a separate feature for each match, the first match only, or the
largest overlap. None of those is "the polygon on the left". The sided question needs a probe point
on a side, and building that point is what this does.

The second thing missing is the refusal. A report that says "no errors" over a file whose segments
all carry the same value on both sides has tested nothing, and no join reports that. The same holds
for points: a join over 240 points in one polygon agrees with every stamp and cannot tell you the
lookup works. Points mode counts the distinct values the agreeing points front and exits 3 when a
rule has fewer than two.

## See also: auditing an export

Two small failures sit in the step that stamps or exports the points. They share no code with this
tool, and this tool does not audit an export script. It does catch what they leave in the data.

- **A nearest-polygon fallback with no distance cap.** A join that gives each point the value of the
  closest polygon gives a point outside every polygon a value that looks like data. Spatial Join's
  Closest option takes a search radius, and the docs list that radius as optional. They say it limits
  which join features are considered. They do not say what a Closest join does when you leave it
  empty, so check that on your own data. Points mode never falls back: a point outside every polygon
  is outside, and a ZIP copied onto it from the nearest polygon is reported as a mismatch (pinned
  at 1 unit and at 4,500 units outside the layer).
- **Defaults that look like data.** `99999` in a ZIP column and `UNINCORPORATED` in a municipality
  column are valid-looking values that mean "nothing was found". `placeholder` reports the first on
  its own tier, at WARNING, so thousands of them do not bury the real mismatches. `outside_default`
  declares the second and checks it only where a point is outside every polygon. A placeholder
  never counts as evidence that the lookup works. A point outside every polygon that is stamped
  with `outside_default` does count as one value, because it agrees with what the layer says
  outside, and a stamp read from the wrong column would not match it.

To find the unmatched targets in the join itself, keep all targets (Spatial Join's default is an
outer join) and count the rows with no match. The docs give `-1` in `JOIN_FID` as the sign of no
match for a one to many join. Run points mode on the result.

## Limits

- Geometry is treated as plane geometry. The offset, the chord and the ray casting are all
  Cartesian. Run it on data in a projected system in feet or metres. Streets that look like lat/lon
  degrees are refused when the offset is 0.01 or larger, because a 10 degree offset is about 690
  miles.
- Nothing is reprojected. The streets and every polygon layer must already be in one system, and
  nothing checks that they are.
- One sample per side by default, at the midpoint. A segment whose side changes polygon along its
  length is answered from that one point. `--dense` raises it to nine.
- The probe point is `--offset` away from the centerline. A parcel narrower than the offset, a
  divided highway, or a road whose right of way is wider than the offset will put the probe in the
  wrong place. 10 feet clears the pavement of a two-lane road; measure before trusting it on a
  four-lane one.
- MultiLineString features are refused rather than exploded. Their parts have no common midpoint and
  no common direction of travel.
- Zero-length segments are skipped and counted. So are the two sides of an out-and-back spur
  digitized there and back, which folds exactly at its own midpoint and has no direction there.
- `--offset` and `--chord` must be finite numbers greater than zero. A NaN passes a plain
  `greater than zero` test, because every comparison against a NaN is false, and it would then
  probe a point that is inside no polygon anywhere. The run would report every side as inside no
  polygon and read like a measurement. Both flags refuse a NaN and an infinity with exit code 64.
- Everything is read into memory. A county centerline file is fine; a state one is not.
- Points mode is plane geometry too, and nothing is reprojected. It has no offset, so it does not
  refuse lat/lon points. Points and polygons in different systems are not detected: the points land
  outside every polygon and report as mismatches or conservative misses.
- The required-field assertion is points mode only. A road rule whose `left_field` or
  `right_field` is not on the streets reads every side as blank. With default severities that fails
  with exit 1 where the layer covers the streets, and exits 3 through the rule check where it
  covers none of them. With `blank_severity` set to `INFO` the blanks are listed and do not fail
  the run, so the exit code then comes from the sign check. Nothing checks that a polygon layer
  carries its `polygon_field` either; a layer without it reads every polygon as a blank value,
  which shows up loudly as findings.
- Two distinct values is a floor. It shows the lookup can tell two places apart, not that it is
  right in a third. Points in a layer of overlapping polygons accept the stamp of any polygon that
  holds them.
- Coverage is 100% of statements and branches on Windows. Two lines are excluded with
  `# pragma: no cover`: the `if __name__ == "__main__":` guard and the `sys.exit(main())` call under
  it, which only run when the file is launched as a script. The Linux host does not have `coverage`
  installed, so coverage was measured on Windows only; the self-test output on Linux is identical
  text.
- The quoted runs came from generated synthetic fixtures that are not shipped, because the
  repository holds four files. The self-test builds its own fixtures and needs none.
- The self-test counts 390 assertions on every interpreter listed above. It has no
  platform-dependent branch, so the count has no unavoidable difference between hosts.
- The agreement rate describes this run against these layers. It is not a quality score for the
  data, and a high rate with an INSUFFICIENT sign check says only that the run could not fail.
- It never fixes a row. It classifies, groups, reports and exits.
- Stamps are compared as exact text after trimming and upper-casing. A ZIP+4 such as `10001-1234`
  against a 5-digit polygon key, a numeric ZIP that lost its leading zero against a text key, and an
  accent in NFC against the same accent in NFD all report as mismatches, never as agreements.
  Reduce a ZIP+4 stamp to five digits and store identifiers as text before the run.
- A polygon ring with fewer than 3 positions is dropped silently, and a point inside such a polygon
  then reads as outside the layer.
- A polygon feature with a null geometry makes the whole polygon layer unreadable (exit 2), while a
  point with a null geometry is skipped and counted. Clean an empty-geometry row out of a polygon
  layer first.
- A point that lacks the rule's field makes the whole run exit 2, instead of reading as blank.
  Export every point with the column present, even when its value is null.
- Coordinates that all fit inside +-180 and +-90 are treated as degrees, and an `--offset` of 0.01
  or more is refused with exit 64. A small local grid in feet or metres whose coordinates all fall
  inside that envelope exits 64 with the default offset, so shift its origin first.
- Duplicate feature ids are accepted. Findings are reported under the id as given, so two features
  with the same id cannot be told apart in the sample ids.
- With `--apply`, `--out` is written over whatever file it names, including the streets, points,
  rules or polygon file. Nothing checks that it differs from an input. The report is written
  straight to `--out`, not to a temporary file that is renamed, so a write that fails partway can
  leave a short file and the run exits 2.
- The `polygons` path is joined as typed. A rules file written on Windows with a backslash in a
  relative path is not found on Linux, and the run exits 2 naming the path. A rule may also name an
  absolute path or one that contains `..`, and the tool reads it, so run only rules files you trust.
- A stored value outside the console code page is printed with backslash escapes, so the report
  and the exit code survive. The JSON report written with `--apply` keeps the value as given.
- Mutation sweep: 295 sites in the core, 88 in the rule and GeoJSON I/O and 60 in `main` were
  mutated. About 25 survivors are equivalent or display only (counters, truncation limits such as
  `[:20]` and `[:10]`, and `return None` against `return False`). The count shown as `points read`
  and the per-severity counts are not pinned by any assertion. Unpinned boundary mutants are the
  `label or polygon_field` label text, `--sample 1`, the 0.01 geographic offset boundary, a
  3-vertex polygon ring, the rollup `rule` key in the JSON report, the empty-string rule id, and
  `sort_keys` and `indent` in the written report.
- GeoJSON in, JSON out. There is no geodatabase reader and no feature service client. Export first.

## Sources

- GeoJSON, [RFC 7946](https://www.rfc-editor.org/rfc/rfc7946): section 3.2 allows a null geometry for
  an unlocated Feature, section 3.1 lets a processor read an empty `coordinates` array as null, and
  section 3.1.1 puts the first two position elements as easting and northing in that order, with
  elevation optional.
- Python [`json`](https://docs.python.org/3/library/json.html): the module accepts `NaN`,
  `Infinity` and `-Infinity` by default, which is why a NaN coordinate has to be handled.
- Python [`argparse`](https://docs.python.org/3/library/argparse.html): `allow_abbrev=False` stops a
  long option being matched from a prefix, added in Python 3.5.
- Esri, [Spatial Join](https://doc.esri.com/en/arcgis-pro/latest/tool-reference/analysis/spatial-join.html):
  the match options (Intersect, Closest, Largest overlap), the optional search radius, the outer
  join default, and the `-1` `JOIN_FID` value.
- QGIS, [Join attributes by location](https://docs.qgis.org/latest/en/docs/user_manual/processing_algs/qgis/vectorgeneral.html):
  the three join types.

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Related

Other single-file tools in this portfolio that pair with this one:

- [roadmiles](https://github.com/uhsear/roadmiles) - the same centerline file, certified for mileage
- [geocodesift](https://github.com/uhsear/geocodesift) - the addresses built from this attribution
- [nalmatch](https://github.com/uhsear/nalmatch) - the same refusal applied to a parcel join
