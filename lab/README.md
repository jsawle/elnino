# Hurricane Explorer 3D — lab

Test copy of Hurricane Explorer 3D (`../hurricane-explorer.html`) for trying new features before they reach the working app.

**Live lab:** https://jsawle.github.io/elnino/lab/

## Testing now (v1.7-lab): official forecast vs ECMWF forecast tracks

For a live storm, the new **Forecasts** tab shows ECMWF's forecast tracks alongside the official NHC/JTWC forecast cone from Esri Living Atlas:

- each ECMWF ensemble forecast (thin purple lines), their average (thick dashed) and the IFS high-detail run (green dash-dot)
- a forecast-time slider and play button that move a dot along every forecast, past the end of the official forecast
- how many forecasts still show the storm, how spread out they are, and how far their average is from the official forecast (or, for older runs, from where the storm really was)
- a choice of forecast run (about the last four days) and of model: IFS (physics) or AIFS (AI)

## Data

- Official forecasts and tracks: Esri Living Atlas *Active Hurricanes, Cyclones and Typhoons* (NHC and JTWC).
- ECMWF tracks: [ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data) tropical cyclone track files (BUFR), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Tracks adapted from ECMWF tropical cyclone forecasts by ECMWF.

`.github/workflows/ecmwf-tracks.yml` runs `lab/pipeline/ecmwf_tracks.py` every 3 hours. It finds the newest 00 or 12 UTC track files, decodes them with ecCodes, and commits small JSON files to `lab/data/ecmwf/` (an `index.json` plus one file per run, keeping the last 8 runs). It only commits when there is a new run. ECMWF only publishes track files when there is a storm to track.

Versions for the app, each model and the track decoder are listed in the app's About tab.

Created by Jason Sawle.
