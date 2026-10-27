# Common Parameters and Metrics Implementation Plan

## Background and Context

The user requested all additions identified in the parameter review. Existing preparation, descriptive analysis, EDA, forecast, workspace persistence and exports provide the integration points. This extends those contracts with backward-compatible defaults and uses the existing dependencies.

## Acceptance Criteria

- Numeric time supports seconds, milliseconds and microseconds; naive datetimes use an explicit timezone and are normalized to UTC. Event markers and preflight use the same interpretation.
- Missing-run limits apply before exploratory filling. Butterworth high/low/band-pass filtering exposes order and phase behavior and preserves the physical branch.
- General and EDA windows expose length and step; general windows produce descriptive and spectral summaries. Welch exposes window, overlap, FFT size and detrending, consistently for PSD and coherence.
- Summary tables add sample variance, peak-to-peak, quartiles, unscaled MAD, integrated total/band power, relative band power and spectral centroid.
- EDA windows add SCL variability and slope, SCR amplitude median/maximum, rise/recovery summaries. Events expose baseline correction, response AUC and valid-event nonresponse fractions.
- Forecast intervals expose a confidence level. Optional rolling validation uses explicit train window, horizon and step, causally fills each training fold, and exports fold scores and predictions.
- New controls round-trip through projects/templates and batch execution. All new tables appear in existing table viewers and report exports. Older settings retain defaults.
- Numerical fixtures and integration tests pass, GUI pages are captured and inspected, and the executable is rebuilt and smoke-tested.

## Implementation Details

Extend core.py and experimental.py with validated settings and reusable numerical helpers. Integrate compact advanced cards into existing scrollable pages in app.py. Extend workflow_ui.py parameter collection, restoration, preflight and marker mapping. Existing generic table export code is reused where possible; explicit table enumerations are updated.

No new dependency is required. Durations and frequencies are expressed in seconds and Hz after input normalization. Missing gap duration is the number of missing samples divided by fs. Windows are half-open and include a flagged partial final window. Band integration interpolates PSD endpoints and uses trapezoidal integration. Event AUC integrates the selected corrected clean signal over the half-open sampled response window without bridging missing samples; incomplete events have no AUC. Nonresponse means zero detected qualifying SCRs in a valid event. Rolling folds use only preceding raw measurements and never fill test actuals.

## Verification Strategy

Use known signals, hand-computable distributions, time-unit/timezone equivalence, bounded-gap rejection, filter attenuation, Welch reference calls, band-power conservation, EDA synthetic fixtures, causal rolling forecasts, parameter round trips and export inclusion. Run the existing pytest suite and app smoke test after integration, then capture all pages and advanced settings, build the Windows executable and smoke-test it. Rerun affected checks only after fixes.
