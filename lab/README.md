# Hurricane Explorer 3D — lab

Test copy of Hurricane Explorer 3D (`../hurricane-explorer.html`) for trying new features before they reach the working app.

**Live lab:** https://jsawle.github.io/elnino/lab/

## Testing now (v1.8-lab): official forecast vs ECMWF forecast tracks and weather maps

For a live storm, the new **Forecasts** tab shows ECMWF's forecast tracks alongside the official NHC/JTWC forecast cone from Esri Living Atlas:

- each of ECMWF's 51 ensemble forecasts (thin purple lines), their average (thick dashed) and the main, unperturbed forecast (green dash-dot)
- a forecast-time slider and play button that move a dot along every forecast, past the end of the official forecast
- how many forecasts still show the storm, how spread out they are, and how far their average is from the official forecast (or, for older runs, from where the storm really was)
- a choice of forecast run (about the last four days) and of model: IFS (physics) or AIFS (AI)
- animated ECMWF weather maps under the tracks, following the same forecast time: moisture (water vapour) or rain colours, air pressure lines with L/H centres, and moving wind streaks (from ECMWF's main forecast, 0.25°, every 3 hours to 6 days ahead, newest forecast only)
- a live storm opens at the time ECMWF made its forecast, so the tracks and weather show straight away

## Data

- Official forecasts and tracks: Esri Living Atlas *Active Hurricanes, Cyclones and Typhoons* (NHC and JTWC).
- ECMWF tracks: [ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data) tropical cyclone track files (BUFR), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Tracks adapted from ECMWF tropical cyclone forecasts by ECMWF.

`.github/workflows/ecmwf-tracks.yml` runs `lab/pipeline/ecmwf_tracks.py` every 3 hours. It finds the newest 00 or 12 UTC track files, decodes them with ecCodes, and commits small JSON files to `lab/data/ecmwf/`: an `index.json` plus one folder per run with one file per named storm (about 15–110 KB each), keeping the last 8 runs. Developing systems that have no name yet are counted but not stored. It only commits when there is a new run. ECMWF only publishes track files when there is a storm to track.

The same Action also cuts ECMWF's IFS forecast (10 m wind, rain, water vapour and sea-level pressure, 0–144 h) around each named storm in the newest run (`lab/pipeline/ecmwf_weather.py`). It saves them as PNG frame sheets (a few MB per storm) on the data-only branch `lab-weather`, which is replaced each time so it never builds up history. The page loads them from `raw.githubusercontent.com`.

Test switch: `?wxcanvas=1` hands the map canvases directly instead of PNG images (faster, if the map refreshes them).

Versions for the app, each model and the track decoder are listed in the app's About tab.

Created by Jason Sawle.
