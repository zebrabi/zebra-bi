# The five project files the writers don't write

`write_report` and the other writers produce the `definition/pages` tree and `version.json`. They do not write these five files, and Power BI Desktop refuses to open a project, or opens it empty, when one of them is off. When there is no working project to copy, write each one exactly as shown, replacing `<Name>` with the project name. Do not add properties and do not change a version.

## `<Name>.pbip`

When wrong, Desktop says: "Property 'model' has not been defined and the schema does not allow additional properties" (a `model` or `semanticModel` entry in `artifacts`: only `report` entries are allowed), or opens an empty report called Untitled (a guessed `$schema`).

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
  "version": "1.0",
  "artifacts": [ { "report": { "path": "<Name>.Report" } } ],
  "settings": { "enableAutoRecovery": true }
}
```

## `<Name>.Report/definition.pbir`

When wrong, Desktop opens an empty Page 1 or refuses with "Expected '$schema' property ... to follow patterns" (a `$schema` that is not the 2.x `definitionProperties` URL used in the template).

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
  "version": "4.0",
  "datasetReference": { "byPath": { "path": "../<Name>.SemanticModel" } }
}
```

## `<Name>.Report/definition/report.json`

Add the Zebra BI visuals you use to `publicCustomVisuals`. When wrong, Desktop says: "Failed to load the report" (`reportVersionAtImport` written as a string such as `"5.59"` where schema 3.0.0 wants the object, or a numeric `"type": 2`), or "Property /layoutOptimization in report.json was not provided as the correct type" (`layoutOptimization: 0`; leave the property out).

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.0.0/schema.json",
  "themeCollection": {
    "baseTheme": {
      "name": "CY24SU08",
      "reportVersionAtImport": { "visual": "2.11.0", "page": "2.1.0", "report": "3.0.0" },
      "type": "SharedResources"
    }
  },
  "publicCustomVisuals": [ "ZebraBITablesPlus3E6085701D7B426980C3859B16327993" ],
  "settings": { "useStylableVisualContainerHeader": true, "useEnhancedTooltips": true }
}
```

## `<Name>.SemanticModel/definition.pbism`

Version 4.2, because the model is TMDL in a `definition/` folder. When wrong, Desktop says: "Cannot read '...model.bim'. Missing required artifact 'model.bim'" (version `1.0` makes Desktop look for the old model format).

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
  "version": "4.2",
  "settings": {}
}
```

## `<Name>.SemanticModel/definition/model.tmdl`

One `ref table` line per table file in `definition/tables/`. The `ref` lines start at column 0, never indented; only the properties under `model` are indented, with one tab. When wrong, Desktop says: "Unexpected line type: ReferenceObject! Document - './model'" (an indented `ref table` line).

```
model Model
	culture: en-US
	sourceQueryCulture: en-US
	defaultPowerBIDataSourceVersion: powerBI_V3
	discourageImplicitMeasures

	dataAccessOptions
		legacyRedirects
		returnErrorValuesAsNull

ref table <Table>
ref table <OtherTable>
```

## TMDL notes for the table files

- Put a partition's `source =` on its own line and indent the whole expression on the lines after it, one level deeper than `source`. Starting the expression on the `source =` line fails with "TMDL Format Error: Parsing error type - InvalidLineType" at the first row.
- Every value in a DATATABLE row is a constant (a number, text or an ISO date string), never a function call. `DATE(2025,1,1)` in a row makes the calculated table fail on load, and every visual on it is blank.
- Carry a date as an ISO string constant (`"yyyy-mm-dd"`) in a `DATETIME` column, or build it outside the rows with `ADDCOLUMNS ( DATATABLE ( ... ), "Date", DATE ( [Year], [MonthNo], 1 ) )`.
- Indent every line of a calculated table's expression with tabs. DATATABLE rows indented with spaces under a tab-indented `source =` fail with "Invalid indentation was detected!".
- Every column a calculated table declares carries `sourceColumn: [Name]` beside `isNameInferred`; without it Desktop says "Column 'Year' in calculated table 'Financials' is missing the SourceColumn property".
- Run `validate_report` after writing them: the checks for every fault named here live in the tool.
