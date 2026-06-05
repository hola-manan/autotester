# What this project should do

<One paragraph: the project's job, end to end — what goes in, what comes out.>

## How to run it (optional but helps accuracy)
- Entry point: <the function/endpoint that runs the core process, e.g.
  `package.module:Class.method` or an HTTP route>
- Inputs: <the parameters/fields it takes, with example values>

## Steps worth watching (optional)
<List the key internal functions/stages the tester should trace, if you know
them — e.g. the transform/scoring/aggregation steps.>

## Correctness expectations (the important part)
List the things that must be TRUE about the output. Be specific — these become
the checks. Examples of the *kinds* of expectations to write:
- Value ranges: <e.g. "ph between 0 and 14", "percentages sum to ~100">
- No silent fabrication: <e.g. "if a source falls back to a default, it must be
  disclosed in field X">
- Inputs flow to outputs: <e.g. "fetched solar radiation must affect the result">
- Internal consistency: <e.g. "total = sum of line items", "status matches the
  underlying number">
- No data loss: <e.g. "every input record appears in the output">

## Known tricky areas (optional)
<Anywhere you already suspect bugs or want extra scrutiny.>
