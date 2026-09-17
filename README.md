# sidestamp

Check the ZIP, ESN, city and municipality stamped on each side of a road centerline against the
polygon that side actually fronts, and refuse to call a run clean when no segment in it could have
disagreed.

A road runs north along a ZIP code boundary. The left ZIP field says 34475 and the right ZIP field
says 34475. The west side of that road is in 34482. Every address on it geocodes to a ZIP the
Postal Service does not use for that street, the MSAG range is built from the same attribution, and
the 911 dispatch that reads the centerline sends the call to the wrong area.

Point-in-polygon validation never finds it. A centerline has no single containing polygon. It runs
between them, and it carries a separate attribute for each side. A spatial join relates the line to
the polygons it touches, and the polygon it touches is not the polygon on the left.

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
PASS  a side stamped 34475 fronting 34482 is a mismatch  <-- pinned defect
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
PASS  and it is the left side, fronting 34482, stamped 34475  <-- pinned defect
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
PASS  an ambiguous side produces no finding, right or wrong  <-- pinned defect
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
----------------------------------------------------------------------
261 assertions, 0 failed
```

## Requirements

Python 3.9 or later. Standard library only: `argparse`, `json`, `math`, `os`, `sys`, and
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

A real run against 360 segments, 96 of them stamped with the ZIP on the far side of the road:

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
    96  SIDE-ZIP ZIP_L stamped '34475' where the side is '34482' [mismatch]
        sample ids: 1, 2, 3, 4, 5
    14  SIDE-ZIP ZIP_L stamped '99999' where the side is '34475' [placeholder]
        sample ids: 337, 338, 339, 340, 341
    14  SIDE-ZIP ZIP_R stamped '99999' where the side is '34475' [placeholder]
        sample ids: 337, 338, 339, 340, 341
     3  SIDE-MUNI MUNI_L stamped 'RIVERTON' where the side is 'UNINCORPORATED' [mismatch]
        sample ids: 351, 352, 353
     3  SIDE-MUNI MUNI_R stamped 'RIVERTON' where the side is 'UNINCORPORATED' [mismatch]
        sample ids: 351, 352, 353
     2  SIDE-MUNI MUNI_L stamped '' where the side is 'RIVERTON' [blank]
        sample ids: 354, 355
     2  SIDE-MUNI MUNI_R stamped '' where the side is 'RIVERTON' [blank]
        sample ids: 354, 355

ambiguous sides, no verdict given:
  OBJECTID 356 SIDE-ZIP: left side spans 2 ZIP boundary polygons (34475, 34479) and is stamped '34475', which is one of them; split the centerline at the boundary
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

probe sign check: INSUFFICIENT
  0 segment(s) front different polygons on their two sides and so could disprove the offset direction
  Nothing in this run could have failed on side direction. A left/right swap would give the same answer.

INSUFFICIENT: no side disagreed, and no segment in this run could have disagreed. This is not a clean run.
```

That second run is the point of the tool. Nothing in it is wrong, and it proves nothing. Every
segment carries the same value on both sides, so swapping left for right would print the same
report. It exits 3, not 0.

| Flag | Default | What it does |
|---|---|---|
| `--streets` | none | GeoJSON of single part LineString centerlines. Required. |
| `--rules` | none | JSON file describing the sided checks. Required. |
| `--offset` | `10.0` | Distance stepped perpendicular to reach a probe point, in the units of the data. |
| `--chord` | `1.0` | Chord straddling the sample point used to measure the direction of travel. |
| `--dense` | off | Sample nine points along each side instead of one. |
| `--id-field` | none | Street property to report segments by. Default: the feature id, then `OBJECTID`, then the position. |
| `--sample` | `5` | Sample ids carried per rollup group. |
| `--out` | none | Path for the JSON report. |
| `--apply` | off | Write `--out`. Without it nothing is written. |
| `--self-test` | off | Run the offline assertions and exit. |

Exit codes: 0 the run was clean and could have failed, 1 findings at ERROR or a flipped probe,
2 an input could not be read, 3 the run proved nothing, 64 usage error.

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
  at the boundary. The report says so and gives no verdict. A stamp naming one of the two is still
  accepted, because the stamp describes the side and not the midpoint, but the split is still owed.
- **Whether the run could have failed at all.** Only a segment whose two sides front different
  polygons can show that the probe reached the correct side. On every other segment a left for
  right swap gives the same answer. The run reports how many such segments it held, how many of
  them settled the question, and exits 3 when the answer is none.
- **The probe's handedness.** Among the segments that settle it, the stored right value should match
  the right probe. When it matches the left more often, the report says FLIPPED and the run fails,
  because every finding in it is on the wrong side. Equal counts are not a pass. As many segments
  say the probe reached the wrong side as say it reached the right one, so the question is open and
  the run exits 3.
- **Groups, not rows.** Findings collapse into the distinct pairs of stored value and containing
  value, sorted by count descending, so one row saying 96 sides fronting ZIP 34482 are stamped
  34475 sits above the one-off boundary segments. Ties are ordered by rule and field, so two runs
  can be diffed.

Run a check for a road crossing a boundary without being split before this one. This tool samples
one point per side by default and assumes the segment does not span a boundary of the layer it is
checked against. An unsplit crossing makes that sample unrepresentative of the whole segment.
`--dense` finds the crossings but does not fix them.

## Why not a spatial join

ArcGIS Pro's Spatial Join and QGIS's Join Attributes by Location relate a feature to the features it
touches, and both are the right tool for a point in a polygon. Esri's Data Reviewer ships an
attribute check that compares a feature's value with the polygon containing it, and it is fast,
supported and configurable in the Pro interface.

They answer a question a centerline cannot be asked. A line running along a boundary intersects both
polygons, and a join returns the one it overlaps most, or the first one, or all of them. None of
those is "the polygon on the left". The sided question needs a probe point on a side, and building
that point is what this does.

The second thing missing is the refusal. A report that says "no errors" over a file whose segments
all carry the same value on both sides has tested nothing, and no join reports that.

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
- The agreement rate describes this run against these layers. It is not a quality score for the
  data, and a high rate with an INSUFFICIENT sign check says only that the run could not fail.
- It never fixes a row. It classifies, groups, reports and exits.
- GeoJSON in, JSON out. There is no geodatabase reader and no feature service client. Export first.

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
