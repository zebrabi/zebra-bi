"""Map one render_visual result (structuredContent.visual) to what the Zebra BI for Office add-ins read.

The mapping follows the Zebra AI app's own PowerPoint and Excel export of the same `visual` shape:
chart settings and hierarchy flattening for Charts, the value-column matrix for Tables, and the
chart-type map.
Settings are partial on purpose: the add-ins merge saved keys over their own defaults.
"""
import copy

# @zebrabi/constants ChartType, mapped as in the Zebra AI app's chart_type_map.
CHART_TYPES = {"waterfall": 0, "area": 1, "bar": 2, "variance": 3, "line": 4, "pin": 7}

# (scenario key in `measure`, ycol field, Office role), in the Zebra AI app's export order for charts.
_CHART_MEASURES = (("Val", "ycol_ac", "Values"), ("FC", "ycol_fc", "Forecast"),
                   ("PL", "ycol_pl", "Plan"), ("PY", "ycol_py", "PreviousYear"))


def is_table(v):
    return v.get("visual_type") == "Table"


def title(v):
    return v.get("title") or ""


# ---------- flat rows (Excel sheet data, both add-ins) ----------

def value_columns(v):
    """tablesOptions.ts getValueColumns: AC, additional measures, PY (or PM), PL, FC; only those with data."""
    m = v.get("measure") or {}
    cols = [{"name": "Val", "displayName": m.get("Val"), "role": "Values", "data": v.get("ycol_ac")}]
    for i, am in enumerate(v.get("additional_measures") or []):
        cols.append({"name": "AdditionalMeasure%d" % (i + 1), "displayName": am.get("name"), "role": "Values", "data": am.get("data")})
    cols += [
        {"name": "PY", "displayName": m.get("PY") or m.get("PM"), "role": "PreviousYear", "data": v.get("ycol_py")},
        {"name": "PL", "displayName": m.get("PL"), "role": "Plan", "data": v.get("ycol_pl")},
        {"name": "FC", "displayName": m.get("FC"), "role": "Forecast", "data": v.get("ycol_fc")},
    ]
    return [c for c in cols if c["data"] and c["displayName"]]


def chart_value_columns(v):
    """The chart add-in's measure order (pptx_builder._edit_webextension_data_charts): AC, FC, PL, PY."""
    m = v.get("measure") or {}
    out = []
    for key, field, role in _CHART_MEASURES:
        if v.get(field) is None:
            continue
        label = m.get(key, "") if key != "PY" else (m.get("PY") or m.get("PM") or "")
        out.append({"name": key, "displayName": label, "role": role, "data": v[field]})
    return out


def flat_rows(v):
    """xlsx_builder._create_table_data: header row, then one row per leaf with the category path and the values.
    Headers are the scenario labels (AC, PY, PL, FC), which is how the add-in assigns roles from a range."""
    cols = value_columns(v) if is_table(v) else chart_value_columns(v)
    names = list(v.get("xcol_name") or []) or [""]
    rows = [names + [c["displayName"] for c in cols]]
    xcol = v.get("xcol")
    if not xcol:
        if cols:
            rows.append([""] + [_total(v, c) for c in cols])
        return rows

    def walk(xnodes, ynodes, path):
        for i, x in enumerate(xnodes):
            p = path + [x.get("name")]
            if x.get("children"):
                walk(x["children"], [(y[i].get("children") if y else None) for y in ynodes], p)
            else:
                rows.append(p + [(y[i]["value"] if y and i < len(y) else None) for y in ynodes])

    walk(xcol, [c["data"] for c in cols], [])
    return rows


def _total(v, col):
    gt = v.get("grand_total") or {}
    key = {"Val": "ac", "PY": "py", "PL": "pl", "FC": "fc"}.get(col["name"])
    if key:
        return gt.get(key) or 0
    idx = int(col["name"].replace("AdditionalMeasure", "")) - 1
    am = gt.get("additional_measures") or []
    return am[idx] if idx < len(am) else 0


# ---------- data labels (both add-ins) ----------
# The add-ins take no number format from the data they are given (the "#,0" below is never read for labels): in
# Excel they read the bound cells again. Their labels follow two stored settings: displayUnits ("P" labels in percent,
# with variances in pp) and decimalPlaces (one value for every value and variance label, 1 when not set). From 1,000
# on, labels are abbreviated (1.1K) with those same decimal places.
_ADDIN_DECIMALS = 1
_ABBREVIATED = 1000
_MAX_DECIMALS = 3


def is_percent(v):
    """True when the result says every displayed measure is a percentage, sent as a fraction (0.352 for 35.2%)."""
    return bool(v.get("default_display_as_percentage"))


def _node_values(nodes):
    """Every value of a ycol, parents (subtotals) included, in a fixed order."""
    out = []
    for n in nodes or []:
        out.append(n.get("value"))
        out += _node_values(n.get("children"))
    return out


def _places(values):
    """The fewest decimal places, up to _MAX_DECIMALS, that show every value exactly; None when more are needed."""
    most = 0
    for x in values:
        for d in range(_MAX_DECIMALS + 1):
            if abs(x * 10 ** d - round(x * 10 ** d)) < 1e-7 * max(1.0, abs(x * 10 ** d)):
                break
        else:
            return None
        most = max(most, d)
    return most


def decimal_places(v, cols):
    """The decimal places the measure needs, read from its values (the result states none): of the percent for a
    percent measure. None when the values need more than _MAX_DECIMALS (a computed ratio or average)."""
    nums = [x for c in cols for x in _node_values(c["data"]) if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return _places([x * 100 for x in nums] if is_percent(v) else nums)


def _labels(v, cols, totals):
    """The numbers the visual labels: every value, the actuals' variance to each comparison, and the totals."""
    num = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0
    lines = [[num(x) for x in _node_values(c["data"])] for c in cols]
    if totals:
        for line, c in zip(lines, cols):
            line.append(num(_total(v, c)))
    ac = next((i for i, c in enumerate(cols) if c["name"] == "Val"), None)
    out = [x for line in lines for x in line]
    if ac is not None:
        for i, c in enumerate(cols):
            if c["name"] in ("PY", "PL", "FC"):
                out += [a - b for a, b in zip(lines[ac], lines[i])]
    return out


def label_settings(v, cols, totals=False):
    """The add-ins' label settings for the measure, only where their defaults would show it wrongly."""
    places = decimal_places(v, cols)
    if is_percent(v):
        s = {"displayUnits": "P"}
        if places is not None and places != _ADDIN_DECIMALS:
            s["decimalPlaces"] = places
        return s
    if places is None or places == _ADDIN_DECIMALS:
        return {}
    if any(abs(x) >= _ABBREVIATED for x in _labels(v, cols, totals)):
        return {}  # fewer or more places would also change the abbreviated labels (1.1K)
    return {"decimalPlaces": places}


def cell_format(v):
    """The Excel number format of the measure's cells in the workbook: percent, and the decimal places the values need
    (all of _MAX_DECIMALS when they need more)."""
    places = decimal_places(v, value_columns(v) if is_table(v) else chart_value_columns(v))
    places = _MAX_DECIMALS if places is None else places
    tail = "." + "0" * places if places else ""
    return "0%s%%" % tail if is_percent(v) else "#,##0" + tail


# ---------- Charts add-in ----------

def chart_settings(v):
    """Partial ChartSettings, as the Zebra AI app writes them on top of chartInstance.getSettings()."""
    cols = chart_value_columns(v)
    s = {"titleText": title(v)}
    if v.get("chart_type") in CHART_TYPES:
        s["chartType"] = CHART_TYPES[v["chart_type"]]
    for i, c in enumerate(cols, start=1):
        s["measure%dRole" % i] = c["role"]
    s["usedMeasuresCount"] = len(cols)
    if len(cols) <= 1:  # one scenario: the variance highlight would hide the category axis
        s["differenceHighlight"] = False
    if v.get("is_inverted"):
        s["invert"] = True
    s.update(label_settings(v, cols))
    return s


def _flatten_values(nodes):
    out = []
    for n in nodes or []:
        if n.get("children"):
            out += _flatten_values(n["children"])
        else:
            out.append(n.get("value"))
    return out


def _flatten_xcol(xcol, levels):
    """pptx_builder._flatten_hierarchical_xcol: one list per level, parents repeated for each leaf."""
    cols = [[] for _ in range(levels)]

    def walk(nodes, path):
        for n in nodes:
            p = path + [n.get("name")]
            if n.get("children"):
                walk(n["children"], p)
            else:
                for i in range(levels):
                    cols[i].append(p[i] if i < len(p) else "")

    walk(xcol or [], [])
    return cols


def chart_dataview(v):
    """The `zebrabi:table-data` DataView for the Charts add-in in PowerPoint."""
    names = list(v.get("xcol_name") or [])
    if v.get("xcol"):
        flat = _flatten_xcol(v["xcol"], len(names))
        categories = [{"source": {"displayName": n}, "values": flat[i]} for i, n in enumerate(names)]
    else:
        categories = [{"source": {"displayName": ""}, "values": [""]}]
    values, value_names, meta = [], [], []
    for c in chart_value_columns(v):
        value_names.append(c["displayName"])
        values.append({"source": {"displayName": c["displayName"]}, "values": _flatten_values(c["data"])})
        meta.append({"displayName": c["displayName"], "roles": {c["role"]: True}, "type": {"numeric": True},
                     "queryName": c["displayName"], "index": len(meta)})
    return {"values": values, "categories": categories, "rowFields": [], "columnFields": [],
            "valueFields": value_names, "rowGrandTotal": False, "columnGrandTotal": False,
            "rowNames": [], "columnNames": [], "valueNames": value_names, "metadata": {"columns": meta}}


# ---------- Tables add-in ----------

_TEXT_TYPE = {"underlyingType": 1, "category": None, "primitiveType": 1, "extendedType": 1, "categoryString": None,
              "text": True, "numeric": False, "integer": False, "bool": False, "dateTime": False, "duration": False,
              "binary": False, "json": False, "none": False}
_NUM_TYPE = {"underlyingType": 259, "category": None, "primitiveType": 3, "extendedType": 259, "categoryString": None,
             "text": False, "numeric": True, "integer": False, "bool": False, "dateTime": False, "duration": False,
             "binary": False, "json": False, "none": False}


def _xcol_object(name, i):
    ref = {"_kind": 2, "source": {"_kind": 0, "entity": name, "kind": 0}, "ref": name, "kind": 2}
    return {"roles": {"Category": True}, "type": dict(_TEXT_TYPE), "displayName": name,
            "queryName": "%s.%s" % (name, name),
            "expr": {"_kind": 2, "source": {"_kind": 0, "entity": name, "variable": "d", "kind": 0}, "ref": name, "kind": 2},
            "rolesIndex": {"Category": [i]}, "index": i, "identityExprs": [ref]}


def _value_object(col, index, is_matrix):
    # The Zebra AI app hardcodes a dollar format here; a neutral thousands format is used instead, so other
    # currencies are not shown as $ (the chart title carries the unit, per IBCS).
    fmt = "#,0"
    name = col["displayName"]
    return {"roles": {col["role"]: True}, "type": dict(_NUM_TYPE), "format": fmt, "displayName": name,
            "queryName": "%s.%s" % (name, name),
            "expr": {"_kind": 3, "source": {"_kind": 0, "entity": name, "variable": "s", "kind": 0}, "ref": name, "kind": 3},
            "rolesIndex": {col["role"]: [0 if is_matrix else index]}, "objects": {"general": {"formatString": fmt}},
            "index": index, "isMeasure": True}


def table_metadata(v):
    """tablesOptions.ts buildMetadataColumns."""
    names = list(v.get("xcol_name") or [])
    cols = [_xcol_object(n, i) for i, n in enumerate(names)] if v.get("xcol") else []
    idx = len(names) if v.get("xcol") else 0
    for c in value_columns(v):
        cols.append(_value_object(c, idx, False))
        idx += 1
    return {"columns": cols}


def _cell(col, ix, value):
    d = {"value": value}
    if col["name"] != "Val":
        d["valueSourceIndex"] = ix
    return d


def _fill_children(xcol, children, cols, level, ancestors):
    """tablesOptions.ts fillChildrenRecursive: a parent carries its subtotal as its first child."""
    for i, x in enumerate(xcol):
        path = ancestors + [{"value": x.get("name"), "levelSourceIndex": level}]
        if x.get("children"):
            sub = {"level": level + 1, "isSubtotal": True,
                   "values": {str(ix): _cell(c, ix, c["data"][i].get("value")) for ix, c in enumerate(cols)}}
            node = {"children": [sub], "value": x.get("name"), "levelValues": path, "level": level}
            children.append(node)
            nxt = [dict(c, data=(c["data"][i].get("children") or [])) for c in cols]
            _fill_children(x["children"], node["children"], nxt, level + 1, path)
        else:
            node = {"value": x.get("name"), "levelValues": path, "level": level}
            if cols:
                node["values"] = {str(ix): _cell(c, ix, c["data"][i].get("value")) for ix, c in enumerate(cols)}
            children.append(node)


def table_matrix(v, grand_total=True):
    """tablesOptions.ts buildMatrix (category labels unformatted: the Zebra AI app's formatXcol only reformats date levels)."""
    names = list(v.get("xcol_name") or [])
    cols = value_columns(v)
    start = len(names)
    sources = [_value_object(c, start + i, True) for i, c in enumerate(cols)]
    children = []
    if v.get("xcol"):
        _fill_children(v["xcol"], children, cols, 0, [])
        if grand_total:
            # Office PowerPoint treats a named "Total" node as a ragged category, so the total is unnamed.
            children.append({"level": 0, "isSubtotal": True,
                             "values": {str(ix): _cell(c, ix, _total(v, c)) for ix, c in enumerate(cols)}})
    else:
        children = [{"level": 0, "values": {str(ix): _cell(c, ix, _total(v, c)) for ix, c in enumerate(cols)}}]
    levels = [{"sources": [_xcol_object(n, i)], "canBeExpanded": i != len(names) - 1} for i, n in enumerate(names)]
    root = {"children": children}
    if names:
        root = {"childIdentityFields": [{"_kind": 2, "source": {"_kind": 0, "entity": n, "kind": 0}, "ref": n, "kind": 2}
                                        for n in names], "children": children}
    return {"rows": {"levels": levels, "root": root},
            "columns": {"levels": [{"sources": copy.deepcopy(sources)}] if sources else [],
                        "root": {"children": [dict({"level": 0}, **({"levelSourceIndex": i} if i else {}))
                                              for i in range(len(sources))]}},
            "valueSources": sources}


def total_is_the_sum(v):
    """True when every column's total in the result is the sum of its rows, give or take the rows' rounding. The Tables
    add-in sums the rows for its total row, in Excel and in PowerPoint; the result's total of a measure that does not
    add up (a ratio, an average) differs from that sum."""
    if not v.get("xcol"):
        return True
    gt = v.get("grand_total") or {}
    for c in value_columns(v):
        key = {"Val": "ac", "PY": "py", "PL": "pl", "FC": "fc"}.get(c["name"])
        if key:
            total = gt.get(key)
        else:
            am = gt.get("additional_measures") or []
            idx = int(c["name"].replace("AdditionalMeasure", "")) - 1
            total = am[idx] if idx < len(am) else None
        leaves = [x for x in _flatten_values(c["data"]) if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if not isinstance(total, (int, float)) or isinstance(total, bool) or not leaves:
            continue
        places = _places(leaves)
        rounding = 0.5 * len(leaves) * 10 ** -places if places is not None else 0
        if abs(total - sum(leaves)) > max(rounding, 1e-9 * max(1.0, abs(total))):
            return False
    return True


def table_settings(v, host):
    """Partial ZBITablesSettings: the keys the Zebra AI app's translateToOldTablesSettingsStructure sets that matter here,
    plus, in Excel, the measure roles xlsx_builder._edit_webextension_data_tables adds."""
    # The add-in sums the rows for the total row, so a total that is not their sum is left out.
    s = {"titleText": title(v), "showTitle": True, "showRowGrandTotal": total_is_the_sum(v)}
    if v.get("is_inverted"):
        s["invert"] = True
    s.update(label_settings(v, value_columns(v), totals=bool(v.get("xcol")) and s["showRowGrandTotal"]))
    if host == "xlsx":
        roles = [c["role"] for c in value_columns(v)]
        for i, r in enumerate(roles, start=1):
            s["measure%dRole" % i] = r
        s["measureRoles"] = roles
        s["usedMeasuresCount"] = len(roles)
        # Without it, Tables migrates ranges with more than four measures as legacy cross-tables.
        s["showCrossTables"] = False
    return s


def table_data(v, host):
    """The `zebrabi:matrix-data` value, with the Zebra AI app's index shift: the add-in counts value columns from 0
    (pptx_builder / xlsx_builder _edit_webextension_data_tables)."""
    settings = table_settings(v, host)
    matrix = table_matrix(v, settings["showRowGrandTotal"])
    meta = table_metadata(v)
    ncat = len(v.get("xcol_name") or []) if v.get("xcol") else 0
    value_fields = [c["displayName"] for c in meta["columns"] if "Category" not in c["roles"]]
    if value_fields and ncat:
        for group in (matrix["columns"]["levels"][0]["sources"] if matrix["columns"]["levels"] else [],
                      matrix["valueSources"], meta["columns"]):
            for c in group:
                if "Category" not in c["roles"]:
                    c["index"] -= ncat
    row_fields = []
    if host == "xlsx":
        meta["measureRoles"] = settings.get("measureRoles", [])
        row_fields = [c["displayName"] for c in meta["columns"] if "Category" in c["roles"]]
    return {"matrix": matrix, "rowFields": row_fields, "columnFields": [], "valueFields": value_fields,
            "rowGrandTotal": host == "pptx", "columnGrandTotal": False,
            "rowNames": [], "columnNames": [], "valueNames": value_fields, "metadata": meta}
