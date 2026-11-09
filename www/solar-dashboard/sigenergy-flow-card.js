// MODIFIED SOURCE EXPORT: generic assets/configuration; NOT byte-identical to installed build.
// Local Solar dashboard build: layout=33158817 (finance labels and exact derived schedule saving; diagram/layout unchanged).
/*
 * Copyright 2025-2026 Jean-Yves Avenard
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/* Sigenergy Flow Card v52 — standalone Home Assistant Lovelace custom card */

// Scene coordinates match the supplied PNG, without stretching or cropping the house.
// The sky and lawn provide space for the upper and lower power readings.
// Modified source export: identifying artwork removed; see SANITIZATION.md. HACS updates may overwrite this fork.
const HOUSE_IMAGE = '/local/solar-dashboard/assets/placeholder.svg';
const SOLAR_WEATHER_ENTITY = 'sensor.example_solar_weather';
const WEATHER_HOUR = 3600000;
const weatherNumber = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const weatherCode = code => {
    if (!Number.isInteger(code)) return { category: null, label: 'Unknown', icon: 'mdi:weather-cloudy-alert' };
    if ([95, 96, 97, 99].includes(code)) return { category: 'storm', label: 'Thunderstorms', icon: 'mdi:weather-lightning-rainy' };
    if ([71, 73, 75, 77, 85, 86].includes(code)) return { category: 'snow', label: 'Snow', icon: 'mdi:weather-snowy' };
    if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return { category: 'rain', label: 'Rain / drizzle', icon: 'mdi:weather-rainy' };
    if ([45, 48].includes(code)) return { category: 'fog', label: 'Fog', icon: 'mdi:weather-fog' };
    if ([0, 1].includes(code)) return { category: 'clear', label: code === 0 ? 'Clear' : 'Mostly clear', icon: 'mdi:weather-sunny' };
    if ([2, 3].includes(code)) return { category: 'cloudy', label: code === 2 ? 'Partly cloudy' : 'Cloudy', icon: 'mdi:weather-cloudy' };
    return { category: null, label: 'Unknown', icon: 'mdi:weather-cloudy-alert' };
};
// UTC epoch hours avoid ambiguous/missing local hours at DST transitions.
// All forecast fields come from ONE Open-Meteo response; met.no supplies Now only.
const solarWeatherModel = (entity, now = Date.now()) => {
    const result = { rows: [], category: null, label: 'Forecast unavailable', icon: 'mdi:weather-cloudy-alert', probability: null, complete: false, fetched: null, hint: '' };
    if (!entity || ['unknown', 'unavailable'].includes(entity.state)) return result;
    const fetched = Date.parse(entity.state), age = now - fetched;
    result.fetched = Number.isFinite(fetched) ? fetched : null;
    if (!Number.isFinite(fetched) || age < -300000 || age > 90 * 60000) return { ...result, label: 'Forecast stale / unavailable' };
    const hourly = entity.attributes?.hourly, units = entity.attributes?.hourly_units;
    if (!Array.isArray(hourly?.time) || units?.time !== 'unixtime' || units?.temperature_2m !== '°C' || units?.precipitation_probability !== '%' || units?.precipitation !== 'mm') return result;
    const unique = new Map(), duplicates = new Set(), end = now + 4 * WEATHER_HOUR;
    hourly.time.forEach((seconds, index) => {
        if (weatherNumber(seconds) === null) return;
        const start = seconds * 1000;
        if (start % WEATHER_HOUR !== 0) return;
        if (unique.has(start)) duplicates.add(start);
        const probability = weatherNumber(hourly.precipitation_probability?.[index]);
        const temperature = weatherNumber(hourly.temperature_2m?.[index]);
        const precipitation = weatherNumber(hourly.precipitation?.[index]);
        unique.set(start, { start, end: start + WEATHER_HOUR, ...weatherCode(hourly.weather_code?.[index]),
            temperature: temperature !== null && temperature >= -100 && temperature <= 70 ? temperature : null,
            probability: probability !== null && probability >= 0 && probability <= 100 ? probability : null,
            precipitation: precipitation !== null && precipitation >= 0 ? precipitation : null });
    });
    // Open-Meteo precipitation/probability at T describe the PRECEDING hour.
    // Join T+1 precipitation onto T's instantaneous temperature/condition by
    // timestamp, not array order. Never shift an expired probability forward.
    result.rows = [...unique.values()].filter(row => !duplicates.has(row.start) && row.start < end && row.end > now).map(row => {
        const following = !duplicates.has(row.end) ? unique.get(row.end) : null;
        return { ...row, probability: following?.probability ?? null, precipitation: following?.precipitation ?? null };
    }).sort((a, b) => a.start - b.start);
    let covered = now;
    for (const row of result.rows) {
        if (row.start > covered) break;
        covered = Math.max(covered, row.end);
    }
    result.complete = covered >= end;
    if (!result.complete || result.rows.some(row => !row.category)) return { ...result, label: 'Forecast incomplete' };
    if (result.rows.every(row => row.probability !== null)) result.probability = Math.max(...result.rows.map(row => row.probability));
    // A dry majority must not hide impending rain, snow or storms.
    const priority = ['storm', 'snow', 'rain'];
    let representative;
    for (const category of priority) {
        representative = result.rows.find(row => row.category === category || (category === 'rain' && row.precipitation >= .1));
        if (representative) break;
    }
    representative ??= result.rows.find(row => row.category === 'fog' && row.start < now + 2 * WEATHER_HOUR);
    if (!representative) {
        const weight = category => result.rows.filter(row => row.category === category).reduce((sum, row) => sum + Math.min(row.end, end) - Math.max(row.start, now), 0);
        const category = ['clear', 'cloudy', 'fog'].sort((a, b) => weight(b) - weight(a))[0];
        representative = result.rows.find(row => row.category === category);
    }
    Object.assign(result, { category: representative.category, label: representative.label, icon: representative.icon });
    if (representative.precipitation >= .1 && !priority.includes(representative.category)) Object.assign(result, weatherCode(61));
    const hints = [];
    if (result.rows.every(row => row.temperature !== null) && Math.min(...result.rows.map(row => row.temperature)) <= 12) hints.push('Cool forecast — consider a coat');
    if (result.category === 'storm') hints.push('Thunder forecast — plan shelter');
    else if (result.category === 'rain' || (result.probability !== null && result.probability >= 50)) hints.push('Rain possible — take an umbrella');
    else if (result.category === 'snow') hints.push('Snow forecast — warm layers');
    result.hint = hints.join(' · ');
    return result;
};
const solarSunPhase = (entity, now = Date.now()) => {
    const elevation = weatherNumber(entity?.attributes?.elevation);
    const updated = Date.parse(entity?.last_updated), age = now - updated;
    if (elevation === null || elevation < -90 || elevation > 90 || !Number.isFinite(updated) || age < -300000 || age > 30 * 60000 || ['unknown', 'unavailable'].includes(entity?.state)) return null;
    return elevation > 0 ? 'day' : elevation >= -6 ? 'twilight' : 'night';
};
// Photo conditions are CURRENT met.no observations, independent of the forward-4h panel.
const solarPhotoWeather = (entity, now = Date.now()) => {
    const updated = Date.parse(entity?.last_reported ?? entity?.last_updated), age = now - updated;
    const categories = {
        sunny: 'clear', 'clear-night': 'clear', partlycloudy: 'cloudy', cloudy: 'cloudy',
        rainy: 'rain', pouring: 'rain', hail: 'rain', fog: 'fog',
        snowy: 'snow', 'snowy-rainy': 'snow', lightning: 'storm', 'lightning-rainy': 'storm',
    };
    const category = Object.hasOwn(categories, entity?.state) ? categories[entity.state] : null;
    const fresh = Number.isFinite(updated) && age >= -300000 && age <= 90 * 60000;
    if (!category || !fresh) return { category: 'cloudy', tooltip: `Photo: cloudy fallback — current configured weather entity ${!category ? 'condition unknown / unavailable / unsupported' : 'report stale / timestamp unavailable'}; not a forecast or solar-power estimate.` };
    return { category, tooltip: `Photo: current configured weather entity = ${entity.state} (met.no), reported ${new Date(updated).toISOString()}; not the next-4h forecast.` };
};
const PHOTO = { width: 1672, height: 941, top: 0, bottom: 0, bleed: 0 };
// Where the top and bottom fades (which keep the readings legible) end and start.
const FADE = { topEnd: 180, bottomStart: 820 };
const SCENE = { width: PHOTO.width, height: PHOTO.top + PHOTO.height + PHOTO.bottom, keep: [0, PHOTO.width], keepNarrow: [0, PHOTO.width], narrowBelow: 620, scale: 1 };
// One compact cabinet in photo space, immediately left of the real white cabinet.
const CABINET = { x: PHOTO.width * .13, y: PHOTO.height * .36, width: 136 };

// Default entities for each `entities:` key in the card config, in priority order.
// Plant sensors are the primary source (they combine every inverter and battery);
// inverter sensors are the secondary / fallback source, used only when the plant sensor is unavailable.
// A key set in the config replaces its defaults entirely; `false` hides that item.
const DEFAULT_ENTITIES = {
    // No guessed entity ID or home-battery fallback: discover this car's live SOC.
    ev_soc: [],
    // Dedicated Hypervolt telemetry: separate household AC circuit, never DC charging.
    ev_power: ['sensor.example_ev_ev_power'],
    ev_plugged: ['binary_sensor.example_ev_car_plugged'],
    ev_readiness: ['sensor.example_ev_charging_readiness'],
    ev_mode: ['select.example_ev_charge_mode'],
    ev_session_energy: ['sensor.example_ev_session_energy'],
    ev_charging: ['switch.example_ev_charging'],
    pv_power: ['sensor.sigen_plant_pv_power'],
    home_power: ['sensor.sigen_plant_consumed_power'],
    battery_power: ['sensor.sigen_plant_battery_power', 'sensor.sigen_inverter_battery_power'],
    battery_soc: ['sensor.sigen_plant_battery_state_of_charge', 'sensor.sigen_inverter_battery_state_of_charge'],
    battery_capacity: ['sensor.sigen_plant_rated_energy_capacity'],
    grid_import_power: ['sensor.sigen_plant_grid_import_power'],
    grid_export_power: ['sensor.sigen_plant_grid_export_power'],
    phase_mode: ['switch.sigen_plant_independent_phase_power_control'],
    ac_charger_power: ['sensor.sigen_ac_charger_charging_power'],
    ac_charger_state: ['sensor.sigen_ac_charger_system_state'],
    ac_charger_total_energy: ['sensor.sigen_ac_charger_total_energy_consumed'],
    ac_charger_vehicle_soc: [],
    ac_charger_vehicle_range: [],
    dc_charger_power: ['sensor.sigen_inverter_dc_charger_output_power'],
    dc_charger_vehicle_soc: ['sensor.sigen_inverter_dc_charger_vehicle_soc'],
    dc_charger_limit: ['number.sigen_inverter_dc_charger_max_charging_power_limit'],
    dc_charger_lifetime_charged_energy: ['sensor.sigen_plant_total_charged_energy_of_the_evdc'],
    dc_charger_lifetime_discharged_energy: ['sensor.sigen_plant_total_discharged_energy_of_the_evdc'],
    // PV Strings dialog: Total row (Sigenergy PV inputs only) and the third-party PV row.
    pv_strings_power: ['sensor.sigen_plant_sigen_pv_power'],
    pv_strings_lifetime_energy: ['sensor.sigen_plant_total_generation_of_self_pv', 'sensor.sigen_plant_total_pv_generation', 'sensor.sigen_inverter_total_pv_energy'],
    third_party_pv_power: ['sensor.sigen_plant_third_party_pv_power'],
    third_party_pv_lifetime_energy: ['sensor.sigen_plant_total_generation_of_third_party_inverter'],
    daily_pv_energy: ['sensor.sigen_plant_daily_pv_energy', 'sensor.sigen_inverter_daily_pv_energy'],
    daily_grid_import_energy: ['sensor.sigen_plant_daily_grid_import_energy'],
    daily_grid_export_energy: ['sensor.sigen_plant_daily_grid_export_energy'],
    daily_battery_charge_energy: ['sensor.sigen_plant_daily_battery_charge_energy', 'sensor.sigen_inverter_daily_charge_energy'],
    // Energy statistics: lifetime counters, whose change per interval feeds the flow estimate.
    stat_pv: ['sensor.sigen_plant_total_pv_generation'],
    stat_battery_charge: ['sensor.sigen_plant_total_charged_energy_of_the_ess'],
    stat_battery_discharge: ['sensor.sigen_plant_total_discharged_energy_of_the_ess'],
    stat_grid_import: ['sensor.sigen_plant_total_imported_energy'],
    stat_grid_export: ['sensor.sigen_plant_total_exported_energy'],
    stat_evac_charge: ['sensor.sigen_plant_total_charged_energy_of_the_evac', 'sensor.sigen_ac_charger_total_energy_consumed'],
    daily_battery_discharge_energy: ['sensor.sigen_plant_daily_battery_discharge_energy', 'sensor.sigen_inverter_daily_discharge_energy'],
};
const PV_KEYS = ['voltage', 'current', 'power', 'daily_energy', 'accumulated_energy'];
// Today's energy tiles: [value element id, entities key, label].
const DAILY_TILES = [
    ['dayPv', 'daily_pv_energy', 'Solar produced'],
    ['dayImport', 'daily_grid_import_energy', 'Grid imported'],
    ['dayExport', 'daily_grid_export_energy', 'Grid exported'],
    ['dayCharge', 'daily_battery_charge_energy', 'Battery charged'],
    ['dayDischarge', 'daily_battery_discharge_energy', 'Battery discharged'],
];

// Photo-space routes are populated from four separate compact-controller ports.
// Geometry never allocates watts between sources or changes measured directions.
const roundedRoute = (points, radius = 12) => {
    const number = value => Number(value.toFixed(2));
    const point = ([x, y]) => `${number(x)} ${number(y)}`;
    let path = `M${point(points[0])}`;
    for (let i = 1; i < points.length - 1; i++) {
        const [a, b, c] = [points[i - 1], points[i], points[i + 1]];
        const before = Math.hypot(b[0] - a[0], b[1] - a[1]);
        const after = Math.hypot(c[0] - b[0], c[1] - b[1]);
        const r = Math.min(radius, before / 2, after / 2);
        if (!r) { path += ` L${point(b)}`; continue; }
        const entry = b.map((v, n) => v + (a[n] - v) * r / before);
        const exit = b.map((v, n) => v + (c[n] - v) * r / after);
        path += ` L${point(entry)} Q${point(b)} ${point(exit)}`;
    }
    return `${path} L${point(points[points.length - 1])}`;
};
const FLOWS = {
    solarFlow: '', batteryFlow: roundedRoute([[65, 129], [65, 150], [132, 150], [132, 182], [68, 182], [68, 186]], 4), homeFlow: '', gridFlow: '', evFlow: '',
};
// The controller has TOP solar, LEFT grid, RIGHT house and BOTTOM battery ports.
// No shared hub or hidden internal/extra animated segments are drawn.
const cabinetFlowPaths = (port, gridStart, foregroundY, solarLaneY = port(68, -4).y) => {
    const solar = port(68, 8), grid = port(8, 80), house = port(128, 80);
    const forkX = (gridStart.x + port(-2, 80).x) / 2, lane = solarLaneY;
    return {
        solarFlow: roundedRoute([[680, 285], [680, lane], [solar.x, lane], [solar.x, solar.y]]),
        gridFlow: roundedRoute([[gridStart.x, gridStart.y], [gridStart.x, grid.y], [grid.x, grid.y]], Math.min(12, Math.max(0, forkX - gridStart.x - 2))),
        homeFlow: roundedRoute([[house.x, house.y], [Math.max(470, house.x + 32), house.y], [Math.max(470, house.x + 32), 650], [1221, 650], [1221, 527]]),
        // This path lives ABOVE the artwork in the cabinet's own 136x280 SVG,
        // not in photo space. Positive = controller -> battery; negative reverses.
        batteryFlow: FLOWS.batteryFlow,
        // A schematic AC fork upstream of the controller; independently metered EV.
        evFlow: roundedRoute([[forkX, grid.y], [forkX, foregroundY], [1550, foregroundY], [1550, 720], [1470, 720]]),
    };
};
// A branch continues its parent's animation from the point where it leaves the parent's path.
const BRANCHES = {};
const LEADERS = [];
// Reading positions: [x, y, edge], where y is the reading's top or bottom edge. [lower] places the
// SigenStor and GRID readings: the row's left edge, each reading's left edge, and the row's bottom edge.
// [chargers] is the left edge of the charger readings, which are centred vertically: [wide, narrow].
const READINGS = {
    pv: [692, 30, 'top'],
    home: [1233, 539, 'top'],
    lower: { left: 427, columns: [427, 1100], bottom: 915, start: 850 },
    chargers: [1380, 1380],
};

const FLOW_COLOR = '#63d6c1';
const FLOW_DEADBAND_KW = .005;
const batteryVisualState = (soc, power, connected = true) => {
    const known = typeof soc === 'number' && Number.isFinite(soc) && soc >= 0 && soc <= 100;
    const moving = connected && typeof power === 'number' && Number.isFinite(power) && Math.abs(power) >= FLOW_DEADBAND_KW;
    return { known, level: !known || soc === 0 ? 0 : Math.max(1, Math.min(10, Math.floor(soc / 10))),
        mode: moving ? power > 0 ? 'charging' : 'discharging' : 'idle' };
};
// Series colours are CSS variables so the day and night styles can each define their own.
const CHART_SERIES = [
    { key: 'pv_power', color: 'var(--c-solar)', name: 'Solar' },
    { key: 'battery_power', color: 'var(--c-battery)', name: 'Battery' },
    { key: 'grid_import_power', color: 'var(--c-grid)', name: 'Grid' },
    { key: 'grid_export_power', color: 'var(--c-grid)', name: 'Grid', negate: true },
    { key: 'ac_charger_power', color: 'var(--c-ac)', name: 'AC', optional: true },
    { key: 'dc_charger_power', color: 'var(--c-dc)', name: 'DC', optional: true },
    { key: 'home_power', color: 'var(--c-home)', name: 'House (Sigenergy)' },
];
const PV_COLORS = [0, 1, 2, 3, 4, 5].map(i => `var(--pv${i})`);
const STYLES = {
    night: {
        'fg': '#edf3f6', 'sub': '#96a5af', 'card': '#11191d', 'card-line': '#343b44', 'panel': '#222831', 'panel-line': '#35414a',
        'tile': '#2a323d', 'tile-hover': '#323b47', 'tile-sub': '#aabac5', 'heading': '#d5e5e8', 'bar': '#45515c', 'bar-fill': 'linear-gradient(90deg,#49b5ac,#9ee4db)',
        'pill': '#343b46', 'pill-fg': '#b8c3ce', 'legend': '#abb9c2', 'muted': '#8f9ea8', 'icon': '#b3c0c9', 'load-bar': '#3a4550', 'load-fill': '#b6d8db',
        'other-line': '#3f4c56', 'other-fill': '#5b6872', 'tip': 'rgba(17,25,29,.94)', 'tip-line': '#3f4c56', 'tip-shadow': '#0008', 'dialog': '#1a2227', 'dialog-line': '#35414a',
        'grid': '#43505a', 'axis': '#a5b3bf', 'cursor': '#9fb0bb', 'dot-edge': '#11191d', 'scene': '#11191d', 'fade': '#11191d', 'fade-opacity': 1, 'fade-left': .7,
        'node-fg': '#edf3f6', 'node-sub': '#d0dade', 'node-em': '#a5ecd9', 'node-shadow': '0 1px 2px #000,0 2px 10px #000c', 'leader': 'rgba(225,234,236,.55)',
        'wire-on': 'rgba(99,214,193,.85)', 'comet': '#8ff5e1',
        'c-solar': '#ead66c', 'c-battery': '#69d4d1', 'c-grid': '#8d8bff', 'c-ac': '#efa66a', 'c-dc': '#c28aff', 'c-home': '#c7d8df',
        'sk-evdc': '#f0b44c', 'sk-evac': '#d9726a', 'sk-load': '#9a6cf0',
        'pv0': '#6bd36b', 'pv1': '#5fd4dc', 'pv2': '#6a78f0', 'pv3': '#a868f0', 'pv4': '#efa66a', 'pv5': '#ead66c',
    },
    day: {
        'fg': '#1f2733', 'sub': '#6c7a89', 'card': 'linear-gradient(180deg,#d6f4f3,#e9f5fa 45%,#f5f9fc)', 'card-line': '#d3e3ec', 'panel': 'rgba(255,255,255,.8)', 'panel-line': '#e0eaf1',
        'tile': '#edf3f8', 'tile-hover': '#e2ecf4', 'tile-sub': '#6c7a89', 'heading': '#2b3744', 'bar': '#d9e5ed', 'bar-fill': 'linear-gradient(90deg,#1fc2bc,#6fe0d6)',
        'pill': '#e6eef4', 'pill-fg': '#5d6c7b', 'legend': '#5d6c7b', 'muted': '#7a8794', 'icon': '#5d6c7b', 'load-bar': '#d5e1ea', 'load-fill': '#3fb6c6',
        'other-line': '#c3d2dd', 'other-fill': '#9fb0bd', 'tip': 'rgba(255,255,255,.96)', 'tip-line': '#d3e0ea', 'tip-shadow': '#3b536a30', 'dialog': '#f7fafc', 'dialog-line': '#d3e0ea',
        'grid': '#d0dce5', 'axis': '#7a8794', 'cursor': '#7a8794', 'dot-edge': '#fff', 'scene': '#e4f2f7', 'fade': '#0e1b26', 'fade-opacity': .7, 'fade-left': .6,
        'node-fg': '#fff', 'node-sub': '#f2f6f8', 'node-em': '#c4f6ea', 'node-shadow': '0 0 2px #000c,0 1px 3px #000,0 2px 10px #000d', 'leader': 'rgba(255,255,255,.7)',
        'wire-on': 'rgba(18,184,166,.9)', 'comet': '#5eead4',
        'c-solar': '#e2a900', 'c-battery': '#14b3ac', 'c-grid': '#6f6df2', 'c-ac': '#ea8a3c', 'c-dc': '#a35ff0', 'c-home': '#6b7f8c',
        'sk-evdc': '#e39a1b', 'sk-evac': '#d4584f', 'sk-load': '#7f4ee0',
        'pv0': '#3fb83f', 'pv1': '#14b5c0', 'pv2': '#5465e8', 'pv3': '#9b52e8', 'pv4': '#ea8a3c', 'pv5': '#e2a900',
    },
};
const styleVars = style => Object.entries(STYLES[style]).map(([name, value]) => `--${name}:${value}`).join(';');
const COMET_LAYERS = [1, .78, .56, .36, .18];

// Energy statistics: sources feed consumers.
const STAT_SOURCES = [
    { name: 'SOLAR', color: 'var(--c-solar)', key: 'stat_pv', caption: 'Solar' },
    { name: 'BATTERY', color: 'var(--c-battery)', key: 'stat_battery_discharge', caption: 'Battery' },
    { name: 'EVDC', color: 'var(--sk-evdc)', key: 'dc_charger_lifetime_discharged_energy', caption: 'EVDC' },
    { name: 'GRID', color: 'var(--c-grid)', key: 'stat_grid_import', caption: 'Imported' },
];
const STAT_SINKS = [
    { name: 'BATTERY', color: 'var(--c-battery)', key: 'stat_battery_charge', caption: 'Charged' },
    { name: 'EVDC', color: 'var(--sk-evdc)', key: 'dc_charger_lifetime_charged_energy', caption: 'EVDC Charged' },
    { name: 'LOAD', color: 'var(--sk-load)', key: null, caption: 'Load' },
    { name: 'EVAC', color: 'var(--sk-evac)', key: 'stat_evac_charge', caption: 'EVAC Charged' },
    { name: 'GRID', color: 'var(--c-grid)', key: 'stat_grid_export', caption: 'Exported' },
];
// How readily each source (solar, battery, EVDC, grid) feeds each consumer (battery, EVDC, load, EVAC, export); zero
// where a device would feed itself. Fitted so that the split reproduces the flows the Sigenergy app shows.
const STAT_AFFINITY = [
    [0.7921, 1, 0.0609, 0.0208, 0.0303],
    [0, 87.6811, 0.108, 0.0151, 0.0049],
    [0.02, 0, 0.02, 0.02, 0.02],
    [0.0247, 0.0192, 0.1678, 0.0631, 0],
];
const STAT_RANGES = ['Day', 'Week', 'Month', 'Year', 'Lifetime'];
const STAT_PERIODS = { Day: '5minute', Week: 'hour', Month: 'hour', Year: 'day', Lifetime: 'day' };

// Splits one interval's energy into a sources x consumers matrix with those totals (iterative proportional
// fitting from STAT_AFFINITY), so that sums over many intervals keep when each flow happened.
const allocateFlows = (sources, sinks) => {
    const flows = sources.map(() => sinks.map(() => 0));
    const supply = sources.reduce((a, b) => a + b, 0), demand = sinks.reduce((a, b) => a + b, 0);
    if (supply <= 1e-9 || demand <= 1e-9) return flows;
    const wanted = sinks.map(v => v * supply / demand);
    sources.forEach((source, i) => wanted.forEach((sink, j) => { flows[i][j] = source > 0 && sink > 0 ? STAT_AFFINITY[i][j] : 0; }));
    for (let pass = 0; pass < 60; pass++) {
        flows.forEach((row, i) => {
            const total = row.reduce((a, b) => a + b, 0);
            if (total > 0) row.forEach((_, j) => { row[j] *= sources[i] / total; });
        });
        wanted.forEach((sink, j) => {
            const total = flows.reduce((a, row) => a + row[j], 0);
            if (total > 0) flows.forEach(row => { row[j] *= sink / total; });
        });
    }
    return flows;
};

// First and last instant (exclusive) of the range that contains [date].
const statWindow = (range, date) => {
    const y = date.getFullYear(), m = date.getMonth(), d = date.getDate();
    if (range === 'Day') return [new Date(y, m, d), new Date(y, m, d + 1)];
    if (range === 'Week') { const start = d - (date.getDay() + 6) % 7; return [new Date(y, m, start), new Date(y, m, start + 7)]; }
    if (range === 'Month') return [new Date(y, m, 1), new Date(y, m + 1, 1)];
    if (range === 'Year') return [new Date(y, 0, 1), new Date(y + 1, 0, 1)];
    return [new Date(2015, 0, 1), new Date()];
};
const statStep = (range, date, direction) => {
    const y = date.getFullYear(), m = date.getMonth(), d = date.getDate();
    if (range === 'Day') return new Date(y, m, d + direction);
    if (range === 'Week') return new Date(y, m, d + 7 * direction);
    if (range === 'Month') return new Date(y, m + direction, 1);
    return new Date(y + direction, 0, 1);
};
const statLabel = (range, date) => {
    const [start, end] = statWindow(range, date), day = d => d.toLocaleDateString('en-CA');
    if (range === 'Day') return day(start);
    if (range === 'Week') return `${day(start)} – ${day(new Date(end - 1))}`;
    if (range === 'Month') return day(start).slice(0, 7);
    return range === 'Year' ? String(start.getFullYear()) : 'Lifetime';
};

// Power given in kW, shown as W below 1 kW and MW from 1000 kW.
const formatPower = kw => {
    const watts = Math.round(Math.abs(kw) * 1000);
    if (watts < 1000) return `${watts} W`;
    const k = Math.abs(kw);
    return k < 999.995 ? `${k.toFixed(2)} kW` : `${(k / 1000).toFixed(2)} MW`;
};
// SigenStor and GRID share one row so their tops line up whatever each one's number of lines;
// the row hangs from its bottom edge, which keeps the taller block inside the scene.
const lowerStyle = (({ left, columns: [first, second], bottom }) =>
    `left:${(left / SCENE.width * 100).toFixed(3)}%;width:${((SCENE.width - left) / SCENE.width * 100).toFixed(3)}%;bottom:${((SCENE.height - bottom) / SCENE.height * 100).toFixed(3)}%;grid-template-columns:${((second - first) / (SCENE.width - left) * 100).toFixed(3)}% auto`)(READINGS.lower);
const placeAt = ([x, y, edge]) => `left:${(x / SCENE.width * 100).toFixed(3)}%;${edge}:${((edge === 'top' ? y : SCENE.height - y) / SCENE.height * 100).toFixed(3)}%`;

class SigenergyFlowCard extends HTMLElement {
    static getStubConfig() {
        return {
            type: 'custom:sigenergy-flow-card'
        };
    }
    setConfig(config) {
        const entities = config.entities ?? {};
        if (typeof entities !== 'object' || Array.isArray(entities)) throw new Error('entities must be a mapping of key: entity_id');
        for (const [key, value] of Object.entries(entities)) {
            if (!(key in DEFAULT_ENTITIES)) throw new Error(`Unknown entities key "${key}". Valid keys: ${Object.keys(DEFAULT_ENTITIES).join(', ')}`);
            if (value !== false && value !== null && (typeof value !== 'string' || !value.includes('.'))) throw new Error(`entities.${key} must be an entity ID or false`);
        }
        this.config = {
            pv_strings: ['PV1', 'PV2', 'PV3', 'PV4'],
            home_entities: [],
            show_lifetime: true,
            show_phase_mode: false,
            title: 'Sigenergy',
            name: 'My Home',
            show_energy_statistics: true,
            debug_energy_statistics: false,
            ...config
        };
        // Each string is a name, which reads the Sigenergy pattern sensor.sigen_inverter_pv<n>_*, or an object
        // { name, voltage, current, power, daily_energy, accumulated_energy } that uses only the keys it lists.
        // A string none of whose entities exist in Home Assistant is treated as not fitted and hidden.
        this._pv = this.config.pv_strings.map((item, i) => {
            const object = typeof item === 'object' && item !== null;
            const pv = { name: (object ? item.name : item) ?? `PV${i + 1}` };
            for (const key of PV_KEYS) pv[key] = object ? (typeof item[key] === 'string' ? item[key] : null) : `sensor.sigen_inverter_pv${i + 1}_${key}`;
            return pv;
        });
        this._build();
        if (this.isConnected && !this._flowFrame) this._startFlowAnimation();
    }
    set hass(hass) {
        const previous = this._hass;
        this._hass = hass;
        if (!this.root)
            return;
        this._update();
        this._updateFinance();
        this._updateBackupReserve();
        this._updateSunTimes();
        this._updateScene();
        this._updateWeather();
        const today = new Date().toLocaleDateString('en-CA');
        if (!previous || this._day !== today) {
            this._day = today;
            this._loadHistory();
            this._loadStatistics();
        }
    }
    getCardSize() {
        return 12;
    }
    _build() {
        this._stopSigenLayout?.();
        this._sigenPixelOffset = null;
        if (this._flowFrame) { cancelAnimationFrame(this._flowFrame); this._flowFrame = null; }
        if (!this.shadowRoot)
            this.attachShadow({
                mode: 'open'
            });
        const legend = [...new Map(CHART_SERIES.map(s => [s.name, s])).values()]
            .map(s => `<span id="legend${s.name}"><i class="dot" style="background:${s.color}"></i>${s.name}</span>`).join('');
        const comets = id => COMET_LAYERS.map(() => `<path class="comet" d="${FLOWS[id]}"/>`).join('');
        const flowMarkup = id => `<g class="flow" id="${id}"><path class="wire" d="${FLOWS[id]}"/><path class="glow" d="${FLOWS[id]}"/>${comets(id)}</g>`;
        const flows = Object.keys(FLOWS).filter(id => id !== 'batteryFlow').map(flowMarkup).join('');
        this.shadowRoot.innerHTML = `<style>
      :host{display:block;container-type:inline-size;container-name:solar-card;height:auto !important;max-height:none !important;overflow:visible !important;touch-action:pan-y pinch-zoom;color:var(--fg);font-family:Roboto,Arial,sans-serif;${styleVars('night')}}
      [hidden]{display:none !important}
      .backup-reserve{display:grid;grid-template-columns:1fr auto;align-items:center;gap:6px 12px;margin-top:12px;font-size:13px}.backup-reserve select{font:inherit;color:inherit;background:var(--card);border:1px solid var(--card-line);border-radius:6px;padding:5px 8px;min-width:84px}.backup-reserve select:disabled{opacity:.55}.backup-reserve output{grid-column:1/-1;font-size:12px;line-height:1.4;overflow-wrap:anywhere}.backup-reserve output[data-error="true"]{color:#ffb4ab}
      button *{pointer-events:none}
      ha-card{display:block;height:auto !important;max-height:none !important;overflow:visible !important;touch-action:pan-y pinch-zoom;background:var(--card);border:1px solid var(--card-line);border-radius:16px;color:inherit;padding:16px;box-sizing:border-box}
      .header{display:block;margin-bottom:10px}
      .heading{font-size:20px;font-weight:650;letter-spacing:.2px}.sub{font-size:12px;color:var(--sub)}
      .layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,340px);gap:14px;align-items:start;width:100%;max-width:1800px;margin-inline:auto}
      .layout>.visual{grid-column:1;grid-row:1}.layout>.sigen-ai{grid-column:2;grid-row:1}.layout>.finance{grid-column:1/-1;grid-row:2}.layout>.sidebar{grid-column:1/-1;grid-row:3}
      .layout>.visual{width:100%;margin-inline:auto;box-sizing:border-box}
      .finance{width:100%;min-width:0;box-sizing:border-box}.finance h2{font-size:18px;font-weight:500;color:#e0f3fa;margin:8px 0 12px}.finance h2:not(:first-child){margin-top:20px}
      .finance-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
      .finance-tile{display:flex;flex-direction:column;justify-content:space-between;gap:16px;min-width:0;min-height:145px;padding:20px;text-align:left;font:inherit;color:#e0f3fa;background:rgba(8,20,30,.88);border:1px solid rgba(110,190,210,.35);border-radius:12px;cursor:pointer;box-sizing:border-box}
      .finance-tile:hover,.finance-tile:focus-visible{border-color:#8ecde8;background:rgba(16,39,54,.95)}.finance-tile small{display:block;min-width:0;white-space:normal;overflow-wrap:anywhere;font-size:14px;line-height:1.4;color:#b8d9e7}.finance-tile strong{font-size:clamp(21px,2.5vw,38px);font-weight:500;line-height:1.15;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.finance-tile em{font-size:11px;line-height:1.5;color:#b8d9e7;font-style:normal;overflow-wrap:anywhere}
      #financeDetails{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:13px;line-height:1.6;color:#c7e3ef}.finance-tile.pending strong{font-size:clamp(18px,2vw,26px)}
      @container solar-card (max-width:600px){.finance-row{grid-template-columns:1fr}.finance-tile{min-height:125px;padding:16px}.finance-tile strong{font-size:28px}}
      .visual{position:relative;display:grid;grid-template-columns:minmax(0,1fr);align-items:center;justify-self:center;width:100%;box-sizing:border-box;min-width:0;overflow:visible;border-radius:12px;background:var(--scene);padding:8px 0 var(--hero-readout-clearance,118px)}
      .scene{grid-row:1;grid-column:1;min-width:0}
      .grid-rail{position:absolute;left:0;top:0;min-width:min(88px,100%);max-width:100%;z-index:3}
      .grid-meter{position:absolute;left:8px;right:auto;width:max-content;max-width:calc(100% - 16px);box-sizing:border-box;transform:translateY(-50%);padding:4px 0;color:#dce8ed;line-height:1.25;overflow-wrap:anywhere}
      .grid-meter strong{display:block;font-size:17px;font-weight:500;font-variant-numeric:tabular-nums}
      .grid-meter small{display:block;font-size:12px;margin-top:3px}.grid-meter em{display:block;color:#aabdc6;font-size:10px;font-style:normal;margin-top:3px}.grid-meter em:empty{display:none}
      .cabinet-rail{position:absolute;min-width:0;width:136px;isolation:isolate;z-index:3}
      .cabinet-content{position:relative;width:136px;height:280px;transform:scale(var(--cabinet-scale));transform-origin:top left}
      .cabinet-svg{display:block;width:100%;height:auto;overflow:visible}
      .cabinet-svg text{font-family:Roboto,Arial,sans-serif;fill:#b8d9e7}
      .cabinet-mobile-label{display:none}
      .cabinet-reading{position:absolute;left:12px;top:82.5%;width:112px;max-width:112px;box-sizing:border-box;text-align:center;color:#e0f3fa;text-shadow:none;z-index:3;line-height:1.05}
      .cabinet-reading strong{font-size:18px;display:block;font-weight:500;font-variant-numeric:tabular-nums;white-space:normal;overflow-wrap:normal}
      .cabinet-reading small{font-size:12px;display:block;margin-top:1px;color:#b8d9e7}
      .cabinet-reading em{display:block;font-size:11px;font-style:normal;margin-top:1px;color:#b8d9e7;white-space:normal;overflow-wrap:normal}
      .battery-base{filter:grayscale(1) brightness(.55)}
      .battery-step{fill:#102a35;opacity:.7}.battery-step.on{fill:#6edbc4;opacity:.92}
      .battery-shimmer{fill:url(#batterySheen);opacity:0;pointer-events:none}
      #batteryArtwork.charging .battery-shimmer{animation:battery-charge 1.8s linear infinite}
      #batteryArtwork.discharging .battery-shimmer{animation:battery-discharge 1.8s linear infinite}
      @keyframes battery-charge{from{transform:translateX(-78px);opacity:.3}to{transform:translateX(78px);opacity:.1}}
      @keyframes battery-discharge{from{transform:translateX(78px);opacity:.2}to{transform:translateX(-78px);opacity:.08}}
      .car-charge-glow{opacity:0;pointer-events:none}.car-charge-glow.is-charging{animation:car-charge-pulse 3.6s ease-in-out infinite}
      @keyframes car-charge-pulse{0%,100%{opacity:.1875}50%{opacity:.65625}}
      @media(prefers-reduced-motion:reduce){#batteryArtwork.charging .battery-shimmer,#batteryArtwork.discharging .battery-shimmer{animation:none;opacity:0}.car-charge-glow.is-charging{animation:none;opacity:.12}}
      .magnify-leader{fill:none;stroke:#a1adb5;stroke-width:1;stroke-dasharray:4 5;opacity:.65;vector-effect:non-scaling-stroke;pointer-events:none}
      .cabinet-port{fill:#526773;stroke:#9bc7d4;stroke-width:.7}
      .scene{position:relative;overflow:visible;border-radius:12px;background:var(--scene);height:${SCENE.height}px}
      .plane{position:absolute;top:0;left:0;width:${SCENE.width}px;height:${SCENE.height}px;--s:1}
      .plane>svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
      .solar-weather{box-sizing:border-box;color:#eff8ff;background:#102435;border:1px solid #62869c;border-radius:10px;line-height:1.3;text-shadow:none;overflow-wrap:anywhere}
      .weather-sky{position:absolute;left:calc(985px * var(--s));top:calc(8px * var(--s));width:calc(675px * var(--s));padding:calc(8px * var(--s));font-size:calc(18px * var(--s));line-height:1.2;visibility:hidden}
      .weather-column{padding:0 0 12px;margin:0 0 12px;border:0;border-bottom:1px solid #3a5367;border-radius:0;background:none;font-size:12px}
      .weather-head{display:grid;grid-template-columns:auto minmax(0,1fr) minmax(0,1.25fr) auto;align-items:center;gap:.6em}.weather-head>.weather-symbol{--mdc-icon-size:3.1em;color:#c1e8ff}.weather-head strong,.weather-head small{display:block}.weather-head small{font-size:.8em;color:#c4d9e7}.weather-now strong{font-size:1.65em;font-weight:550;white-space:nowrap}.weather-condition strong{font-size:1.08em;font-weight:500}.weather-peak{text-align:right}.weather-peak strong{font-size:1.95em;font-weight:550;line-height:1.1}.weather-peak strong.weather-unknown{font-size:1.3em}.weather-peak small{font-size:.75em;white-space:normal}
      .weather-hours{display:grid;grid-template-columns:repeat(var(--weather-hours,5),minmax(0,1fr));gap:4px;margin:6px 0;padding-top:5px;border-top:1px solid #3a5367}.weather-hour{display:flex;flex-direction:column;align-items:center;text-align:center;min-width:0;font-variant-numeric:tabular-nums}.weather-hour ha-icon{--mdc-icon-size:1.2em;vertical-align:middle}.weather-hour small{font-size:.85em}
      .weather-meta,.weather-hint{font-size:.85em;color:#d3e6f3}.weather-meta{margin-top:3px}.weather-meta a{color:inherit;text-decoration:none}.weather-meta a:hover{text-decoration:underline}
      .weather-sky .weather-hours{margin:4px 0;padding-top:3px}
      .weather-now strong.weather-unknown{font-size:1em;white-space:normal}
      .weather-column .weather-head{grid-template-columns:auto minmax(0,1fr) auto;gap:0 .6em}.weather-column .weather-symbol{grid-column:1;grid-row:1/3}.weather-column .weather-now{grid-column:2;grid-row:1}.weather-column .weather-condition{grid-column:2;grid-row:2}.weather-column .weather-peak{grid-column:3;grid-row:1/3}
      :host([data-weather-placement="sky"]) .weather-sky{visibility:visible}:host([data-weather-placement="sky"]) .weather-column{display:none}
      .fade{stop-color:var(--fade)}.fadeRect{opacity:var(--fade-opacity)}
      .leader{fill:none;stroke:var(--leader);stroke-width:1;vector-effect:non-scaling-stroke}
      .flow{visibility:hidden}.ports-ready .flow{visibility:visible}
      .wire{fill:none;stroke:#53666f;stroke-width:1.7;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round;transition:stroke .4s}
      .flow.active .wire{stroke:var(--wire-on)}
      #batteryFlow{--comet:#79c9df}#evFlow{--comet:#90cbe9}
      #batteryFlow .wire{stroke:#526f7a}
      #batteryFlow.active .wire{stroke:#79c9df}
      .cabinet-svg #batteryFlow{visibility:visible}
      #evFlow .wire{stroke:#516d80}
      #evFlow.active .wire{stroke:#90cbe9}
      .ev-readout{background:rgba(8,20,30,.88);border:1px solid rgba(110,190,210,.35);border-radius:8px;padding:6px 8px;left:auto !important;right:0;width:160px;min-width:0;max-width:100%;box-sizing:border-box;white-space:normal;overflow-wrap:anywhere}
      .ev-readout>*{min-width:0;max-width:100%;box-sizing:border-box;white-space:normal}
      .ev-readout small{font-size:11px}.ev-readout em{font-size:10px}
      .ev-readout .cupra-soc{display:block;font-size:18px;color:var(--primary-text-color,#fff);line-height:1.2;margin-bottom:4px}
      .ev-readout .cupra-wait{display:block;font-size:11px;line-height:1.3;margin-bottom:6px}
      .ev-summary{display:grid;grid-template-columns:1fr auto;gap:8px 12px;font-size:13px;margin-top:10px}
      .ev-summary span{color:var(--muted)}.ev-summary b{font-weight:500;text-align:right}
      .glow,.comet{fill:none;stroke:var(--comet);stroke-linecap:round;stroke-linejoin:round;display:none}
      .glow{stroke-width:4;opacity:.5;vector-effect:non-scaling-stroke}.comet{stroke-width:1.7;opacity:.3;vector-effect:non-scaling-stroke}
      .flow.active.moving .glow,.flow.active.moving .comet{display:inline}
      .node{position:absolute;z-index:2;color:var(--node-fg);white-space:nowrap;line-height:1.2;text-shadow:var(--node-shadow)}
      .grid-endpoint{background:rgba(8,20,30,.88);border:1px solid rgba(110,190,210,.35);border-radius:8px;padding:8px 10px;color:#e0f3fa}
      .grid-endpoint small,.grid-endpoint em{color:#b8d9e7}
      .node strong{display:block;font-size:clamp(14px,calc(27px * var(--s)),30px);font-weight:580}
      .node small,.node em{font-weight:500}
      .node small{display:block;font-size:clamp(11px,calc(19px * var(--s)),20px);color:var(--node-sub);margin-top:.15em;letter-spacing:.3px}
      .node em{display:block;font-size:clamp(10px,calc(13px * var(--s)),14px);font-style:normal;color:var(--node-em);margin-top:.2em}
      .node em:empty{display:none}
      .node em.stack span{display:block}.node em.stack span+span::before{content:none}
      .compact .node em.lifetime:not(.stack) span{display:inline}.compact .node em.lifetime:not(.stack) span+span::before{content:' · '}
      .node em span+span::before{content:' · '}
      .narrow .lead{transform:none;text-align:left}
      .endpoint-readout{width:max-content;min-width:80px;max-width:160px;white-space:normal;text-align:left;visibility:hidden}
      .labels-ready .endpoint-readout{visibility:visible}
      .endpoint-readout strong{font-size:18px;line-height:22px;white-space:nowrap}
      .endpoint-readout small{font-size:12px;line-height:14px;margin-top:1px}
      .endpoint-readout em{font-size:10px;line-height:12px;margin-top:2px}
      .tiny .endpoint-readout strong{font-size:14px;line-height:17px}.tiny .endpoint-readout small{font-size:10px;line-height:12px}
      .tiny #houseReadout em{display:none}
      .endpoint-dot{fill:#b4c5ce;stroke:var(--scene);stroke-width:1;vector-effect:non-scaling-stroke}
      .compact .sidecol .node{max-width:calc(430px * var(--s))}.narrow .sidecol .node{max-width:calc(230px * var(--s))}.compact .sidecol em{white-space:normal}
      .compact .lower .node+.node{max-width:calc(240px * var(--s))}.narrow .lower .node+.node{max-width:calc(150px * var(--s))}.compact .lower em{white-space:normal}
      .compact .node em span{display:block}.compact .node em span+span::before{content:none}
      .sidecol{position:absolute;top:50%;transform:translateY(-50%);z-index:2;display:flex;flex-direction:column;justify-content:space-between;gap:max(14px,calc(24px * var(--s)));min-height:calc(184px * var(--s))}
      .lower{position:absolute;z-index:2;display:grid;align-items:start}.lower .node{position:static}
      .sidecol .node{position:static;border-left:1px solid var(--leader);padding-left:max(6px,calc(10px * var(--s)))}
      .node button{all:unset;cursor:pointer;display:block}.node button:focus-visible{outline:2px solid #71d3d1}
      .sidebar{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px;min-width:0;align-items:start}.panel{min-width:0;background:var(--panel);border:1px solid var(--panel-line);border-radius:12px;padding:13px}
      .sigen-ai{background:rgba(8,20,30,.88);border-color:rgba(110,190,210,.35);color:#e0f3fa}
      .sigen-ai summary{cursor:pointer;font-size:14px;font-weight:600;line-height:1.5}.sigen-ai summary small{display:block;font-size:11px;font-weight:400;color:#b8d9e7}
      .sigen-options{margin:8px 0}.sigen-options>summary{font-size:11px;font-weight:400;color:#b8d9e7}
      .sigen-ai p{font-size:11px;line-height:1.4;color:#b8d9e7;margin:9px 0}
      .sigen-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.sigen-actions button{flex:1 1 140px;min-height:42px;padding:9px 12px;font:inherit;font-size:12px;color:#bce8ff;background:rgba(58,139,185,.14);border:1px solid rgba(110,190,210,.45);border-radius:7px;cursor:pointer}.sigen-actions button:hover,.sigen-actions button:focus-visible{background:rgba(58,139,185,.3);border-color:#8ecde8}
      .sigen-frame-viewport{width:100%;min-width:0;height:clamp(360px,65dvh,540px)}.sigen-ai[data-view="signin"] .sigen-frame-viewport{height:clamp(500px,80dvh,900px)}
      .sigen-ai iframe{display:block;width:100%;height:100%;min-width:0;box-sizing:border-box;border:1px solid rgba(110,190,210,.35);border-radius:8px;background:#08141e}
      .sigen-ai a{display:block;margin-top:10px;padding:8px 10px;border-radius:7px;background:rgba(58,139,185,.14);border:1px solid rgba(110,190,210,.35);color:#bce8ff;text-decoration:none;font-size:12px;text-align:center}.sigen-ai a:hover{background:rgba(58,139,185,.25)}
      .sigen-ai .weather-meta a{display:inline;margin:0;padding:0;background:none;border:0;border-radius:0;font:inherit;color:inherit;text-align:inherit}
      .panel h3{margin:0 0 11px;font-size:14px;font-weight:600;color:var(--heading)}.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
      .stat{all:unset;box-sizing:border-box;background:var(--tile);border-radius:8px;padding:10px;cursor:pointer;transition:background .2s}.stat:hover{background:var(--tile-hover)}.stat:focus-visible{outline:2px solid #71d3d1}.stat small{font-size:11px;color:var(--tile-sub);display:block}.stat b{font-size:18px;display:block;margin-top:4px}
      .link{all:unset;box-sizing:border-box;display:block;width:100%;cursor:pointer;border-radius:6px}.link:hover{filter:brightness(1.2)}.link:focus-visible{outline:2px solid #71d3d1}
      .socbar{height:10px;background:var(--bar);border-radius:8px;overflow:hidden;margin:9px 0}.socfill{height:100%;background:var(--bar-fill);width:0%;transition:width .5s}
      .statpanel{margin-top:14px}.statbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin:10px 0}
      .statnav{display:flex;align-items:center;gap:4px;background:var(--tile);border-radius:10px;padding:3px}.statnav span{min-width:170px;text-align:center;font-size:14px;font-variant-numeric:tabular-nums}
      .statnav button{all:unset;cursor:pointer;width:34px;height:30px;text-align:center;font-size:20px;line-height:30px;border-radius:8px;color:var(--icon)}.statnav button:hover{background:var(--tile-hover)}.statnav button[disabled]{opacity:.3;cursor:default}.statnav button:focus-visible,.statrange:focus-visible{outline:2px solid #71d3d1}
      .statrange{appearance:none;-webkit-appearance:none;background:var(--tile);color:var(--fg);border:1px solid var(--panel-line);border-radius:10px;padding:7px 28px 7px 12px;font:inherit;font-size:14px;cursor:pointer}
      #statChart{width:100%;display:block}.skn{cursor:pointer;transition:opacity .2s}.skn:focus-visible{outline:none}.skn:focus-visible rect.skbox{stroke:#71d3d1;stroke-width:2}.skr{opacity:.5;transition:opacity .2s}.skr.on{opacity:.8}.skr.dim{opacity:.1}.skn.dim{opacity:.3}
      .sktext{font-family:inherit;fill:#142024;pointer-events:none}.skpct{font-family:inherit;font-size:11px;fill:var(--fg);pointer-events:none;paint-order:stroke;stroke:var(--panel);stroke-width:3px}
      .statsum{text-align:center;margin:12px 0 8px;padding:10px;border:1px solid var(--panel-line);border-radius:12px}.statsum b{font-size:24px}.statsum small{font-size:13px;color:var(--sub);margin-left:4px}.statsum div{color:var(--sub);font-size:14px;margin-top:2px}
      .statdata{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-top:8px;font-size:13px}.statdata button{all:unset;cursor:pointer;color:var(--c-battery)}.statdata a{color:var(--c-battery);cursor:pointer}
      .statpanel pre{user-select:text;-webkit-user-select:text;white-space:pre-wrap;font-family:inherit;font-size:12px;margin:10px 0 0}
      .statcards{display:flex;flex-wrap:wrap;gap:9px}.statcard{flex:1 1 140px;box-sizing:border-box;background:var(--tile);border-radius:10px;padding:10px 12px}.statcard b{font-size:18px;display:block}.statcard span{font-size:13px;color:var(--tile-sub);display:block}
      .chartpanel{margin-top:14px}.charthead{display:flex;justify-content:space-between;align-items:center}.legend{font-size:11px;color:var(--legend)}
      .legend span{margin-right:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
      .sun-times{display:flex;justify-content:space-between;gap:12px;margin-top:10px;padding:10px 15px;border-radius:24px;background:var(--pill);color:var(--pill-fg);font-size:12px}
      #chart,#pvChart{width:100%;height:170px;display:block;touch-action:pan-y}.muted{color:var(--muted);font-size:12px}
      .chart-wrap{position:relative}
      .tip{position:absolute;top:4px;z-index:3;pointer-events:none;background:var(--tip);border:1px solid var(--tip-line);border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.5;white-space:nowrap;box-shadow:0 6px 18px var(--tip-shadow);font-variant-numeric:tabular-nums}
      .tip b{display:block;font-size:12px;color:var(--heading);margin-bottom:2px}
      .tip div{display:flex;align-items:center;gap:6px}.tip span{margin-left:auto;padding-left:14px;color:var(--fg)}.tip em{font-style:normal;color:var(--muted)}
      dialog{background:var(--dialog);color:var(--fg);border:1px solid var(--dialog-line);border-radius:16px;padding:0;width:min(760px,calc(100vw - 24px));max-height:calc(100vh - 32px);box-sizing:border-box;font-family:Roboto,Arial,sans-serif}
      dialog::backdrop{background:rgba(0,0,0,.6)}
      .dlg{padding:16px;display:flex;flex-direction:column;gap:14px}
      .dlg-head{display:flex;justify-content:space-between;align-items:center}.dlg-head h2{margin:0;font-size:18px;font-weight:600}
      .dlg-head button{all:unset;cursor:pointer;font-size:20px;line-height:1;padding:4px 8px;border-radius:8px;color:var(--icon)}.dlg-head button:focus-visible{outline:2px solid #71d3d1}
      .pv-wrap{overflow-x:auto}
      .pv-table{width:100%;border-collapse:separate;border-spacing:6px;font-size:14px;font-variant-numeric:tabular-nums}
      .pv-table th{font-size:12px;font-weight:500;color:var(--sub);text-align:center;padding:0 4px}
      .pv-table td{background:var(--tile);border-radius:8px;padding:9px 8px;text-align:center;white-space:nowrap}
      .pv-table th[scope=row]{text-align:left;font-size:13px;color:var(--fg);white-space:nowrap}
      .pv-table tfoot td{background:transparent;color:var(--heading);font-weight:600}
      .pv-table .dot{margin-right:6px}
      .loads{display:flex;flex-direction:column;gap:6px}
      .load{all:unset;box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1fr) auto 3.5em;gap:2px 12px;align-items:center;background:var(--tile);border-radius:8px;padding:9px 12px;cursor:pointer;font-size:14px;font-variant-numeric:tabular-nums}
      .load:focus-visible{outline:2px solid #71d3d1}
      .load .name{display:flex;align-items:center;gap:8px;min-width:0}.load .name span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .load ha-icon{--mdc-icon-size:18px;color:var(--icon);flex:none}
      .load .pct{color:var(--muted);font-size:12px;text-align:right}
      .load .bar{grid-column:1/-1;height:4px;border-radius:2px;background:var(--load-bar);overflow:hidden}.load .bar i{display:block;height:100%;background:var(--load-fill)}
      .load.other{cursor:default;background:transparent;border:1px dashed var(--other-line)}.load.other .bar i{background:var(--other-fill)}
      .home-total{display:flex;justify-content:space-between;align-items:baseline;font-size:14px;color:var(--icon)}.home-total b{font-size:22px;color:var(--fg)}
      @container solar-card (max-width:800px){.layout{grid-template-columns:1fr}.layout>.visual,.layout>.sigen-ai,.layout>.finance,.layout>.sidebar{grid-column:1;grid-row:auto}}
      @container solar-card (max-width:620px){
        ha-card{padding:8px}.visual{padding-top:4px}
        .grid-meter{right:auto}.grid-meter strong{font-size:11px;white-space:normal;overflow-wrap:anywhere}.grid-meter small{font-size:10px;white-space:nowrap}.grid-meter em{display:none}
        .cabinet-svg .controller-title,.cabinet-svg .controller-subtitle,.cabinet-svg .battery-title{visibility:hidden}
        .cabinet-mobile-label{display:block;position:absolute;left:8px;width:120px;max-width:120px;box-sizing:border-box;text-align:center;color:#b8d9e7;font-size:14px;line-height:14px;font-weight:500;pointer-events:none}
        .cabinet-mobile-controller{top:3%}.cabinet-mobile-battery{top:57.5%;white-space:nowrap}
      }
    </style>
    <ha-card><div class="header"><div class="heading" id="heading"></div></div>
      <div class="layout"><div class="visual"><div class="grid-rail" id="gridRail"><div class="grid-meter" id="gridMeter"><strong id="grid">—</strong><small>GRID</small><em>Sigenergy meter</em><em id="gridStatus"></em><em id="phaseMode"></em></div></div><section class="cabinet-rail" aria-label="Compact cabinet detail, explanatory schematic"><div class="cabinet-content">
        <svg class="cabinet-svg" id="cabinetIllustration" viewBox="0 0 136 280" role="img" aria-label="Cabinet detail, schematic: controller and battery storage, four separate power ports">
          <title>Cabinet detail · schematic</title>
          <defs>
            <linearGradient id="cabinetShell" x2="1" y2=".3"><stop stop-color="#08141e" stop-opacity=".88"/><stop offset="1" stop-color="#0c2333" stop-opacity=".88"/></linearGradient>
            <linearGradient id="batterySheen"><stop stop-color="#bcefff" stop-opacity="0"/><stop offset=".5" stop-color="#bcefff" stop-opacity=".9"/><stop offset="1" stop-color="#bcefff" stop-opacity="0"/></linearGradient>
            <clipPath id="batteryBodyClip"><rect x="28" y="190" width="78" height="35" rx="4"/></clipPath>
            <linearGradient id="controllerFace" x2="1" y2=".1"><stop stop-color="#202e36"/><stop offset=".24" stop-color="#354650"/><stop offset=".65" stop-color="#23333c"/><stop offset="1" stop-color="#30424c"/></linearGradient>
            <pattern id="controllerVents" width="4.5" height="20" patternUnits="userSpaceOnUse"><rect width="4.5" height="20" fill="#40545e"/><rect width="2.3" height="20" fill="#101e26"/></pattern>
            <pattern id="controllerLowerVents" width="46" height="3" patternUnits="userSpaceOnUse"><rect width="46" height="1" fill="#40545e"/></pattern>
          </defs>
          <rect x=".5" y=".5" width="135" height="279" rx="12" fill="url(#cabinetShell)" stroke="rgba(110,190,210,.35)"/>
          <rect x="8" y="8" width="120" height="136" rx="7" fill="rgba(8,20,30,.88)" stroke="rgba(110,190,210,.35)"/>
          <text class="controller-title" x="68" y="23" text-anchor="middle" font-size="10.4" font-weight="500">ENERGY CONTROLLER</text>
          <text class="controller-subtitle" x="68" y="37" text-anchor="middle" font-size="8.1">Inverter &amp; power management</text>
          <g aria-label="Dark controller with vents and subtle blue light">
            <path d="M95 55 L108 51 V123 L95 129 Z" fill="#1a2a33" stroke="#425760" stroke-width=".7"/>
            <path d="M98 59 H105 M98 65 H105 M98 71 H105 M98 77 H105 M98 83 H105 M98 89 H105 M98 95 H105 M98 101 H105 M98 107 H105 M98 113 H105" stroke="#334954" stroke-width="1.4"/>
            <rect x="34" y="51" width="62" height="78" rx="6" fill="url(#controllerFace)" stroke="#4b606b" stroke-width=".8"/>
            <rect x="42" y="59" width="46" height="20" rx="2" fill="url(#controllerVents)"/>
            <rect x="42" y="85" width="46" height="2.3" rx="1" fill="#68b4cc"/>
            <rect x="42" y="94" width="46" height="13" rx="2" fill="#20333d" stroke="#425e6b" stroke-width=".7"/>
            <text x="65" y="102.5" text-anchor="middle" font-size="5.5" letter-spacing="1" style="fill:#9cc4d2">ENERGY</text>
            <rect x="42" y="113" width="46" height="7" fill="url(#controllerLowerVents)"/>
            <circle cx="43" cy="124" r="1.4" fill="#8bdac7"/>
          </g>
          <rect x="8" y="162" width="120" height="114" rx="7" fill="rgba(8,20,30,.88)" stroke="rgba(110,190,210,.35)"/>
           <text class="battery-title" x="68" y="170" text-anchor="middle" font-size="10.4" font-weight="500">BATTERY STORAGE</text>
          <g id="batteryArtwork" role="img" aria-labelledby="batteryArtTitle"><title id="batteryArtTitle">Battery SOC unavailable</title>
            <image class="battery-base" href="/local/solar-battery.png" x="22" y="186" width="92" height="43.85" preserveAspectRatio="xMidYMid meet"/>
            <g clip-path="url(#batteryBodyClip)">
              ${Array.from({length:10}, (_, i) => `<rect id="batteryStep${i + 1}" class="battery-step" x="${28 + i * 7.8}" y="190" width="6.8" height="35" rx="1"/>`).join('')}
              <rect class="battery-shimmer" x="28" y="190" width="78" height="35"/>
            </g>
            <text id="batteryUnknown" x="67" y="215" text-anchor="middle" font-size="18" style="fill:#e0f3fa">?</text>
          </g>
          <circle cx="8" cy="80" r="1.8" class="cabinet-port"><title>GRID input/output · Sigenergy meter</title></circle>
          <circle cx="68" cy="8" r="1.8" class="cabinet-port"><title>SOLAR input</title></circle>
          <circle cx="128" cy="80" r="1.8" class="cabinet-port"><title>HOUSE output · Sigenergy-monitored load</title></circle>
           <circle cx="68" cy="144" r="1.8" class="cabinet-port"><title>BATTERY charge/discharge port</title></circle>
           ${flowMarkup('batteryFlow')}
        </svg>
        <span class="cabinet-mobile-label cabinet-mobile-controller" title="Inverter &amp; power management">ENERGY<br>CONTROLLER</span>
        <span class="cabinet-mobile-label cabinet-mobile-battery">BATTERY STORAGE</span>
        <div class="cabinet-reading"><strong id="battery">—</strong><small>SOC <span id="soc">—</span></small><em id="battStatus"></em></div>
      </div></section><section class="scene" id="scene"><div class="plane" id="plane">
        <svg viewBox="0 0 ${SCENE.width} ${SCENE.height}" preserveAspectRatio="none" aria-label="Energy flow between equipment">
          <defs>
            <clipPath id="housePhotoClip"><rect id="housePhotoBounds" x="0" y="0" width="${SCENE.width}" height="${SCENE.height}"/></clipPath>
            <linearGradient id="fadeTop" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="fade" stop-opacity=".6"/><stop offset=".5" class="fade" stop-opacity=".35"/><stop offset="1" class="fade" stop-opacity="0"/></linearGradient>
            <linearGradient id="fadeBottom" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="fade" stop-opacity="0"/><stop offset=".45" class="fade" stop-opacity=".4"/><stop offset="1" class="fade" stop-opacity=".65"/></linearGradient>
            <linearGradient id="fadeLeft" x1="0" y1="0" x2="1" y2="0"><stop offset="0" class="fade" style="stop-opacity:var(--fade-left)"/><stop offset="1" class="fade" stop-opacity="0"/></linearGradient>
            <radialGradient id="cupraChargeHalo"><stop stop-color="#61c9ff" stop-opacity=".9"/><stop offset=".65" stop-color="#328bdb" stop-opacity=".45"/><stop offset="1" stop-color="#328bdb" stop-opacity="0"/></radialGradient>
          </defs>
          <g clip-path="url(#housePhotoClip)">
          <image class="photo" href="${HOUSE_IMAGE}" x="0" y="0" width="${PHOTO.width}" height="${PHOTO.height}" preserveAspectRatio="xMidYMid meet"/>
          <image id="chargerArt" display="none" href="${ARTWORK['charger_day.png'].href}" x="${ARTWORK['charger_day.png'].at[0]}" y="${PHOTO.top + ARTWORK['charger_day.png'].at[1]}" width="${ARTWORK['charger_day.png'].size[0]}" height="${ARTWORK['charger_day.png'].size[1]}" preserveAspectRatio="none"/>
          <rect x="0" y="${-PHOTO.bleed}" width="${PHOTO.width}" height="${PHOTO.bleed + FADE.topEnd}" class="fadeRect" fill="url(#fadeTop)"/>
          <rect x="0" y="${FADE.bottomStart}" width="${PHOTO.width}" height="${SCENE.height - FADE.bottomStart + PHOTO.bleed}" class="fadeRect" fill="url(#fadeBottom)"/>
          <rect x="0" y="${-PHOTO.bleed}" width="520" height="${SCENE.height + 2 * PHOTO.bleed}" fill="url(#fadeLeft)"/>
          </g>
          <ellipse id="cupraCarGlow" class="car-charge-glow" cx="1465" cy="720" rx="148" ry="87" fill="url(#cupraChargeHalo)" aria-hidden="true"/>
          <path class="magnify-leader" id="cabinetMagnifyTop"/>
          <path class="magnify-leader" id="cabinetMagnifyBottom"/>
          <rect x="390" y="543" width="48" height="82" rx="4" class="magnify-leader"/>
          ${LEADERS.map(d => `<path class="leader" data-d="${d}" d="${d}"/>`).join('')}
          <path id="pvEndpointLeader" class="leader"/>
          <path id="houseEndpointLeader" class="leader"/>
          ${flows}
          <circle id="pvEndpointDot" class="endpoint-dot"/>
          <circle id="houseEndpointDot" class="endpoint-dot"/>
          <circle cx="1470" cy="720" r="5" fill="#90cbe9" stroke="var(--scene)" stroke-width="2"/>
          <circle id="evFork" r="3" fill="#648ca5" stroke="var(--scene)" stroke-width="1"><title>AC connection schematic; EV uses a separate meter. Electricity source mix is not measured.</title></circle>
        </svg>
        <aside class="solar-weather weather-sky" aria-label="Local weather and next four hours"></aside>
        <div class="node endpoint-readout" id="pvReadout"><button id="pvButton" title="Show PV string details"><strong id="pv">—</strong><small>SOLAR ›</small></button></div>
        <div class="node endpoint-readout" id="houseReadout">${this.config.home_entities.length
            ? `<button id="homeButton" title="Show Sigenergy-monitored house load"><strong id="home">—</strong><small>HOUSE ›</small></button>`
            : `<strong id="home">—</strong><small>HOUSE</small>`}<em>Sigenergy-monitored load</em></div>
        <div class="node ev-readout" id="evReadout"><small>VEHICLE BATTERY</small><strong class="cupra-soc cupra-wait" id="evSoc">Waiting for vehicle data</strong><strong id="evPower">—</strong><small>VEHICLE ·<br>AC CHARGER</small><em id="evStatus"></em><em id="evSession"></em></div>
        <div class="sidecol" id="chargers">
          <div class="node" id="acNode"><strong id="ac">—</strong><small>AC CHARGER</small><em id="acSoc"></em><em id="acTotal"></em></div>
          <div class="node" id="dcNode"><button id="dcButton" title="Adjust DC charging power limit"><strong id="dc">—</strong><small>DC CHARGER<span id="dcGear"> ⚙</span></small><em id="dcSoc"></em><em id="dcLifetime" class="lifetime stack"></em></button></div>
        </div>
      </div></section></div>
        <section class="panel sigen-ai"><aside class="solar-weather weather-column" aria-label="Local weather and next four hours"></aside><details id="sigenChatDetails"><summary>Sigen AI<small>Official embedded chat</small></summary>
          <details class="sigen-options"><summary>Sign-in options</summary>
          <p>Sign in inside this panel on each device if needed and select your system. Then choose Return to chat. Signing in through Chrome or another browser does not sign in this panel. Home Assistant cannot verify the Sigen session.</p>
          <div class="sigen-actions"><button type="button" id="sigenSignIn">Sign in / reconnect here</button><button type="button" id="sigenReturnChat">Return to chat</button></div>
          </details>
          <div class="sigen-frame-viewport" id="sigenFrameViewport"><iframe id="sigenChatFrame" title="Official Sigen app and AI chat" data-src="https://app-eu.sigencloud.com/#/chat" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-forms allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"></iframe></div>
        </details><a href="https://app-eu.sigencloud.com/" target="_blank" rel="noopener noreferrer">Open Sigen separately ↗</a></section>
      <section class="finance" aria-label="Measured-meter financial estimates">
        ${[['month', 'Month to date'], ['lifetime', 'Recorded period']].map(([period, heading]) => `<h2>${heading}</h2><div class="finance-row">${[['baseline', 'How much it would have cost before solar was installed'], ['saving', 'Actual operation saving — AI mode not assumed throughout'], ['schedule', 'Estimated saving with a timed schedule']].map(([column, label]) => `<button class="finance-tile pending" id="finance_${period}_${column}" data-period="${period}" data-column="${column}" aria-label="${label}: ${column === 'schedule' ? 'estimated saving, not schedule cost; ' : ''}coverage and methodology"><small>${label}</small><strong>Collecting data</strong><em>Tap for evidence gaps and coverage ›</em></button>`).join('')}</div>`).join('')}
      </section><aside class="sidebar">
        <section class="panel"><h3>Today's energy</h3><div class="stats">
          ${DAILY_TILES.map(([id, , label]) => `<button class="stat"><small>${label}</small><b id="${id}">—</b></button>`).join('')}<button class="stat" id="socStat"><small>Battery SOC</small><b id="daySoc">—</b></button>
        </div></section><section class="panel"><h3>Battery level</h3><button class="link" id="socLevel"><strong id="socLarge">—</strong><div class="socbar"><div class="socfill" id="socFill"></div></div></button><button class="link muted" id="capacity" title="Show stored energy today"><span id="capacityText"></span></button><div class="backup-reserve"><label for="backupReserve">Backup reserve</label><select id="backupReserve" aria-describedby="backupReserveStatus" disabled><option value="" disabled>—</option>${[10,25,50,75,100].map(value => `<option value="${value}">${value}%</option>`).join('')}</select><output id="backupReserveStatus" class="muted" aria-live="polite">Waiting for reported reserve</output></div></section>
        <section class="panel" id="evPanel"><h3>Hypervolt</h3><strong id="evPanelPower">—</strong><div class="ev-summary"><span>Connection</span><b id="evPanelPlug">—</b><span>Charging</span><b id="evPanelStatus">—</b><span>Readiness</span><b id="evPanelReady">—</b><span>Charge mode</span><b id="evPanelMode">—</b><span>Session energy</span><b id="evPanelEnergy">—</b></div><p class="muted">House/grid: Sigenergy meter. EV: separate Hypervolt reading. Source mix is not measured.</p></section>
      </aside></div>
      <section class="panel chartpanel"><div class="charthead"><h3>Power metrics · Today</h3><span class="muted" id="chartStatus">Loading history…</span></div>
      <div class="legend">${legend}</div>
      <div class="chart-wrap"><svg id="chart"></svg><div class="tip" id="chartTip" hidden></div></div><div class="sun-times"><span id="sunrise">Sunrise —</span><span id="sunset">Sunset —</span></div></section>
      <section class="panel statpanel" id="statPanel"${this.config.show_energy_statistics ? '' : ' hidden'}><div class="charthead"><h3>Energy statistics</h3><span class="muted" id="statStatus">Loading…</span></div>
      <div class="statbar"><div class="statnav"><button id="statPrev" title="Previous" aria-label="Previous">‹</button><span id="statDate">—</span><button id="statNext" title="Next" aria-label="Next">›</button></div><select class="statrange" id="statRange" aria-label="Range">${STAT_RANGES.map(range => `<option>${range}</option>`).join('')}</select></div>
      <svg id="statChart" role="group" aria-label="Energy flows, estimated"></svg><div class="statsum" id="statSummary" hidden></div><div class="statcards" id="statCards"></div><pre class="muted" id="statDebug" hidden></pre><div class="statdata" id="statData" hidden><button id="statCopy">Copy the recorded data</button><a id="statDownload" download="sigenergy-statistics.json">Download (JSON)</a><span class="muted" id="statCopied"></span></div></section>
    </ha-card>
    <dialog id="financeDialog" aria-labelledby="financeTitle"><div class="dlg"><div class="dlg-head"><h2 id="financeTitle">Financial coverage &amp; methodology</h2><button id="financeClose" aria-label="Close finance details">✕</button></div><div id="financeDetails"></div><a class="link" href="/config/integrations/integration/solar_finance">Finance settings · Activate Flux export / correct tariff dates and rates ↗</a></div></dialog>
    <dialog id="pvDialog" aria-labelledby="pvTitle"><div class="dlg">
      <div class="dlg-head"><h2 id="pvTitle">PV Strings</h2><button id="pvClose" title="Close" aria-label="Close">✕</button></div>
      <section class="panel"><h3>PV real time info</h3><div class="pv-wrap"><table class="pv-table">
        <thead><tr><th></th><th>Voltage<br>(V)</th><th>Current<br>(A)</th><th>Power<br>(kW)</th><th>Today<br>(kWh)</th><th>Lifetime<br>(kWh)</th></tr></thead>
        <tbody>${this._pv.map(({ name }, i) => `<tr id="pvRow${i}"><th scope="row"><i class="dot" style="background:${PV_COLORS[i % PV_COLORS.length]}"></i></th>${['voltage', 'current', 'power', 'daily_energy', 'accumulated_energy'].map(k => `<td id="pv${i}_${k}">—</td>`).join('')}</tr>`).join('')}</tbody>
        <tfoot><tr><th scope="row">Total</th><td></td><td></td><td id="pvTotal_power">—</td><td id="pvTotal_daily_energy">—</td><td id="pvTotal_accumulated_energy">—</td></tr><tr id="pvThirdRow" hidden><th scope="row">Third-party PV</th><td></td><td></td><td id="pvThird_power">—</td><td></td><td id="pvThird_accumulated_energy">—</td></tr></tfoot>
      </table></div></section>
      <section class="panel"><div class="charthead"><h3>PV power (kW) · Today</h3><span class="muted" id="pvChartStatus"></span></div>
      <div class="legend">${this._pv.map(({ name }, i) => `<span id="pvLegend${i}"><i class="dot" style="background:${PV_COLORS[i % PV_COLORS.length]}"></i></span>`).join('')}</div>
      <div class="chart-wrap"><svg id="pvChart"></svg><div class="tip" id="pvChartTip" hidden></div></div></section>
    </div></dialog>
    <dialog id="energyDialog" aria-labelledby="energyTitle"><div class="dlg">
      <div class="dlg-head"><h2 id="energyTitle">Battery energy</h2><button id="energyClose" title="Close" aria-label="Close">✕</button></div>
      <section class="panel"><div class="home-total"><span>Stored now</span><b id="energyNow">—</b></div><div class="muted" id="energyOf"></div></section>
      <section class="panel"><div class="charthead"><h3>Stored energy (kWh) · Today</h3><span class="muted" id="energyChartStatus"></span></div>
      <div class="chart-wrap"><svg id="energyChart"></svg><div class="tip" id="energyChartTip" hidden></div></div></section>
    </div></dialog>
    <dialog id="homeDialog" aria-labelledby="homeTitle"><div class="dlg">
      <div class="dlg-head"><h2 id="homeTitle">Sigenergy-monitored house load</h2><button id="homeClose" title="Close" aria-label="Close">✕</button></div>
      <section class="panel"><div class="home-total"><span>Sigenergy-monitored consumption</span><b id="homeTotal">—</b></div><div class="muted" id="homeMeasured"></div></section>
      <section class="panel"><div class="loads" id="homeLoads"></div></section>
    </div></dialog>`;
        this.root = this.shadowRoot;
        this.root.querySelectorAll('.finance-tile').forEach(tile => tile.addEventListener('click', () => {
            this._financeSelection = { period: tile.dataset.period, column: tile.dataset.column };
            this._updateFinance();
            this._id('financeDialog').showModal();
        }));
        this._id('financeClose').addEventListener('click', () => this._id('financeDialog').close());
        this._wireSigenChat();
        this._wireBackupReserve();
        this._id('heading').textContent = [this.config.title, this.config.name].filter(Boolean).join(' · ');
        // The PV string names come from the card config, so they go in as text rather than through the template.
        this._pv.forEach(({ name }, i) => {
            this._id(`pvRow${i}`).querySelector('th').append(String(name));
            this._id(`pvLegend${i}`).append(String(name));
        });
        this._sceneFile = 'house_day.png';
        this._loadRows = new Map();
        this._loadsEmpty = null;
        this._flowItems = Object.keys(FLOWS).map(id => {
            const group = this._id(id);
            return { id, group, wire: group.querySelector('.wire'), length: 0, glow: group.querySelector('.glow'), comets: [...group.querySelectorAll('.comet')], pos: 0 };
        });
        for (const item of this._flowItems)
            item.parent = this._flowItems.find(other => other.id === BRANCHES[item.id]);
        const dialog = this._id('pvDialog');
        this._id('pvButton').addEventListener('click', () => {
            dialog.showModal();
            this._updatePv();
            this._loadPvHistory();
        });
        this._id('pvClose').addEventListener('click', () => dialog.close());
        const homeDialog = this._id('homeDialog');
        this._id('homeButton')?.addEventListener('click', () => {
            homeDialog.showModal();
            this._updateHome();
        });
        this._id('homeClose').addEventListener('click', () => homeDialog.close());
        // A modal dialog sits above HA's more-info dialog, so it closes first.
        this._id('homeLoads').addEventListener('click', e => {
            const entityId = e.target.closest('[data-entity]')?.dataset.entity;
            if (!entityId) return;
            homeDialog.close();
            this._moreInfo(entityId);
        });
        for (const stat of this.root.querySelectorAll('.stat, #socLevel'))
            stat.addEventListener('click', () => this._moreInfo(stat.dataset.entity));
        const energyDialog = this._id('energyDialog');
        this._id('capacity').addEventListener('click', () => {
            energyDialog.showModal();
            this._updateEnergy();
            this._loadEnergyHistory();
        });
        this._id('energyClose').addEventListener('click', () => energyDialog.close());
        for (const d of [dialog, homeDialog, energyDialog]) d.addEventListener('click', e => { if (e.target === d) d.close(); });
        this._observeCharts();
        for (const [chart, tip] of [['chart', 'chartTip'], ['pvChart', 'pvChartTip'], ['energyChart', 'energyChartTip']]) {
            const svg = this._id(chart), box = this._id(tip);
            const show = e => this._hoverChart(svg, box, e);
            svg.addEventListener('pointermove', show);
            svg.addEventListener('pointerdown', show);
            svg.addEventListener('pointerleave', () => this._hideHover(svg, box));
        }
        this._id('dcButton').addEventListener('click', () => this._moreInfo(this._ent('dc_charger_limit')));
        this._wireStatistics();
        this._installSolarScroll();
        if (this._flowStates) for (const id of Object.keys(this._flowStates)) this._applyFlowClass(id);
    }
    _wireBackupReserve() {
        this._stopBackupReserve();
        const select = this._id('backupReserve');
        const start = () => {
            clearTimeout(this._backupReserveCloseTimer);
            if (select.disabled || this._backupReserveEditing) return;
            this._backupReserveEditing = true;
            // :open, where implemented for native selects, also detects cancel
            // without blur. Only trust a close after observing an actual open.
            if (window.CSS?.supports?.('selector(select:open)')) {
                let seenOpen = false;
                this._backupReservePickerTimer = setInterval(() => {
                    if (select.matches(':open')) seenOpen = true;
                    else if (seenOpen) this._finishBackupReservePicker();
                }, 150);
            }
        };
        const finish = () => this._finishBackupReservePicker();
        // Let a queued change capture the native selection before blur/focus
        // restoration synchronises the reported value (notably on mobile).
        const settle = () => {
            clearTimeout(this._backupReserveCloseTimer);
            this._backupReserveCloseTimer = setTimeout(finish, 0);
        };
        const change = () => {
            const value = select.value;
            finish();
            this._setBackupReserve(value);
        };
        const key = event => {
            if (event.key === 'Escape' || event.key === 'Tab') settle();
            else if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) start();
        };
        // Native pickers can return focus to the already-focused select without
        // a blur/change. Window focus or the next outside interaction releases
        // that session; plain DOM focus is never used as a permanent lock.
        const outside = event => {
            if (!event.composedPath().includes(select)) settle();
        };
        const bindings = [[select, 'focus', start], [select, 'pointerdown', start],
            [select, 'keydown', key], [select, 'blur', settle], [select, 'change', change],
            [window, 'focus', settle], [window, 'pointerdown', outside, true]];
        for (const [node, name, handler, capture] of bindings) node.addEventListener(name, handler, capture);
        this._backupReserveBindings = bindings;
        this._backupReserveRenderKey = null;
        this._updateBackupReserve();
    }
    _finishBackupReservePicker() {
        clearTimeout(this._backupReserveCloseTimer);
        this._backupReserveCloseTimer = null;
        if (!this._backupReserveEditing) return;
        this._backupReserveEditing = false;
        clearInterval(this._backupReservePickerTimer);
        this._backupReservePickerTimer = null;
        this._backupReserveRenderKey = null;
        this._updateBackupReserve();
        if (this._backupReserveFitDeferred) {
            this._backupReserveFitDeferred = false;
            this._queueSceneFit();
        }
    }
    _stopBackupReserve() {
        for (const [node, name, handler, capture] of this._backupReserveBindings || []) node.removeEventListener(name, handler, capture);
        this._backupReserveBindings = null;
        clearTimeout(this._backupReserveCloseTimer);
        this._backupReserveCloseTimer = null;
        clearInterval(this._backupReservePickerTimer);
        this._backupReservePickerTimer = null;
        this._backupReserveEditing = false;
        this._backupReserveFitDeferred = false;
    }
    _backupReserveState() {
        const current = this._hass?.states?.['number.sigen_plant_ess_backup_state_of_charge'];
        const state = this._backupReserveObserved && this._backupReserveObserved.previous === current ? this._backupReserveObserved.state : current;
        const text = state?.state;
        const value = typeof text === 'string' && text.trim() !== '' ? Number(text) : NaN;
        return { state, text, value, valid: Number.isFinite(value) && value >= 0 && value <= 100 };
    }
    _updateBackupReserve() {
        const select = this._id('backupReserve'), status = this._id('backupReserveStatus');
        if (!select || !status) return;
        if (this._backupReserveEditing) return;
        const { text, value, valid } = this._backupReserveState();
        const pending = this._backupReservePending;
        const disabled = !valid || !!pending || typeof this._hass?.callService !== 'function' || typeof this._hass?.callApi !== 'function';
        const key = JSON.stringify([text, valid, pending?.value, this._backupReserveError, disabled]);
        if (this._backupReserveRenderedSelect === select && this._backupReserveRenderKey === key) return;
        this._backupReserveRenderedSelect = select;
        this._backupReserveRenderKey = key;
        // Keep exactly five writable choices. An off-list value is shown verbatim
        // in the disabled placeholder and status, never rounded to a preset.
        const label = valid ? `${text}% (reported)` : '—';
        if (select.options[0].textContent !== label) select.options[0].textContent = label;
        const selected = pending ? String(pending.value) : valid && [10,25,50,75,100].includes(value) ? String(value) : '';
        if (select.value !== selected) select.value = selected;
        if (select.disabled !== disabled) select.disabled = disabled;
        const busy = pending ? 'true' : 'false';
        if (select.getAttribute('aria-busy') !== busy) select.setAttribute('aria-busy', busy);
        const error = this._backupReserveError ? 'true' : 'false';
        if (status.dataset.error !== error) status.dataset.error = error;
        const message = pending
            ? `Pending ${pending.value}% — waiting for device readback. Reported: ${valid ? text + '%' : 'unavailable'}`
            : this._backupReserveError || (valid ? `Reported: ${text}%` : 'Backup reserve unavailable — no write possible');
        if (status.textContent !== message) status.textContent = message;
    }
    async _setBackupReserve(input) {
        const value = Number(input), entityId = 'number.sigen_plant_ess_backup_state_of_charge';
        if (input === '' || ![10,25,50,75,100].includes(value) || this._backupReservePending || !this._backupReserveState().valid) return;
        const pending = { value };
        this._backupReservePending = pending;
        this._backupReserveError = null;
        this._updateBackupReserve();
        try {
            // HA enforces this user's control permission; never assume a service
            // acknowledgement proves a write, or embed credentials in the card.
            await this._hass.callService('number', 'set_value', { value }, { entity_id: entityId });
            for (let attempt = 0; attempt < 10; attempt++) {
                const reported = await this._hass.callApi('GET', `states/${entityId}`);
                if (reported?.entity_id === entityId && typeof reported.state === 'string' && reported.state.trim() !== '' && Number(reported.state) === value) {
                    this._backupReservePending = null;
                    this._backupReserveObserved = { previous: this._hass.states[entityId], state: reported };
                    this._updateBackupReserve();
                    return;
                }
                if (attempt < 9) await new Promise(resolve => setTimeout(resolve, 2000));
            }
            throw new Error('Device readback did not confirm the requested reserve. Check the reported value before retrying.');
        } catch (error) {
            this._backupReserveError = `Reserve not confirmed: ${error?.message || 'Home Assistant rejected the request or readback failed'}`;
        } finally {
            if (this._backupReservePending === pending) this._backupReservePending = null;
            this._updateBackupReserve();
        }
    }
    _wireSigenChat() {
        this._id('sigenChatDetails').addEventListener('toggle', () => {
            // Once bootstrapped, collapse/reopen must not navigate or recreate
            // the cross-origin frame, including while login is in progress.
            if (this._id('sigenChatDetails').open && !this._id('sigenChatFrame').getAttribute('src'))
                this._navigateSigen('chat');
            else this._queueSigenLayout();
        });
        this._id('sigenSignIn').addEventListener('click', () => this._navigateSigen('signin'));
        this._id('sigenReturnChat').addEventListener('click', () => this._navigateSigen('chat'));
        // A load event is layout evidence only, NEVER authentication evidence.
        this._id('sigenChatFrame').addEventListener('load', () => this._queueSigenLayout());
        this._observeSigenFrame();
    }
    _navigateSigen(view) {
        const signin = view === 'signin';
        this.root.querySelector('.sigen-ai').dataset.view = signin ? 'signin' : 'chat';
        this._id('sigenChatDetails').open = true;
        // Official navigation in the SAME frame only. No session inspection,
        // copied credentials, postMessage authentication or automatic redirects.
        this._id('sigenChatFrame').setAttribute('src', signin ? 'https://app-eu.sigencloud.com/' : 'https://app-eu.sigencloud.com/#/chat');
        this._queueSigenLayout();
    }
    _observeSigenFrame() {
        this._stopSigenLayout();
        const queue = this._sigenLayoutHandler = () => this._queueSigenLayout();
        this._sigenResize = new ResizeObserver(queue);
        for (const el of [this._id('sigenFrameViewport'), this.root.querySelector('.sigen-ai'), this.root.querySelector('.layout')])
            this._sigenResize.observe(el);
        window.addEventListener('resize', queue);
        window.addEventListener('scroll', queue, true);
        window.visualViewport?.addEventListener('resize', queue);
        window.visualViewport?.addEventListener('scroll', queue);
        queue();
    }
    _queueSigenLayout() {
        if (this._sigenLayoutFrame) return;
        this._sigenLayoutFrame = requestAnimationFrame(() => {
            this._sigenLayoutFrame = null;
            if (this.isConnected) this._snapSigenFrame();
        });
    }
    _snapSigenFrame() {
        if (!this._id('sigenChatDetails')?.open) return;
        const frame = this._id('sigenChatFrame'), slot = this._id('sigenFrameViewport').getBoundingClientRect();
        if (slot.width <= 0 || slot.height <= 0) return;
        // Integer border-box sizes also give integer inner viewport sizes
        // because the iframe border is 1px. Measure outside cross-origin DOM.
        frame.style.width = `${Math.floor(slot.width)}px`;
        frame.style.height = `${Math.floor(slot.height)}px`;
        const rect = frame.getBoundingClientRect();
        const previous = this._sigenPixelOffset || {x:0, y:0};
        const x = rect.x - previous.x, y = rect.y - previous.y;
        const offset = {x:Math.round(x) - x, y:Math.round(y) - y};
        this._sigenPixelOffset = offset;
        frame.style.transform = `translate(${offset.x}px, ${offset.y}px)`;
    }
    _stopSigenLayout() {
        this._sigenResize?.disconnect();
        this._sigenResize = null;
        if (this._sigenLayoutFrame) cancelAnimationFrame(this._sigenLayoutFrame);
        this._sigenLayoutFrame = null;
        const handler = this._sigenLayoutHandler;
        if (handler) {
            window.removeEventListener('resize', handler);
            window.removeEventListener('scroll', handler, true);
            window.visualViewport?.removeEventListener('resize', handler);
            window.visualViewport?.removeEventListener('scroll', handler);
        }
        this._sigenLayoutHandler = null;
    }
    _financeScheduleSaving(values) {
        // Exact decimal-string subtraction: never subtract rounded displays or
        // binary floating-point GBP. BigInt division truncates towards zero,
        // so only a positive fractional remainder needs +1 for ceiling.
        const parse = raw => {
            if (typeof raw !== 'string' || raw.length > 2100) return null;
            const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(raw.trim());
            if (!match) return null;
            const fraction = match[3] ?? match[4] ?? '';
            const exponent = Number(match[5] || 0);
            if (!Number.isInteger(exponent) || Math.abs(exponent) > 1000) return null;
            let scale = fraction.length - exponent;
            if (scale > 1000) return null;
            let coefficient = BigInt((match[2] || '0') + fraction) * (match[1] === '-' ? -1n : 1n);
            if (scale < 0) { coefficient *= 10n ** BigInt(-scale); scale = 0; }
            return {coefficient, scale};
        };
        const baseline = parse(values?.baseline), scheduleCost = parse(values?.schedule);
        if (!baseline || !scheduleCost) return null;
        const scale = Math.max(baseline.scale, scheduleCost.scale);
        const difference = baseline.coefficient * 10n ** BigInt(scale - baseline.scale) - scheduleCost.coefficient * 10n ** BigInt(scale - scheduleCost.scale);
        const divisor = 10n ** BigInt(scale);
        const whole = difference / divisor + (difference > 0n && difference % divisor !== 0n ? 1n : 0n);
        const absolute = (difference < 0n ? -difference : difference).toString().padStart(scale + 1, '0');
        const fraction = scale ? absolute.slice(-scale).replace(/0+$/, '') : '';
        const value = (difference < 0n ? '-' : '') + (scale ? absolute.slice(0, -scale) : absolute) + (fraction ? '.' + fraction : '');
        const grouped = (whole < 0n ? -whole : whole).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return {value, display: (whole < 0n ? '−' : '') + '£' + grouped};
    }
    _ordinaryFinanceScenario(data, period) {
        if (data?.model_scenario) {
            const endpoints = data.schedule_saving_range;
            if (!data.matched || data.period !== period || !/^[a-f0-9]{64}$/.test(data.coverage_fingerprint || '')
                || !Array.isArray(endpoints) || endpoints.length !== 2 || (data.blockers || []).length) return null;
            const bounds = endpoints.map(value => this._financeScheduleSaving({baseline:value, schedule:'0'}));
            const order = this._financeScheduleSaving({baseline:endpoints[1], schedule:endpoints[0]});
            if (bounds.some(x => !x) || !order || order.value.startsWith('-')) return null;
            return {display:bounds.map(x => x.display).join(' – '), value:endpoints.join(' to '), scenario:data};
        }
        const scenario = data?.ordinary_schedule_scenario;
        if (!scenario?.matched || scenario.scenario_id !== 'cheap-window-self-consumption-v2' || scenario.period !== period
            || !/^[a-f0-9]{64}$/.test(data?.coverage_fingerprint || '')
            || scenario.coverage_fingerprint !== data.coverage_fingerprint || !scenario.values
            || (scenario.blockers || []).length) return null;
        for (const key of ['baseline', 'actual', 'saving']) {
            if (typeof data?.values?.[key] !== 'string' || scenario.matched_parent_values?.[key] !== data.values[key]) return null;
        }
        if (scenario.values.baseline !== data.values.baseline || scenario.values.actual !== data.values.actual) return null;
        for (const key of ['coverage_start', 'coverage_end', 'covered_hours', 'coverage_intervals', 'gap_count', 'segments_truncated']) {
            if (scenario.coverage?.[key] !== data[key]) return null;
        }
        if (JSON.stringify(scenario.coverage?.coverage_segments) !== JSON.stringify(data.coverage_segments)) return null;
        const saving = this._financeScheduleSaving({baseline: scenario.values.baseline, schedule: scenario.values.schedule_cost});
        return saving ? {...saving, scenario} : null;
    }
    _updateFinance() {
        if (!this._id('financeDetails')) return;
        const states = this._hass?.states || {};
        const summary = this.config.finance_entity ? states[this.config.finance_entity]
            : Object.values(states).find(s => s.attributes?.solar_finance_schema === 1 && s.attributes?.periods && !s.attributes?.period);
        const report = summary?.attributes;
        const fresh = this._hass?.connected !== false && this._hass?.connection?.connected !== false && summary && !['unknown', 'unavailable'].includes(summary.state)
            && Number.isFinite(Date.parse(report.last_sample_at)) && Date.now() - Date.parse(report.last_sample_at) < 180000 && Date.now() >= Date.parse(report.last_sample_at);
        const fallback = ['Finance backend is unavailable; check integration status.', 'Financial estimates require eligible measured intervals, dated tariffs and explicitly disclosed model/settings evidence. Unknown evidence is not zero.'];
        const date = value => new Date(value).toLocaleString('en-GB', {timeZone: 'Europe/London', dateStyle: 'medium', timeStyle: 'short'});
        const hours = data => data?.covered_hours != null && data.covered_hours !== '' && Number.isFinite(Number(data.covered_hours)) ? Number(data.covered_hours).toFixed(1) : null;
        const coveredDates = data => {
            const start = new Date(data.coverage_start), end = new Date(data.coverage_end);
            const year = value => value.toLocaleDateString('en-GB', {timeZone:'Europe/London', year:'numeric'});
            return new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/London', day:'numeric', month:'short', ...(year(start) !== year(end) ? {year:'numeric'} : {})}).formatRange(start, end);
        };
        for (const period of ['month', 'lifetime']) for (const column of ['baseline', 'saving', 'schedule']) {
            const tile = this._id(`finance_${period}_${column}`);
            if (!tile) continue;
            // All three cards use the additive corrected common-coverage view.
            // Never fall back to a diagnosed segmented ordinary estimate.
            const data = report?.corrected_view?.periods?.[period];
            const derived = column === 'schedule' ? this._ordinaryFinanceScenario(data, period) : null;
            const display = column === 'schedule' ? derived?.display : data?.display?.[column];
            const valid = fresh && typeof display === 'string' && data.coverage_start && data.coverage_end;
            tile.classList.toggle('pending', !valid);
            tile.querySelector('strong').textContent = valid ? display : 'Collecting data';
            tile.setAttribute('aria-label', `${tile.querySelector('small').textContent}: ${valid ? display : 'Collecting data'}; ${column === 'schedule' ? 'ordinary overnight and self-consumption estimate, not AI gain; ' : ''}covered-period estimate; tap for provenance and limitations`);
            const covered = data?.coverage_start && data?.coverage_end ? `${coveredDates(data)}${hours(data) !== null ? ` · ${hours(data)}h covered` : ''} · Partial` : '';
            tile.querySelector('em').textContent = covered ? `${covered}${column === 'schedule' ? ' · Model range' : ''}${!valid ? ' · Pending' : ''} · Details ›` : 'Tap for evidence gaps and coverage ›';
        }
        const selection = this._financeSelection || {period:'month', column:'baseline'};
        const data = report?.corrected_view?.periods?.[selection.period];
        const blockers = [...(data?.column_blockers?.[selection.column] || data?.blockers || report?.blockers || fallback)];
        if (summary && !fresh) blockers.unshift('Finance telemetry is stale or disconnected; cached financial values are hidden.');
        const scheduleSaving = this._ordinaryFinanceScenario(data, selection.period);
        const scheduleAvailable = Boolean(scheduleSaving);
        const legacyScheduleSaving = this._financeScheduleSaving(data?.values);
        const mode = report?.mode_evidence;
        const reserve = report?.live_settings?.reserve_percent;
        const original = data?.meter_only_accounting;
        const selectedModels = (report?.schedule_model_history || []).filter(model => (data?.calibration || []).some(item => item.input_id?.endsWith(':' + model.id)));
        const valuesText = values => Object.entries(values || {}).map(([key, value]) => `${key}: ${value ?? 'unavailable'} GBP`).join('\n');
        const segmentsText = segments => (segments || []).map(([start, end]) => `${start} → ${end}`).join('\n');
        const settingsText = Object.entries(report?.live_settings || {}).map(([key, observation]) => `${key}: ${observation.status === 'observed' ? `${observation.value}${key === 'reserve_percent' ? '%' : ' kW'}` : observation.status}${observation.raw_value != null ? `; raw ${observation.raw_value}` : ''}; observed ${observation.timestamp || 'unknown'}; ${observation.source || observation.reason || 'no retained evidence'}${observation.register != null ? `; register ${observation.register}` : ''}${observation.applicability ? `; ${observation.applicability}` : ''}`).join('\n');
        const parts = [selection.period === 'month' ? 'Month to date' : 'Recorded period — covered intervals only',
            'FIXED FROZEN AUDIT SNAPSHOT — 4 November 2026; NOT live recalculated figures. Covered window: 28 Oct 13:50–4 Nov 11:16 UTC (2026), identical 9,907 intervals. Baseline £69.723962; actual operation cost £60.942748 / saving £8.781215. Ordinary fixed overnight charging + self-consumption estimated cost £60.801597 / saving £8.922365. Approximately 14p difference on cash only; not inventory-neutral and not proven AI benefit. The old peak-only benchmark saving of £1.868003 was not representative of ordinary self-consumption. One historical terminal SOC is missing; no fabricated inventory credit is included in cash savings. AI advantage not yet established.',
            'The timed-schedule tile is a retrospective fixed-rule cash-saving estimate, NOT proven AI gain. Actual operation is not assumed to be AI throughout. Actual cash accounting is retained; unequal battery inventories and evidence gaps remain qualifications.',
            'Card labels are presentation labels, not a claim that every covered interval was measured under AI. Baseline is a counterfactual cost for the same house demand, not a historic pre-installation bill. Actual saving is baseline minus measured actual cost; retain the recorded mode history and qualifications below.',
            scheduleSaving ? `LIVE HOURLY ORDINARY-SCHEDULE ESTIMATE: ${scheduleSaving.display}. Estimated saving = identical-interval baseline ${data.values.baseline} GBP − ordinary-schedule cost ${scheduleSaving.scenario.values.schedule_cost} GBP = ${scheduleSaving.value} GBP; ceiling applied once, after full-precision subtraction. 02–05 Europe/London overnight charging; self-consumption outside cheap period to observed reserve; no deliberate battery export. Historical replay is a RETROSPECTIVE ESTIMATE, not a backdated registered policy or proven AI advantage. Calculated ${scheduleSaving.scenario.calculated_at}.` : 'Ordinary timed-schedule estimate pending: matched scenario evidence is required. Never substitute the old peak-only policy, frozen audit amount, another period, stale coverage or a smaller favourable interval subset.',
            legacyScheduleSaving ? `SEPARATELY RETAINED REGISTERED PEAK-ONLY POLICY — not the third tile: estimated saving ${legacyScheduleSaving.value} GBP (${legacyScheduleSaving.display}); retained COST ${data.values.schedule} GBP; original policy registration and history unchanged.` : '',
            scheduleSaving ? `Raw cash versus inventory: ${scheduleSaving.scenario.inventory_qualification} Initial simulated energy ${scheduleSaving.scenario.simulated_initial_stored_kwh} kWh; terminal ${scheduleSaving.scenario.simulated_final_stored_kwh} kWh; last actual terminal SOC ${scheduleSaving.scenario.actual_terminal_soc ?? 'unknown'}%. ${scheduleSaving.scenario.soc_resets?.length || 0} conditional SOC resets; final inventory alone does not reconcile earlier segment endpoints.` : '',
            mode?.first_retained_ai_mode_at ? `Actual recorded operation: AI Mode first observed in retained Recorder evidence at ${mode.first_retained_ai_mode_at} (${date(mode.first_retained_ai_mode_at)}, Europe/London). Current recorded mode: ${mode.current_recorded_mode || 'unknown'}. ${mode.qualification || 'First retained observation is not proof of initial activation or continuous AI operation.'} Source: ${mode.source || 'not supplied'}.` : report?.operation_label || 'Actual recorded operation: no retained AI-mode provenance supplied by this report.',
            data?.coverage_start && data?.coverage_end ? `${scheduleSaving ? 'The retained baseline, actual cash and historical schedule estimate use identical eligible covered segments; the ordinary timed-schedule third tile is used only after exact interval, baseline and actual-cost matching' : 'Baseline and actual saving share eligible measured intervals; derived schedule saving is unavailable'}: ${date(data.coverage_start)} → ${date(data.coverage_end)} (Europe/London). ${data.covered_hours} covered hours; ${data.gap_count || 0} gaps excluded. Partial scope, NOT a full month or lifetime.${scheduleAvailable ? ' Ordinary replay and physical calibration are retrospective estimates; the separate legacy policy retains its original registration.' : ''}` : 'No eligible common covered interval yet.',
            data?.coverage_segments?.length ? 'Exact main-comparison coverage segments (UTC):\n' + segmentsText(data.coverage_segments) : '',
            data?.boundary_version ? `Main comparison boundary: ${data.boundary_version}. Estimated conversion-loss correction applies to household demand BEFORE both baseline and schedule; actual cost uses measured import/export. Raw data and prior reports are retained.` : '',
            reserve ? `Live reserve: ${reserve.status === 'observed' ? `${reserve.value}%` : reserve.status}; observed ${reserve.timestamp || 'unknown'}; source: ${reserve.source || reserve.reason || 'unknown'}${reserve.register != null ? `; register ${reserve.register}` : ''}. This current observation is NOT retro-assigned to historical intervals; historical reserve uses its own timestamped evidence.` : 'Live reserve: no timestamped observation in this report; no saved-option fallback.',
            report?.scope?.label || '',
            blockers.length ? 'Evidence gaps:\n• ' + blockers.join('\n• ') : 'No outstanding evidence blockers for this covered interval.',
            report?.methodology || 'Baseline: same demand at configured example baseline rates. Actual saving = baseline − actual timestamp-priced import cost and standing, plus export revenue. Timed schedule is COST, not saving. No rates are retrospectively guessed.',
            report?.current_export_tariff ? `Current export tariff: ${report.current_export_tariff.template}; effective ${date(report.current_export_tariff.effective_from)}${report.current_export_tariff.assumed ? ' (ASSUMED, not billing verified)' : ''}. Rates GBP/kWh; standing GBP/day.` : 'Export tariff requires your own dated configuration; example rates are not billing evidence.',
            data?.assumed_tariff ? 'This covered-period measured-meter cost estimate uses ASSUMED tariff dates/rates; it is not a verified supplier bill.' : '',
            data?.calibration?.length ? 'Conditional calibration and evidence:\n' + data.calibration.map(item => `${item.status === 'estimated' ? 'ESTIMATED — not verified calibration' : `Reported calibration status: ${item.status || 'unknown'}`}; input/model ${item.input_id}; boundary ${item.load_boundary || 'unspecified'}.\nEvidence: ${item.loss_evidence || 'not supplied'}\nUncertainty/assumptions: ${item.uncertainty || 'not supplied'}`).join('\n\n') : '',
            selectedModels.length ? 'Model parameters/provenance used by this covered row:\n' + selectedModels.map(model => `Model ${model.id}; registered ${model.model_registered_at || model.ledger_registered_at || 'unknown'}; effective scenario date ${model.effective_from}; ${model.retrospective_calibration ? 'RETROSPECTIVE calibration, not a prospective AI test' : 'see dated calibration evidence'}.\nUsable capacity ${model.usable_capacity_kwh} kWh; charge transfer ${model.charge_efficiency}; discharge transfer ${model.discharge_efficiency}; PV factor ${model.pv_ac_factor}; incremental stored standby ${model.standby_loss_kw} kW. Inverter AC ceiling ${model.inverter_ac_limit_kw ?? 'unknown'} kW; battery DC rating ceilings charge/discharge ${model.battery_charge_dc_limit_kw ?? 'unknown'}/${model.battery_discharge_dc_limit_kw ?? 'unknown'} kW. Grid-import ceiling ${model.grid_limit_unreported ? 'UNREPORTED — headroom feasibility unknown' : model.grid_import_limit_kw ?? 'unknown'}.\nHardware evidence: ${model.hardware_evidence || 'not supplied'}`).join('\n\n') : '',
            data?.sensitivity?.length ? 'Sensitivity — illustrative assumptions, NOT measured confidence bounds; each available case uses the exact central covered interval set:\n' + data.sensitivity.map(item => `${JSON.stringify(item.parameters)}: ${item.status}; covered hours ${item.covered_hours ?? 'unavailable'}\n${item.values ? valuesText(item.values) : (item.blockers || ['Unavailable on identical coverage']).join('; ')}`).join('\n\n') : '',
            original?.values ? `Original measured-only accounting — inclusive-register boundary, retained separately, NOT directly comparable with the main revised-boundary subset:\n${original.coverage_start} → ${original.coverage_end}; ${original.covered_hours} covered hours; ${original.gap_count || 0} gaps.\n${valuesText(original.values)}\nExact original accounting segments (UTC):\n${segmentsText(original.coverage_segments)}` : '',
            settingsText ? 'Timestamped settings/capability provenance (current observations, not historic backfill):\n' + settingsText + '\nInvalid U32 all-ones readings are not real power caps. Dynamic availability, ratings and configured limits are distinct; grid headroom may remain unknown.' : '',
            report?.tariff_history?.length ? 'Configured tariff histories (GBP/kWh; standing GBP/day):\n' + report.tariff_history.map(v => `${v.kind}: ${v.template} from ${date(v.effective_from)}${v.assumed ? ' — ASSUMED/editable, not billing verified' : ''}; standing ${v.standing}; bands ${v.bands.map(([a,b,p]) => `${String(Math.floor(a/60)).padStart(2,'0')}:${String(a%60).padStart(2,'0')}–${String(Math.floor(b/60)).padStart(2,'0')}:${String(b%60).padStart(2,'0')} ${p}`).join(', ')}`).join('\n') : '',
            `Separately retained PEAK-ONLY benchmark registered: ${report?.policy_registered_at || 'not configured'}. 02–05 charge towards 100%; PV serves house then charges; preserve until 16; 16–19 discharge to house down to timestamp-observed reserve; preserve after 19. No deliberate battery export; policy timing has not been retrospectively optimized. Estimated physical calibration is separate from policy registration.`,
            data?.model || 'Schedule needs measured starting SOC, usable capacity before reserve, dated reserve, calibrated losses and verified battery/grid/export limits. No terminal energy credit: differing ending battery inventories can bias a short-period cost comparison.',
            data?.simulated_initial_stored_kwh != null ? `Initial simulated inventory: ${data.simulated_initial_stored_kwh} kWh; final: ${data.simulated_final_stored_kwh} kWh; actual terminal SOC: ${data.actual_terminal_soc ?? 'unknown'}%.` : '',
            data?.soc_resets?.length ? 'Conditional segment SOC resets (no energy carried across omitted time):\n' + data.soc_resets.map(item => `${item.timestamp}: measured SOC ${item.measured_soc}%; ${item.reason}`).join('\n') : '',
            data?.schedule_exclusion_reasons?.length ? 'Reasons other intervals were excluded from the main schedule comparison (not blockers for the displayed covered subset):\n• ' + data.schedule_exclusion_reasons.join('\n• ') : '',
            report?.historical_backfill ? (typeof report.historical_backfill === 'string' ? report.historical_backfill : JSON.stringify(report.historical_backfill, null, 2)) : 'Daily generation alone cannot supply matching import/export/load intervals.',
            report?.backfill_error ? `Recorder import error: ${report.backfill_error}` : '',
            data?.preceding_gap ? `Preceding coverage gap: ${data.preceding_gap}` : '',
            report?.scan_limited ? 'Recent-interval scan limit applies; older ledger data is retained, not included in this displayed period.' : '',
            data?.values ? 'Unrounded GBP backend ledger values (schedule means retained COST, not the third card saving):\n' + Object.entries(data.values).map(([k,v]) => `${k}: ${v}`).join('\n') + (legacyScheduleSaving ? `\nSeparately retained peak-only policy SAVING: ${legacyScheduleSaving.value}` : '') : '',
            report?.comparison_v2 ? 'Prospective v2 tracking — not causal AI proof; original registered policy remains separate:\n' + JSON.stringify(report.comparison_v2, null, 2) : 'Prospective v2 tracking has not been deployed or registered yet.',
            'Display: mathematical ceiling to whole GBP, including negative savings; grouping separators. Tap details does not control any energy device.'];
        const corrected = report?.corrected_view;
        const sensitivities = Object.entries(corrected?.fixed_scenarios || {}).map(([name, scenario]) => ({name, physics:scenario.physics, view:scenario.periods?.[selection.period]}));
        this._id('financeDetails').textContent = [
            'CONDITIONAL MODEL SCENARIO RANGE — not a confidence interval, guaranteed physical bound, verified saving or AI winner. No midpoint. Retained physical model is the principal timed tile; separately named training-only sensitivities are not adopted as measured capacity.',
            !fresh ? 'Telemetry stale or disconnected: numeric tiles hidden.' : '',
            corrected?.status || 'Corrected hourly view pending; the legacy segmented ordinary result is audit only.',
            data ? `Same recovered coverage for all THREE cards: ${data.coverage_start} → ${data.coverage_end}; ${data.covered_hours} hours. Month and recorded period reduce ONE trajectory per fixed scenario, never a new measured-SOC anchor. Baseline ${data.values.baseline} GBP; measured gross actual cost ${data.values.actual} GBP; actual saving ${data.actual_saving} GBP. Timed full-precision conditional saving endpoints: ${data.schedule_saving_range.join(' to ')} GBP. ${scheduleSaving?.display || 'Pending'}.` : (corrected?.blockers || fallback).join('\n'),
            data ? `Initial virtual stock range ${data.simulated_initial_stored_range.join(' to ')} kWh; terminal ${data.simulated_final_stored_range.join(' to ')} kWh. Actual terminal SOC ${data.actual_terminal_soc ?? 'unknown'}% (capacity-to-cell mapping remains conditional). ${data.terminal_stock_qualification}` : '',
            corrected?.physical_limits || '',
            corrected?.boundary_qualification || 'EV inclusion in Sigen CT is unverified; separate circuits are not proof of exclusion.',
            'Policy unchanged: 02–05 Europe/London charging towards full; otherwise self-consumption to dated reserve; no deliberate battery export. Unknown gaps widen conserved state; gap cash omitted identically from all three columns. Recovered endpoint totals have unknown within-interval dispatch/elapsed-time allocation uncertainty.',
            'Fixed scenarios and unrounded values (training frozen before separate holdout; no online fitting):\n' + JSON.stringify(sensitivities, null, 2),
            'Reconstruction and omitted-gap provenance:\n' + JSON.stringify(corrected?.preparation || {}, null, 2),
            'Display: mathematical ceiling applied independently ONCE to each full-precision endpoint, including negative amounts. This is not a rounded-input subtraction. Both endpoints retained even when ceilings coincide.',
            'Retained native/parent accounting — AUDIT ONLY, not any of the three corrected cards:\n' + JSON.stringify(report?.periods?.[selection.period] || {}, null, 2),
            report?.tariff_history ? 'Unchanged dated tariff/provenance histories:\n' + JSON.stringify(report.tariff_history, null, 2) : ''
        ].filter(Boolean).join('\n\n');
    }
    _observeCharts() {
        if (this._chartObserver) this._chartObserver.disconnect();
        this._chartObserver = new ResizeObserver(entries => {
            for (const entry of entries) entry.target._redraw?.();
            // Layout writes run outside the observer delivery cycle, once per frame.
            if (entries.some(entry => entry.target.fit)) this._queueSceneFit();
        });
        this._chartObserver.observe(this._id('chart'));
        this._chartObserver.observe(this._id('pvChart'));
        this._chartObserver.observe(this._id('energyChart'));
        this._chartObserver.observe(this._id('statChart'));
        const scene = this._id('scene'), sidebar = this.root.querySelector('.sidebar');
        scene.fit = sidebar.fit = () => this._fitScene();
        this._chartObserver.observe(scene);
        for (const el of [this._id('cabinetIllustration'), this._id('chargers'), this._id('pvReadout'), this._id('houseReadout'), this._id('evReadout')]) {
            el.fit = scene.fit;
            this._chartObserver.observe(el);
        }
        this._chartObserver.observe(sidebar);
        if (!this._heroResize) {
            this._heroResize = () => this._queueSceneFit();
            window.addEventListener('resize', this._heroResize);
            window.visualViewport?.addEventListener('resize', this._heroResize);
        }
    }
    _queueSceneFit() {
        if (this._backupReserveEditing) { this._backupReserveFitDeferred = true; return; }
        if (this._fitFrame) return;
        this._fitFrame = requestAnimationFrame(() => {
            this._fitFrame = null;
            if (this._backupReserveEditing) { this._backupReserveFitDeferred = true; return; }
            if (this.isConnected) this._fitScene();
        });
    }
    // The lifetime parts (charged, discharged) share a line when it fits beside the garage, else stack;
    // with a single charger the line must also stop short of the charger on the garage wall.
    _stackLifetime(narrow, scale) {
        const art = ARTWORK['charger_day.png'], single = this._id('acNode').hidden !== this._id('dcNode').hidden;
        const room = narrow ? 230 : single ? art.at[0] - READINGS.chargers[0] - 8 : 420;
        for (const id of ['dcLifetime']) {
            const el = this._id(id);
            el.classList.remove('stack');
            el.style.whiteSpace = 'nowrap';
            const range = document.createRange();
            range.selectNodeContents(el);
            const width = range.getBoundingClientRect().width;
            el.style.whiteSpace = '';
            el.classList.toggle('stack', width > room * scale);
        }
    }
    // With one charger its readings are level with the charger on the garage wall; otherwise they are
    // centred vertically.
    _alignChargers(narrow) {
        const chargers = this._id('chargers'), ac = this._id('acNode'), dc = this._id('dcNode');
        if (ac.hidden === dc.hidden) {
            Object.assign(chargers.style, { top: '', transform: '', minHeight: '' });
            return;
        }
        const art = ARTWORK['charger_day.png'];
        Object.assign(chargers.style, { top: `${(PHOTO.top + art.at[1] + art.size[1] / 2) / SCENE.height * 100}%`, transform: '', minHeight: '0' });
        if (narrow) chargers.style.left = `${(art.at[0] + art.size[0] + 14) / SCENE.width * 100}%`;
    }
    // The scene is shown at its preferred scale with the sides cropped; once the visible width reaches
    // the protected range, the whole scene scales down instead, so the house always stays in view.
    // Beside a taller sidebar, it scales up to fill the row as far as the protected range allows.
    _fitScene() {
        this._installSolarScroll();
        const visualElement = this.root.querySelector('.visual');
        const narrow = this.clientWidth <= SCENE.narrowBelow;
        if (narrow) visualElement.style.removeProperty('max-width');
        else {
            const viewportHeight = window.visualViewport?.height || window.innerHeight;
            const top = Math.max(64, Math.min(visualElement.getBoundingClientRect().top, viewportHeight * .35));
            const budget = Math.max(300, Math.min(820, viewportHeight - top - 16));
            const reserved = parseFloat(visualElement.style.getPropertyValue('--hero-readout-clearance')) || 118;
            const imageHeight = Math.max(180, budget - reserved - 8);
            // Width AND height bound the same uniform scale. Never stretch or
            // crop the photo to fill an ultrawide panel's entire available row.
            visualElement.style.maxWidth = `${imageHeight * SCENE.width / SCENE.height}px`;
        }
        const scene = this._id('scene'), plane = this._id('plane'), sidebar = this.root.querySelector('.sidebar');
        const width = scene.clientWidth;
        if (!width) return;
        const [keepLeft, keepRight] = narrow ? SCENE.keepNarrow : SCENE.keep, keepWidth = keepRight - keepLeft;
        let scale = width / keepWidth;
        let height = SCENE.height * scale;
        const visible = width / scale;
        const centre = Math.min(SCENE.width - visible / 2, Math.max(visible / 2, (keepLeft + keepRight) / 2));
        scene.style.height = `${height}px`;
        Object.assign(plane.style, { width: `${SCENE.width * scale}px`, height: `${SCENE.height * scale}px`, left: `${-(centre - visible / 2) * scale}px`, top: `${(height - SCENE.height * scale) / 2}px` });
        plane.style.setProperty('--s', scale);
        plane.classList.toggle('compact', scale < .6);
        plane.classList.toggle('narrow', narrow);
        plane.classList.toggle('tiny', width < 600);
        this._id('housePhotoBounds').setAttribute('x', centre - visible / 2);
        this._id('housePhotoBounds').setAttribute('width', visible);
        const chargers = this._id('chargers');
        chargers.style.left = `${(READINGS.chargers[narrow ? 1 : 0] / SCENE.width * 100).toFixed(3)}%`;
        this._stackLifetime(narrow, scale);
        this._alignChargers(narrow);
        // Photo-space placement, not a reserved column. Phone internals share
        // one 136x280 coordinate system, scaled together to a readable shell.
        const visual = this.root.querySelector('.visual').getBoundingClientRect();
        const photoPlane = plane.getBoundingClientRect();
        const cabinetWidth = narrow ? Math.max(76, Math.min(88, CABINET.width * scale)) : CABINET.width * scale;
        const cabinetX = narrow ? PHOTO.width * .105 : CABINET.x;
        const cabinet = this.root.querySelector('.cabinet-rail');
        Object.assign(cabinet.style, {
            left: `${photoPlane.left - visual.left + cabinetX * scale}px`,
            top: `${photoPlane.top - visual.top + (PHOTO.top + CABINET.y) * scale}px`,
            width: `${cabinetWidth}px`,
            height: `${280 * cabinetWidth / 136}px`,
        });
        cabinet.style.setProperty('--cabinet-scale', cabinetWidth / 136);
        Object.assign(this._id('gridRail').style, {
            width: `${Math.max(0, photoPlane.left - visual.left + cabinetX * scale - 8)}px`,
            top: `${photoPlane.top - visual.top}px`,
            height: `${SCENE.height * scale}px`,
        });
        this._fitCabinetFlows(scale);
    }
    _fitCabinetFlows(scale) {
        const art = this._id('cabinetIllustration').getBoundingClientRect(), plane = this._id('plane').getBoundingClientRect();
        if (!art.width || !scale) return;
        const port = (x, y) => ({ x: Math.round(((art.left - plane.left) + x * art.width / 136) / scale * 100) / 100,
            y: Math.round(((art.top - plane.top) + y * art.height / 280) / scale * 100) / 100 });
        const gridRail = this._id('gridRail').getBoundingClientRect(), grid = port(8, 80);
        // GRID is a separate endpoint in the photo margin, not a cabinet label.
        // Its bottom-centre starts a visible vertical leg before the left port.
        this._id('gridMeter').style.top = `${PHOTO.height * .26 * scale}px`;
        const meter = this._id('gridMeter').getBoundingClientRect();
        const gridStart = {
            x: (meter.left + Math.min(meter.width, gridRail.width) / 2 - plane.left) / scale,
            y: (meter.bottom + 4 - plane.top) / scale,
        };
        const foregroundY = Math.max(780, (art.bottom - plane.top + 16) / scale);
        const paths = cabinetFlowPaths(port, gridStart, foregroundY);
        for (const item of this._flowItems ?? []) {
            if (item.wire.getAttribute('d') === paths[item.id]) continue;
            for (const path of [item.wire, item.glow, ...item.comets]) path.setAttribute('d', paths[item.id]);
            item.length = 0;
            this._resetFlowAnimation(item.id);
            this._id(`glow-${item.id}`)?.remove();
        }
        const fork = this._id('evFork');
        fork.setAttribute('cx', (gridStart.x + port(-2, 80).x) / 2);
        fork.setAttribute('cy', grid.y);
        fork.setAttribute('r', 1.6 / scale);
        // Keep the complete live Cupra label BELOW the entire foreground cable.
        this._id('evReadout').style.top = `${(foregroundY + 14 / scale) / SCENE.height * 100}%`;
        for (const [id, x, y, end] of [['cabinetMagnifyTop', 136, 18, '390 543'], ['cabinetMagnifyBottom', 136, 264, '438 625']]) {
            const p = port(x, y);
            this._id(id).setAttribute('d', `M${p.x} ${p.y} L${end}`);
        }
        this.root.querySelector('.visual').classList.add('ports-ready');
        this._fitEndpointLabels(scale);
        this._fitCabinetReading();
        this._reserveMobileReadoutSpace();
    }
    _fitCabinetReading() {
        const content = this.root.querySelector('.cabinet-content');
        const reading = this.root.querySelector('.cabinet-reading');
        if (!content || !reading) return;
        const fields = [[this._id('battery'), 18], [this._id('soc').parentElement, 12], [this._id('battStatus'), 11]];
        // Font/layout units stay in the same 136x280 cabinet coordinate space
        // on every screen and at browser zoom; no external text or ellipsis.
        for (const [el, size] of fields) {
            if (!el) continue;
            el.style.fontSize = `${size}px`;
            if (el.clientWidth && el.scrollWidth > el.clientWidth)
                el.style.fontSize = `${Math.max(8, size * el.clientWidth / el.scrollWidth)}px`;
        }
        const shell = content.getBoundingClientRect(), box = reading.getBoundingClientRect();
        const available = shell.bottom - 6 * shell.width / 136 - box.top;
        if (available > 0 && box.height > available) {
            const ratio = available / box.height * .97;
            for (const [el] of fields) if (el)
                el.style.fontSize = `${Math.max(8, parseFloat(el.style.fontSize) * ratio)}px`;
        }
    }
    _updateBatteryArtwork(soc, power) {
        const state = batteryVisualState(soc, power, this._flowsConnected());
        const art = this._id('batteryArtwork');
        for (let level = 1; level <= 10; level++)
            this._id(`batteryStep${level}`).classList.toggle('on', state.known && level <= state.level);
        for (const mode of ['charging', 'discharging'])
            art.classList.toggle(mode, state.known && state.mode === mode);
        this._id('batteryUnknown').toggleAttribute('hidden', state.known);
        this._text('batteryArtTitle', state.known
            ? `Battery ${soc}% SOC; ten-step display ${state.level * 10}%; ${state.mode}. Energy source mix is not measured.`
            : 'Battery SOC unavailable; no charge level inferred');
        art.setAttribute('data-level', state.known ? state.level : 'unknown');
        this._fitCabinetReading();
    }
    _reserveMobileReadoutSpace() {
        const visual = this.root.querySelector('.visual');
        // The plane/readout are absolute and do not contribute to grid height.
        // Measure relative to the scene, not the padded visual, so repeated
        // fits cannot accumulate padding or oscillate as the EV text changes.
        const bottom = this._id('evReadout').getBoundingClientRect().bottom;
        const sceneBottom = this._id('scene').getBoundingClientRect().bottom;
        const cabinetBottom = this.root.querySelector('.cabinet-rail').getBoundingClientRect().bottom;
        const clearance = Math.max(118, Math.ceil(Math.max(bottom, cabinetBottom) - sceneBottom + 16));
        const value = `${clearance}px`;
        if (visual.style.getPropertyValue('--hero-readout-clearance') !== value) {
            visual.style.setProperty('--hero-readout-clearance', value);
            this._queueSceneFit();
        }
    }
    _fitEndpointLabels(scale) {
        const scene = this._id('scene').getBoundingClientRect(), plane = this._id('plane').getBoundingClientRect();
        const sx = plane.width / (SCENE.width * scale), sy = plane.height / (SCENE.height * scale);
        const wires = this._flowItems.map(item => item.wire), points = [];
        // Test the whole label rectangles against every visible measured branch,
        // including idle wires. Work in the same screen transform as the SVG paths.
        for (const wire of wires) {
            const length = wire.getTotalLength(), matrix = wire.getScreenCTM();
            for (let at = 0; at <= length; at += Math.max(1, .75 / scale))
                points.push(wire.getPointAtLength(at).matrixTransform(matrix));
        }
        const occupied = [], gap = 8;
        for (const [id, flow, end, preferred, prefix] of [
            ['pvReadout', 'solarFlow', false, 'right', 'pv'],
            ['houseReadout', 'homeFlow', true, 'left', 'house'],
        ]) {
            const label = this._id(id), wire = this._id(flow).querySelector('.wire');
            const matrix = wire.getScreenCTM(), anchor = wire.getPointAtLength(end ? wire.getTotalLength() : 0);
            const p = anchor.matrixTransform(matrix), box = label.getBoundingClientRect(), w = box.width, h = box.height;
            const candidates = [];
            for (const side of [preferred, preferred === 'left' ? 'right' : 'left'])
                for (const dy of [0, 4, 8, 12, h / 2, h, -4])
                    candidates.push({ left: side === 'right' ? p.x + gap : p.x - gap - w, top: p.y - h + dy });
            candidates.push({ left: p.x - w / 2, top: p.y - gap - h }, { left: p.x - w / 2, top: p.y + gap });
            const clear = r => r.left >= scene.left + 2 && r.top >= scene.top + 2 && r.left + w <= scene.right - 2 && r.top + h <= scene.bottom - 2
                && !points.some(q => q.x > r.left - 3 && q.x < r.left + w + 3 && q.y > r.top - 3 && q.y < r.top + h + 3)
                && !occupied.some(q => r.left < q.right + 3 && r.left + w > q.left - 3 && r.top < q.bottom + 3 && r.top + h > q.top - 3);
            const chosen = candidates.find(clear) ?? {
                left: Math.max(scene.left + 2, Math.min(p.x + gap, scene.right - w - 2)),
                top: Math.max(scene.top + 2, Math.min(p.y - h, scene.bottom - h - 2)),
            };
            // HOUSE only: reduce the horizontal gap 8 -> 3 px; keep vertical placement.
            if (id === 'houseReadout') {
                if (chosen.left + w <= p.x) chosen.left += 5;
                else if (chosen.left >= p.x) chosen.left -= 5;
                // Requested adjustment: 60 CSS pixels right of the previous position.
                chosen.left += 60;
            }
            label.style.left = `${(chosen.left - plane.left) / sx}px`;
            label.style.top = `${(chosen.top - plane.top) / sy}px`;
            occupied.push({ ...chosen, right: chosen.left + w, bottom: chosen.top + h });
            const nearest = new DOMPoint(Math.max(chosen.left, Math.min(p.x, chosen.left + w)), Math.max(chosen.top, Math.min(p.y, chosen.top + h))).matrixTransform(matrix.inverse());
            this._id(`${prefix}EndpointLeader`).setAttribute('d', `M${anchor.x} ${anchor.y} L${nearest.x} ${nearest.y}`);
            const dot = this._id(`${prefix}EndpointDot`);
            dot.setAttribute('cx', anchor.x); dot.setAttribute('cy', anchor.y); dot.setAttribute('r', 1.8 / scale);
        }
        this._id('plane').classList.add('labels-ready');
    }
    _installSolarScroll() {
        // LCARS caps/locks panel views. Override only this Solar view's actual
        // ancestors while mounted; never touch the global theme, body or document.
        if (!this.isConnected || !/^\/solar-energy(?:\/|$)/.test(window.location.pathname)) return;
        this._solarScrollStyles ??= new Map();
        const tags = new Set(['hui-card', 'hui-card-options', 'hui-panel-view', 'hui-view', 'hui-view-container', 'hui-root', 'ha-panel-lovelace']);
        for (let el = this.parentElement ?? this.getRootNode()?.host; el; el = el.parentElement ?? el.getRootNode()?.host) {
            const computed = el.style ? window.getComputedStyle?.(el) : null;
            const clippedWrapper = computed && (['hidden', 'clip', 'auto', 'scroll'].includes(computed.overflowY)
                || (computed.maxHeight && computed.maxHeight !== 'none'));
            if ((tags.has(el.localName) || clippedWrapper) && el.style) {
                let saved = this._solarScrollStyles.get(el);
                if (!saved) { saved = new Map(); this._solarScrollStyles.set(el, saved); }
                const viewportHeight = window.visualViewport?.height || window.innerHeight;
                const scrollHeight = Math.max(240, viewportHeight - Math.max(0, el.getBoundingClientRect().top));
                const panel = el.localName === 'ha-panel-lovelace';
                const overrides = { height: panel ? `${scrollHeight}px` : 'auto', 'max-height': panel ? `${scrollHeight}px` : 'none',
                    'min-height': '0px', 'overflow-x': panel ? 'clip' : 'visible', 'overflow-y': panel ? 'auto' : 'visible', 'touch-action': 'pan-y pinch-zoom' };
                for (const [name, value] of Object.entries(overrides)) {
                    if (!saved.has(name)) saved.set(name, { value: el.style.getPropertyValue(name), priority: el.style.getPropertyPriority(name), applied: value });
                    saved.get(name).applied = value;
                    el.style.setProperty(name, value, 'important');
                }
            }
            if (el.localName === 'ha-panel-lovelace') break;
        }
    }
    _restoreSolarScroll() {
        for (const [el, saved] of this._solarScrollStyles ?? []) for (const [name, original] of saved) {
            // Do not overwrite another component's later inline edit.
            if (el.style.getPropertyValue(name) !== original.applied || el.style.getPropertyPriority(name) !== 'important') continue;
            if (original.value) el.style.setProperty(name, original.value, original.priority);
            else el.style.removeProperty(name);
        }
        this._solarScrollStyles = null;
    }
    // Artwork always uses actual current solar elevation, never forecast symbols or the theme override.
    _isDay() {
        return solarSunPhase(this._hass?.states['sun.sun']) === 'day';
    }
    _updateScene() {
        const now = Date.now();
        const phase = solarSunPhase(this._hass?.states['sun.sun'], now);
        const model = solarPhotoWeather(this._hass?.states[this.config.weather_entity || 'weather.example'], now);
        const day = phase === 'day';
        this.setAttribute?.('data-weather-phase', phase || 'unknown');
        const category = model.category;
        const file = phase && category ? (this.config.weather_image_template ? this.config.weather_image_template.replace('{phase}', phase).replace('{category}', category) : HOUSE_IMAGE) : HOUSE_IMAGE;
        const tooltip = `${model.tooltip} ${phase ? `Current sun.sun elevation: ${this._hass.states['sun.sun'].attributes.elevation}° (${phase}).` : 'Current sun elevation stale / unavailable; original background retained.'}`;
        this._id('scene').setAttribute('title', tooltip);
        for (const photo of this.root.querySelectorAll('.photo')) photo.setAttribute('aria-label', tooltip);
        if (this._sceneFile === file) return;
        this._sceneFile = file;
        this._id('chargerArt').setAttribute('href', ARTWORK[day ? 'charger_day.png' : 'charger_night.png'].href);
        for (const photo of this.root.querySelectorAll('.photo')) {
            photo.onerror = () => {
                photo.onerror = null;
                photo.setAttribute('href', HOUSE_IMAGE);
                const failed = `${tooltip} Selected image failed to load; original background retained.`;
                photo.setAttribute('aria-label', failed);
                this._id('scene').setAttribute('title', failed);
            };
            photo.setAttribute('href', file);
        }
    }
    _updateWeather() {
        if (!this._hass || !this.root?.querySelectorAll) return;
        if (this.isConnected) this._weatherTimer ??= setInterval(() => { this._updateScene(); this._updateWeather(); }, 30000);
        const now = Date.now(), model = solarWeatherModel(this._hass.states[SOLAR_WEATHER_ENTITY], now);
        const current = this._hass.states[this.config.weather_entity || 'weather.example'];
        const currentTime = Date.parse(current?.last_reported ?? current?.last_updated);
        const temperature = weatherNumber(current?.attributes?.temperature);
        const currentFresh = current && !['unknown', 'unavailable'].includes(current.state) && Number.isFinite(currentTime) && now - currentTime >= -300000 && now - currentTime <= 90 * 60000;
        const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
        const time = (ms, zone = false) => new Intl.DateTimeFormat('en-GB', { timeZone: this._hass.config?.time_zone || 'Europe/London', hour: '2-digit', minute: '2-digit', ...(zone ? { timeZoneName: 'short' } : {}) }).format(ms);
        const nowText = currentFresh && temperature !== null && ['°C', '°F'].includes(current.attributes.temperature_unit) ? `${temperature.toFixed(1)}${current.attributes.temperature_unit}` : '—';
        const hourly = model.rows.map(row => `<div class="weather-hour" title="${escape(time(row.start, true))}: ${escape(row.label)}, temperature at hour start. ${escape(time(row.start, true))}–${escape(time(row.end, true))}: precipitation likelihood greater than 0.1 mm."><span><ha-icon icon="${row.icon}"></ha-icon> ${escape(time(row.start))}</span><span>${row.temperature === null ? '—' : row.temperature.toFixed(1) + '°'} · ${row.probability === null ? '—' : row.probability + '%'}</span></div>`).join('');
        const phase = solarSunPhase(this._hass.states['sun.sun'], now);
        const rain = model.probability === null ? '—' : `${model.probability}%`;
        const fetched = model.fetched === null ? 'Never fetched' : `Fetched ${time(model.fetched, true)} (${Math.max(0, Math.floor((now - model.fetched) / 60000))}m ago)`;
        const methodology = 'Maximum of full-hour precipitation probabilities for bins intersecting the rolling next four hours. Greater than 0.1 mm; NOT an aggregate four-hour chance. First/last bins may only partly intersect. Missing probabilities remain unavailable.';
        const provenance = `Forecast conditions, temperatures and probabilities: Open-Meteo, approximate town area (coordinates rounded to 0.1°). ${fetched}. Model issue time not supplied. Forecast expires 90 minutes after fetch. Current Now temperature: met.no${currentFresh ? ', updated ' + time(currentTime, true) : ', stale / unavailable'}.${phase ? ' Current solar phase: ' + phase + '.' : ' Sun elevation unavailable; original background retained.'}`;
        const markup = `<div class="weather-head"><ha-icon class="weather-symbol" icon="${model.icon}" aria-hidden="true"></ha-icon><div class="weather-now" title="Current temperature · met.no${currentFresh ? ' · ' + escape(time(currentTime, true)) : ' · unavailable'}"><small>Now · met.no</small><strong>${escape(nowText)}</strong></div><div class="weather-condition"><small>Next 4h</small><strong>${escape(model.label)}</strong></div><div class="weather-peak" tabindex="0" title="${escape(methodology)}" aria-label="Peak hourly precipitation chance ${model.probability === null ? 'unavailable' : escape(rain)}. ${escape(methodology)}"><strong${model.probability === null ? ' class="weather-unknown"' : ''}>${escape(rain)}</strong><small>Peak hourly rain chance</small></div></div><div class="weather-hours" style="--weather-hours:${Math.max(1, model.rows.length)}">${hourly || '<span>No usable hourly forecast</span>'}</div><div class="weather-meta" tabindex="0" title="${escape(provenance)}">${model.fetched === null ? 'Not updated' : 'Updated ' + escape(time(model.fetched))} · <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo</a>${phase ? '' : ' · Sun unavailable'}</div>${model.hint ? `<div class="weather-hint">${escape(model.hint)}</div>` : ''}`;
        for (const node of this.root.querySelectorAll('.solar-weather')) if (node.innerHTML !== markup) node.innerHTML = markup;
        this._placeWeather?.();
        if (this.isConnected && !this._weatherResize && typeof ResizeObserver !== 'undefined') {
            const scene = this._id('scene');
            if (scene) { this._weatherResize = new ResizeObserver(() => this._placeWeather()); this._weatherResize.observe(scene); }
        }
    }
    _placeWeather() {
        // Measure the permanently mounted, hidden sky candidate. Fall back to
        // the existing right column ABOVE chat, which naturally stacks below
        // the photo on mobile. Never move/rebuild the iframe or refit the photo.
        const sky = this.root.querySelector?.('.weather-sky');
        const scale = parseFloat(this._id?.('plane')?.style?.getPropertyValue('--s'));
        const rect = sky?.getBoundingClientRect?.();
        const placement = rect?.width > 0 && scale >= .6 && rect.height <= 180 * scale ? 'sky' : 'column';
        if (this.getAttribute?.('data-weather-placement') !== placement) this.setAttribute?.('data-weather-placement', placement);
    }
    _id(id) {
        return this.root.getElementById(id)
    }
    _moreInfo(entityId) {
        if (!entityId) return;
        this.dispatchEvent(new CustomEvent('hass-more-info', { bubbles: true, composed: true, detail: { entityId } }));
    }
    _has(id) {
        return !!id && !!this._hass?.states[id];
    }
    // Entity for an `entities:` key: the configured one, else the first available default; null when disabled.
    _ent(key) {
        const set = this.config.entities?.[key];
        if (set === false || set === null) return null;
        if (set) return set;
        const ids = DEFAULT_ENTITIES[key];
        return ids.find(id => !['unknown', 'unavailable', undefined].includes(this._hass?.states[id]?.state)) ?? ids[0] ?? null;
    }
    _num(id) {
        const s = id ? this._hass?.states[id]?.state : undefined;
        return s === undefined || s === null || String(s).trim() === '' || ['unknown', 'unavailable', 'none'].includes(String(s).toLowerCase()) ? null : (Number.isFinite(Number(s)) ? Number(s) : null)
    }
    // Rewriting unchanged text under the pointer between press and release cancels the click.
    _text(id, v) {
        this._setText(this._id(id), v);
    }
    // Status parts sit on one line, or one per line when the scene is small.
    _parts(id, parts) {
        const el = this._id(id), list = parts.filter(Boolean);
        if (el.dataset.parts === list.join('\n')) return;
        el.dataset.parts = list.join('\n');
        el.replaceChildren(...list.map(text => Object.assign(document.createElement('span'), { textContent: text })));
    }
    _setText(el, v) {
        if (el.textContent !== String(v)) el.textContent = v;
    }
    // Value in kW or kWh when the entity reports W, Wh, MW or MWh.
    _scaled(id) {
        const v = this._num(id);
        return v === null ? null : v * this._factor(id);
    }
    _factor(id) {
        return { W: .001, Wh: .001, MW: 1000, MWh: 1000 }[this._hass?.states[id]?.attributes?.unit_of_measurement] ?? 1;
    }
    // Battery power with charging positive, whatever the source's sign convention.
    _batterySign() {
        return this.config.battery_power_invert ? -1 : 1;
    }
    _pvEntity(i, key) {
        return this._pv[i][key];
    }
    _updateHome() {
        if (!this._hass || !this._id('homeDialog').open) return;
        const home = this._scaled(this._ent('home_power'));
        const loads = this.config.home_entities.map(item => {
            const entity = typeof item === 'string' ? item : item.entity;
            const name = (typeof item === 'object' && item.name) || this._hass.states[entity]?.attributes?.friendly_name || entity;
            const icon = (typeof item === 'object' && item.icon) || this._hass.states[entity]?.attributes?.icon || '';
            return { entity, name, icon, kw: this._scaled(entity) };
        }).sort((a, b) => (b.kw ?? -Infinity) - (a.kw ?? -Infinity));
        const measured = loads.reduce((sum, l) => sum + Math.max(0, l.kw ?? 0), 0);
        const scale = Math.max(home ?? 0, measured, .001);
        const other = home === null ? null : home - measured;
        const drawing = loads.filter(l => l.kw !== null && l.kw >= .005);
        const rows = drawing.map(l => this._loadRow(l, l.entity, scale, home));
        if (!drawing.length) rows.push(this._loadsEmpty ??= Object.assign(document.createElement('div'), { className: 'muted', textContent: 'No listed load is drawing power' }));
        if (other !== null && other >= .005) rows.push(this._loadRow({ name: 'Other (unmeasured)', icon: '', kw: other }, null, scale, home));
        const list = this._id('homeLoads');
        if (rows.length !== list.children.length || rows.some((row, i) => row !== list.children[i])) list.replaceChildren(...rows);
        this._text('homeTotal', this._power(home));
        this._text('homeMeasured', home === null || home <= 0 ? '' : `Measured ${formatPower(measured)} · ${Math.round(Math.min(measured / home, 1) * 100)}% of Sigenergy-monitored consumption`);
    }
    // Rows are kept per entity and updated in place, so a click is not lost to a refresh.
    _loadRow(load, entity, scale, home) {
        const key = entity ?? '';
        let row = this._loadRows.get(key);
        if (!row) {
            row = document.createElement('button');
            row.className = entity ? 'load' : 'load other';
            if (entity) Object.assign(row.dataset, { entity }), row.title = entity;
            else row.tabIndex = -1;
            row.innerHTML = '<span class="name"><span></span></span><span></span><span class="pct"></span><span class="bar"><i></i></span>';
            this._loadRows.set(key, row);
        }
        const [name, value, pct, bar] = row.children;
        let icon = name.querySelector('ha-icon');
        if (load.icon && !icon) name.prepend(icon = document.createElement('ha-icon'));
        if (icon && icon.getAttribute('icon') !== load.icon) load.icon ? icon.setAttribute('icon', load.icon) : icon.remove();
        this._setText(name.lastElementChild, load.name);
        this._setText(value, formatPower(load.kw));
        this._setText(pct, home === null || home <= 0 ? '' : `${Math.round(Math.max(0, load.kw) / home * 100)}%`);
        bar.firstChild.style.width = `${(Math.max(0, load.kw) / scale * 100).toFixed(1)}%`;
        return row;
    }
    // A string is fitted when at least one of its entities exists in Home Assistant.
    _pvFitted(i) {
        return PV_KEYS.some(key => this._has(this._pv[i][key]));
    }
    _updatePv() {
        if (!this._hass || !this._id('pvDialog').open) return;
        const digits = { voltage: 1, current: 2, power: 2, daily_energy: 2, accumulated_energy: 2 };
        const sums = { power: null, daily_energy: null, accumulated_energy: null };
        this._pv.forEach((_, i) => {
            const fitted = this._pvFitted(i);
            this._id(`pvRow${i}`).hidden = !fitted;
            this._id(`pvLegend${i}`).hidden = !fitted;
            if (!fitted) return;
            for (const key of Object.keys(digits)) {
                const id = this._pvEntity(i, key);
                const v = key === 'voltage' || key === 'current' ? this._num(id) : this._scaled(id);
                this._text(`pv${i}_${key}`, v === null ? '—' : v.toFixed(digits[key]));
                if (key in sums && v !== null) sums[key] = (sums[key] ?? 0) + v;
            }
        });
        // Total row: the system's own PV sensors when available, otherwise the sum of the strings above.
        const kw = key => this._scaled(this._ent(key));
        // sensor.sigen_plant_total_pv_generation covers all PV in the plant, so the third-party share is removed
        // to keep the Total row to the Sigenergy strings; when the third-party total is unknown it is used as-is.
        let lifetime = kw('pv_strings_lifetime_energy');
        const thirdLifetime = kw('third_party_pv_lifetime_energy');
        if (lifetime !== null && thirdLifetime !== null && !this.config.entities?.pv_strings_lifetime_energy
            && this._ent('pv_strings_lifetime_energy') === 'sensor.sigen_plant_total_pv_generation') lifetime -= thirdLifetime;
        const totals = {
            power: kw('pv_strings_power') ?? sums.power,
            daily_energy: kw('daily_pv_energy') ?? sums.daily_energy,
            accumulated_energy: lifetime ?? sums.accumulated_energy,
        };
        for (const [key, v] of Object.entries(totals)) this._text(`pvTotal_${key}`, v === null ? '—' : v.toFixed(2));
        const thirdPower = this._ent('third_party_pv_power'), thirdTotal = this._ent('third_party_pv_lifetime_energy');
        this._id('pvThirdRow').hidden = !this._has(thirdPower) && !this._has(thirdTotal);
        const tp = this._scaled(thirdPower), tt = this._scaled(thirdTotal);
        this._text('pvThird_power', tp === null ? '—' : tp.toFixed(2));
        this._text('pvThird_accumulated_energy', tt === null ? '—' : tt.toFixed(2));
    }
    // Stored energy is SOC × rated capacity; no sensor reports it directly.
    _updateEnergy() {
        if (!this._hass || !this._id('energyDialog').open) return;
        const soc = this._num(this._socEntity), cap = this._scaled(this._ent('battery_capacity'));
        this._text('energyNow', soc === null || cap === null ? '—' : `${(soc * cap / 100).toFixed(1)} kWh`);
        this._text('energyOf', cap === null ? '' : `of ${cap.toFixed(1)} kWh rated capacity${soc === null ? '' : ` · ${soc.toFixed(1)}%`}`);
    }
    async _loadEnergyHistory() {
        if (!this._hass) return;
        const cap = this._scaled(this._ent('battery_capacity'));
        if (cap === null) {
            this._text('energyChartStatus', 'Rated capacity unavailable');
            return;
        }
        const day = new Date();
        day.setHours(0, 0, 0, 0);
        this._text('energyChartStatus', 'Loading history…');
        if (!this._socEntity) {
            this._text('energyChartStatus', 'Battery SOC not configured');
            return;
        }
        try {
            const query = `history/period/${encodeURIComponent(day.toISOString())}?filter_entity_id=${encodeURIComponent(this._socEntity)}&minimal_response&no_attributes`;
            const [points] = this._series(await this._hass.callApi('GET', query), [this._socEntity]);
            const series = [{ points: points.map(p => ({ t: p.t, v: p.v * cap / 100 })), color: 'var(--c-battery)', name: 'Stored', unit: 'kWh' }];
            const hasData = this._showChart(this._id('energyChart'), series, day.getTime(), false, Math.ceil(cap / 2) * 2);
            this._text('energyChartStatus', hasData ? 'Midnight → now' : 'No recorded battery history today');
        } catch (e) {
            this._text('energyChartStatus', 'Recorder history unavailable');
            this._id('energyChart').replaceChildren();
        }
    }
    async _loadPvHistory() {
        if (!this._hass) return;
        const day = new Date();
        day.setHours(0, 0, 0, 0);
        const strings = this._pv.map((pv, i) => ({ ...pv, color: PV_COLORS[i % PV_COLORS.length] })).filter(pv => this._has(pv.power));
        const ids = strings.map(pv => pv.power);
        if (!ids.length) {
            this._text('pvChartStatus', 'No string power entities configured');
            return;
        }
        this._text('pvChartStatus', 'Loading history…');
        try {
            const query = `history/period/${encodeURIComponent(day.toISOString())}?filter_entity_id=${encodeURIComponent(ids.join(','))}&minimal_response&no_attributes`;
            const series = this._series(await this._hass.callApi('GET', query), ids).map((points, i) => {
                const factor = this._factor(ids[i]);
                return { points: points.map(p => ({ t: p.t, v: p.v * factor })), color: strings[i].color, name: strings[i].name };
            });
            const hasData = this._showChart(this._id('pvChart'), series, day.getTime(), false);
            this._text('pvChartStatus', hasData ? 'Midnight → now' : 'No recorded string history today');
        } catch (e) {
            this._text('pvChartStatus', 'Recorder history unavailable');
            this._id('pvChart').replaceChildren();
        }
    }
    _power(v) {
        return v === null ? '—' : formatPower(v)
    }
    _kwh(v) {
        return `${v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}\u00A0kWh`;
    }
    // Lifetime charged / discharged; discharged (V2G / V2H) is left out while it reads 0.
    _lifetime(charged, discharged) {
        return [charged === null ? '' : `Lifetime ↓\u00A0${this._kwh(charged)}`, discharged === null || discharged === 0 ? '' : `↑\u00A0${this._kwh(discharged)}`];
    }
    _energy(v) {
        return v === null ? '—' : `${v.toFixed(2)} kWh`
    }
    _cupraSocEntity(rows) {
        const matches = rows.filter(row => this.config.ev_device_id && row.device_id === this.config.ev_device_id
            && this.config.ev_config_entry_id && row.config_entry_id === this.config.ev_config_entry_id
            && row.platform === 'vag_connect' && !row.disabled_by
            && row.entity_id?.startsWith('sensor.') && row.unique_id?.endsWith('_battery_soc'));
        return matches.length === 1 ? matches[0].entity_id : null;
    }
    _renderCupraSoc() {
        const entity = this.config.entities?.ev_soc === false ? null
            : this.config.entities?.ev_soc || this._cupraSocId;
        const state = entity ? this._hass?.states[entity] : null;
        const value = state?.state;
        const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN;
        const valid = this._flowsConnected() && state?.attributes?.unit_of_measurement === '%'
            && Number.isFinite(number) && number >= 0 && number <= 100;
        this._text('evSoc', valid ? `${Number(number.toFixed(1))}%` : 'Waiting for vehicle data');
        this._id('evSoc')?.classList.toggle('cupra-wait', !valid);
    }
    _updateCupraSoc() {
        this._renderCupraSoc();
        // Registry metadata, never name matching. Retry while awaiting discovery, at
        // most once a minute; state updates themselves come from normal HA pushes.
        if (this.config.entities?.ev_soc !== undefined || this._cupraSocLookup
            || !this._flowsConnected() || !this._hass?.callWS
            || Date.now() < (this._cupraSocLookupAfter || 0)) return;
        this._cupraSocLookupAfter = Date.now() + 60000;
        this._cupraSocLookup = this._hass.callWS({ type: 'config/entity_registry/list' })
            .then(rows => {
                this._cupraSocId = this._cupraSocEntity(rows);
                if (this.root) this._renderCupraSoc();
            })
            .catch(() => { /* Keep the honest waiting label if metadata is inaccessible. */ })
            .finally(() => { this._cupraSocLookup = null; });
    }
    _hypervoltData() {
        const state = key => this._hass?.states[this._ent(key)];
        const text = key => {
            const value = state(key)?.state;
            return value && !['unknown', 'unavailable', 'none'].includes(value.toLowerCase()) ? value : null;
        };
        // HA entity availability is authoritative; unchanged numeric states do not expire.
        const powerState = state('ev_power');
        const connected = this._hass?.connected !== false && this._hass?.connection?.connected !== false;
        const powerUnit = powerState?.attributes?.unit_of_measurement;
        const value = powerState?.state;
        const numeric = value !== undefined && value !== null && String(value).trim() !== '' ? Number(value) : NaN;
        const rawPower = ['W', 'kW', 'MW'].includes(powerUnit) && Number.isFinite(numeric)
            ? numeric * ({ W: .001, kW: 1, MW: 1000 }[powerUnit]) : null;
        const power = connected && rawPower !== null && rawPower >= 0 ? rawPower : null;
        const energyUnit = state('ev_session_energy')?.attributes?.unit_of_measurement;
        const energy = ['Wh', 'kWh', 'MWh'].includes(energyUnit) ? this._scaled(this._ent('ev_session_energy')) : null;
        const plugged = text('ev_plugged');
        return {
            power,
            powerText: power === null ? 'Unavailable' : this._power(power),
            plug: plugged === 'on' ? 'Plugged in' : plugged === 'off' ? 'Unplugged' : 'Unavailable',
            status: power === null ? 'Power unavailable' : power > 0 ? 'Charging' : 'Idle',
            readiness: text('ev_readiness') ?? 'Unavailable',
            mode: text('ev_mode') ?? 'Unavailable',
            energy: energy === null || energy < 0 ? 'Unavailable' : this._energy(energy),
        };
    }
    _updateHypervolt() {
        if (!this._hass || !this.root) return;
        this._updateCupraSoc();
        const data = this._hypervoltData();
        this._text('evPower', data.powerText);
        this._text('evStatus', `${data.plug} · ${data.status}`);
        this._text('evSession', `Session ${data.energy}`);
        for (const [id, value] of Object.entries({ evPanelPower: data.powerText, evPanelPlug: data.plug,
            evPanelStatus: data.status, evPanelReady: data.readiness, evPanelMode: data.mode,
            evPanelEnergy: data.energy })) this._text(id, value);
        // EV is an AC load: only positive, available measured power animates toward the
        // car, including small nonzero readings below the general 25 W threshold.
        this._flow('evFlow', data.power, true);
        // Positive measured charging power AND a confirmed plug, never merely
        // the charging-enable switch or "plugged in" on its own.
        this._id('cupraCarGlow').classList.toggle('is-charging',
            this._flowsConnected() && data.plug === 'Plugged in' && data.power !== null && data.power >= FLOW_DEADBAND_KW);
    }
    _measuredPower(key, signed = false) {
        const entity = this._ent(key), value = this._num(entity);
        const unit = this._hass?.states[entity]?.attributes?.unit_of_measurement;
        if (value === null || !['W', 'kW', 'MW'].includes(unit)) return null;
        const power = value * ({ W: .001, kW: 1, MW: 1000 }[unit]);
        return Number.isFinite(power) && (signed || power >= 0) ? power : null;
    }
    _netGridPower(imported, exported) {
        // Both sensors are nonnegative halves of the same signed Sigenergy register.
        // Missing or contradictory halves cannot establish a direction or true zero.
        if (![imported, exported].every(v => typeof v === 'number' && Number.isFinite(v) && v >= 0)) return null;
        if (imported > 0 && exported > 0) return null;
        return imported - exported;
    }
    _flowsConnected() {
        return !!this._hass && this._hass.connected !== false && this._hass.connection?.connected !== false;
    }
    _flowSummaryText(pv, battery, grid) {
        if (!this._flowsConnected()) return 'Home Assistant disconnected · Flow animation paused';
        const batteryText = battery === null ? 'Battery unavailable' : Math.abs(battery) < FLOW_DEADBAND_KW
            ? 'Battery idle' : battery > 0 ? 'Battery charging' : 'Battery discharging';
        const solarText = pv === null ? 'Solar unavailable' : pv >= FLOW_DEADBAND_KW ? 'Solar generating' : 'Solar idle';
        const gridText = grid === null ? 'Sigenergy grid unavailable' : Math.abs(grid) < FLOW_DEADBAND_KW
            ? 'Sigenergy grid idle' : grid > 0 ? 'Sigenergy grid importing' : 'Sigenergy grid exporting';
        return `${batteryText} · ${solarText} · ${gridText}`;
    }
    _updateSchematicFlows(pv, home, battery, grid) {
        // PV: panels -> cabinet. Home: cabinet -> HOME.
        // Positive battery power means charging: cabinet bus -> internal battery.
        // Positive net grid power means import: off-canvas GRID -> cabinet.
        // Negative battery/grid power reverses only that independent path.
        this._flow('solarFlow', pv);
        this._flow('homeFlow', home);
        this._flow('batteryFlow', battery);
        this._flow('gridFlow', grid);
    }
    // Positive power runs from the start of the flow's path to its end.
    _flow(id, power, positiveOnly = false) {
        if (!this._flowStates) this._flowStates = {};
        const valid = typeof power === 'number' && Number.isFinite(power);
        const previous = this._flowStates[id];
        const state = { active: this._flowsConnected() && valid && (positiveOnly ? power > 0 : Math.abs(power) >= FLOW_DEADBAND_KW),
            reverse: !positiveOnly && valid && power < 0, kw: valid ? Math.abs(power) : 0 };
        this._flowStates[id] = state;
        // Do not leave a previous-direction pulse painted until the next RAF.
        // Same-direction power updates keep their phase; transitions reset it.
        if (!state.active || previous?.active !== state.active || previous?.reverse !== state.reverse)
            this._resetFlowAnimation(id);
        this._applyFlowClass(id);
        if (this.isConnected && !this._flowFrame) this._startFlowAnimation();
    }
    _resetFlowAnimation(id) {
        const item = this._flowItems?.find(item => item.id === id);
        if (!item) return;
        item.pos = 0;
        item.travelled = 0;
        item.comet = 0;
        item.fork = undefined;
        for (const el of [item.glow, ...item.comets]) {
            el.style.opacity = '0';
            el.setAttribute('stroke-dasharray', '0 100000');
            el.setAttribute('stroke-dashoffset', '0');
        }
    }
    _applyFlowClass(id) {
        const group = this._id(id);
        if (!group) return;
        group.classList.toggle('active', !!this._flowStates[id].active);
        group.classList.toggle('moving', !!this._flowStates[id].active && !matchMedia('(prefers-reduced-motion: reduce)').matches);
        if (!this._flowStates[id].active) {
            if (id === 'evFlow') this._id('cupraCarGlow')?.classList.toggle('is-charging', false);
            if (id === 'batteryFlow') for (const mode of ['charging', 'discharging'])
                this._id('batteryArtwork')?.classList.toggle(mode, false);
        }
    }
    connectedCallback() {
        this._installSolarScroll();
        if (!this.root) return;
        this._updateWeather();
        this._updateScene();
        if (!this._backupReserveBindings) this._wireBackupReserve();
        if (!this._sigenResize) this._observeSigenFrame();
        if (!this._chartObserver) this._observeCharts();
        if (!this._flowFrame) this._startFlowAnimation();
        this._statTimer ??= setInterval(() => { if (this._statCurrent()) this._loadStatistics(); }, 300000);
        this._historyTimer ??= setInterval(() => { if (!document.hidden) this._advanceChart(); }, 30000);
    }
    _drawFrequentFlow(item, state, dt) {
        const grid = this._flowItems.find(flow => flow.id === 'gridFlow');
        const gridLength = grid?.length || (grid?.wire.getAttribute('d') ? grid.wire.getTotalLength() : 240);
        // Cadence is visual only: never allocate grid watts to HOUSE or EV.
        // If grid is idle/unavailable, use its existing 1 kW visual reference
        // rather than suppressing an independently measured positive flow.
        const gridState = this._flowStates.gridFlow;
        const gridKw = gridState?.active ? gridState.kw : 1;
        const interval = (gridLength + Math.min(240, gridLength * .7)) / (60 + 35 * Math.sqrt(gridKw));
        const speed = 60 + 35 * Math.sqrt(state.kw);
        const spacing = speed * interval;
        const comet = Math.min(240, item.length * .7, spacing * .7);
        item.pos = (item.pos + dt * speed) % spacing;
        item.comet = comet;
        item.travelled = item.pos - comet / 2;
        const centre = state.reverse ? item.length - item.travelled : item.travelled;
        const place = (el, dash) => {
            // Every tail layer and glow has the SAME period, so the five
            // segments remain one aligned train, not independent tiny beams.
            el.setAttribute('stroke-dasharray', `${dash} ${spacing - dash}`);
            el.setAttribute('stroke-dashoffset', `${dash / 2 - centre}`);
            el.style.removeProperty('opacity');
        };
        place(item.glow, comet * .6);
        item.comets.forEach((el, i) => place(el, comet * COMET_LAYERS[i]));
    }
    _startFlowAnimation() {
        if (this._flowFrame) cancelAnimationFrame(this._flowFrame);
        let previousFrame = null;
        const draw = (now) => {
            // A callback handle is pending work, not proof that a loop survived.
            // If SVG geometry throws, the next HA update can restart the RAF.
            this._flowFrame = null;
            if (!this.isConnected) {
                this._flowFrame = null;
                return;
            }
            const dt = previousFrame === null ? 0 : Math.min(now - previousFrame, 100) / 1000;
            previousFrame = now;
            for (const item of this._flowItems) {
                const state = this._flowStates?.[item.id];
                if (state?.active && !this._flowsConnected()) {
                    state.active = false;
                    this._resetFlowAnimation(item.id);
                    this._applyFlowClass(item.id);
                    continue;
                }
                if (!state?.active || !item.wire.getAttribute('d')) continue;
                if (!item.length) {
                    const length = item.wire.getTotalLength();
                    this._addGlowFilter(item);
                    item.length = length;
                }
                if (item.id === 'evFlow' || item.id === 'homeFlow') {
                    this._drawFrequentFlow(item, state, dt);
                    continue;
                }
                let comet, travelled, fork = 0;
                if (item.parent) {
                    if (!this._flowStates[item.parent.id]?.active) continue;
                    item.parent.length ||= item.parent.wire.getTotalLength();
                    item.fork ??= this._forkDistance(item.parent, item);
                    ({ comet, travelled } = item.parent);
                    fork = item.fork;
                    travelled -= fork;
                } else {
                    comet = Math.min(240, item.length * .7);
                    item.pos = (item.pos + dt * (60 + 35 * Math.sqrt(state.kw))) % (item.length + comet);
                    travelled = item.pos - comet / 2;
                    Object.assign(item, { comet, travelled });
                }
                const centre = state.reverse ? item.length - travelled : travelled;
                const gap = item.length + 2 * comet + fork;
                const place = (el, dash) => {
                    el.setAttribute('stroke-dasharray', `${dash} ${gap}`);
                    el.setAttribute('stroke-dashoffset', `${dash / 2 - centre}`);
                    el.style.removeProperty('opacity');
                };
                place(item.glow, comet * .6);
                item.comets.forEach((el, i) => place(el, comet * COMET_LAYERS[i]));
            }
            this._flowFrame = requestAnimationFrame(draw);
        };
        this._flowFrame = requestAnimationFrame(draw);
    }
    // The blur region is fixed in scene units because a level or upright path has no area to scale from.
    _addGlowFilter(item) {
        const ns = 'http://www.w3.org/2000/svg', pad = 24, box = item.wire.getBBox();
        const filter = document.createElementNS(ns, 'filter');
        filter.id = `glow-${item.id}`;
        for (const [name, value] of Object.entries({ filterUnits: 'userSpaceOnUse', x: box.x - pad, y: box.y - pad, width: box.width + 2 * pad, height: box.height + 2 * pad }))
            filter.setAttribute(name, value);
        filter.innerHTML = '<feGaussianBlur stdDeviation="3"/>';
        this.root.querySelector('defs').append(filter);
        item.glow.setAttribute('filter', `url(#${filter.id})`);
    }
    // Distance along the parent's path to the point where the branch starts.
    _forkDistance(parent, branch) {
        const start = branch.wire.getPointAtLength(0);
        let nearest = 0, best = Infinity;
        for (let at = 0; at <= parent.length; at += .5) {
            const point = parent.wire.getPointAtLength(at);
            const distance = Math.hypot(point.x - start.x, point.y - start.y);
            if (distance < best) { best = distance; nearest = at; }
        }
        return nearest;
    }
    disconnectedCallback() {
        clearInterval(this._weatherTimer);
        this._weatherTimer = null;
        this._weatherResize?.disconnect();
        this._weatherResize = null;
        this._stopBackupReserve();
        this._stopSigenLayout();
        this._restoreSolarScroll();
        if (this._heroResize) {
            window.removeEventListener('resize', this._heroResize);
            window.visualViewport?.removeEventListener('resize', this._heroResize);
            this._heroResize = null;
        }
        if (this._fitFrame) { cancelAnimationFrame(this._fitFrame); this._fitFrame = null; }
        if (this._chartObserver) { this._chartObserver.disconnect(); this._chartObserver = null; }
        if (this._flowFrame) cancelAnimationFrame(this._flowFrame);
        this._flowFrame = null;
        clearInterval(this._statTimer);
        this._statTimer = null;
        clearInterval(this._historyTimer);
        this._historyTimer = null;
    }
    _update() {
        if (!this._hass)
            return;
        const n = x => this._num(x)
          , e = key => this._ent(key)
          , kw = key => this._scaled(this._ent(key))   // kW or kWh, whatever unit the entity reports
          , pv = this._measuredPower('pv_power')
          , home = this._measuredPower('home_power');
        const batRaw = this._measuredPower('battery_power', true), bat = batRaw === null ? null : batRaw * this._batterySign();
        const socEntity = e('battery_soc');
        const rawSoc = n(socEntity), soc = rawSoc !== null && rawSoc >= 0 && rawSoc <= 100 ? rawSoc : null;
        const imp = this._measuredPower('grid_import_power')
          , exp = this._measuredPower('grid_export_power');
        const ac = kw('ac_charger_power')
          , dc = kw('dc_charger_power');
        const hasAc = this._has(e('ac_charger_power')), hasDc = this._has(e('dc_charger_power'));
        this._id('dcGear').hidden = !this._has(e('dc_charger_limit'));
        this._id('acNode').hidden = !hasAc;
        this._id('dcNode').hidden = !hasDc;
        this._id('chargerArt').setAttribute('display', hasAc || hasDc ? 'inline' : 'none');
        this._id('legendAC').hidden = !hasAc;
        this._id('legendDC').hidden = !hasDc;
        const grid = this._netGridPower(imp, exp);
        this._text('pv', this._power(pv));
        this._text('home', this._power(home));
        this._text('battery', this._power(bat));
        this._text('soc', soc === null ? '—' : `${soc.toFixed(1)}%`);
        this._text('battStatus', bat === null ? 'Unavailable' : bat >= FLOW_DEADBAND_KW ? 'Charging' : bat <= -FLOW_DEADBAND_KW ? 'Discharging' : 'Idle');
        this._text('grid', this._power(grid));
        this._text('gridStatus', grid === null ? 'Unavailable' : grid >= FLOW_DEADBAND_KW ? 'Importing' : grid <= -FLOW_DEADBAND_KW ? 'Exporting' : 'Idle');
        // Keep measured text and flow state in the same update, before optional
        // statistics/dialog work. A later UI failure must not retain old flows.
        this._updateSchematicFlows(pv, home, bat, grid);
        this._updateHypervolt();
        this._updateBatteryArtwork(soc, bat);
        const phase = this._hass.states[e('phase_mode')]?.state;
        this._text('phaseMode', !this.config.show_phase_mode ? '' : phase === 'on' ? 'Power Per Phase Mode' : phase === 'off' ? 'Total Power Mode' : '');
        const acTotal = kw('ac_charger_total_energy');
        this._text('acTotal', !this.config.show_lifetime || acTotal === null ? '' : `${this._kwh(acTotal)} consumed`);
        this._parts('dcLifetime', !this.config.show_lifetime ? [] : this._lifetime(kw('dc_charger_lifetime_charged_energy'), kw('dc_charger_lifetime_discharged_energy')));
        this._text('ac', this._power(ac));
        this._text('dc', this._power(dc));
        // ac_charger_soc / ac_charger_range at the top level are the original keys and still work.
        const carEntity = e('ac_charger_vehicle_soc') ?? (this.config.entities?.ac_charger_vehicle_soc === undefined ? this.config.ac_charger_soc : null)
          , rangeEntity = e('ac_charger_vehicle_range') ?? (this.config.entities?.ac_charger_vehicle_range === undefined ? this.config.ac_charger_range : null);
        const car = carEntity ? n(carEntity) : null
          , range = rangeEntity ? n(rangeEntity) : null
          , dcsoc = n(e('dc_charger_vehicle_soc'));
        const acState = this._hass.states[e('ac_charger_state')]?.state?.split('-')[0];
        const carText = !carEntity ? '' : car === null ? 'Vehicle SOC unavailable' : `${car.toFixed(0)}%${range === null ? '' : ` · ${range.toFixed(0)} km`}`;
        this._parts('acSoc', [acState && !['unknown', 'unavailable'].includes(acState) ? acState : '', carText]);
        // The inverter reports 0% with no DC vehicle attached; a genuine 0% while charging is still shown.
        // Negative DC power is the vehicle supplying the house.
        const dcState = dc === null ? '' : dc > 0.025 ? 'Charging' : dc < -0.025 ? 'Discharging' : '';
        const dcSocText = dcsoc === null || (dcsoc === 0 && !dcState) ? '' : `Vehicle SOC ${dcsoc.toFixed(0)}%`;
        this._parts('dcSoc', [dcState, dcSocText]);
        for (const [el, key] of DAILY_TILES) {
            const id = e(key), tile = this._id(el).closest('.stat');
            tile.hidden = !id;
            if (!id) continue;
            this._text(el, this._energy(this._scaled(id)));
            tile.dataset.entity = id;
        }
        this._text('daySoc', soc === null ? '—' : `${soc.toFixed(1)}%`);
        this._id('socStat').dataset.entity = socEntity ?? '';
        this._id('socLevel').dataset.entity = socEntity ?? '';
        this._socEntity = socEntity;
        this._updateEnergy();
        this._text('socLarge', soc === null ? '—' : `${soc.toFixed(1)}%`);
        this._id('socFill').style.width = `${Math.min(100, Math.max(0, soc ?? 0))}%`;
        const cap = kw('battery_capacity');
        this._text('capacityText', cap === null ? '' : `${((soc ?? 0) * cap / 100).toFixed(1)} of ${cap.toFixed(1)} kWh`);
        this._updatePv();
        this._updateHome();
    }
    _updateSunTimes() {
        // Calculate sunrise and sunset for *today* from Home Assistant's location.
        // sun.sun next_rising / next_setting refer to tomorrow after today's event.
        const lat = Number(this._hass?.config?.latitude);
        const lon = Number(this._hass?.config?.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
            this._text('sunrise', 'Sunrise —');
            this._text('sunset', 'Sunset —');
            this._sunTimes = null;
            return;
        }
        // SunCalc solar equations, evaluated at local noon for the selected day.
        const now = new Date();
        const noon = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
        const rad = Math.PI / 180, dayMs = 86400000, J1970 = 2440588, J2000 = 2451545;
        const toJulian = d => d.valueOf() / dayMs - .5 + J1970;
        const fromJulian = j => new Date((j + .5 - J1970) * dayMs);
        const lw = -lon * rad, phi = lat * rad;
        const d = toJulian(noon) - J2000;
        const n = Math.round(d - .0009 - lw / (2 * Math.PI));
        const ds = .0009 + lw / (2 * Math.PI) + n;
        const M = rad * (357.5291 + .98560028 * ds);
        const C = rad * (1.9148 * Math.sin(M) + .02 * Math.sin(2 * M) + .0003 * Math.sin(3 * M));
        const L = M + C + rad * 102.9372 + Math.PI;
        const dec = Math.asin(Math.sin(L) * Math.sin(rad * 23.4397));
        const Jnoon = J2000 + ds + .0053 * Math.sin(M) - .0069 * Math.sin(2 * L);
        const cosH = (Math.sin(-.833 * rad) - Math.sin(phi) * Math.sin(dec)) / (Math.cos(phi) * Math.cos(dec));
        if (cosH < -1 || cosH > 1) {
            this._text('sunrise', 'Sunrise —');
            this._text('sunset', 'Sunset —');
            this._sunTimes = null;
            return;
        }
        const h = Math.acos(cosH);
        const Jset = J2000 + (.0009 + (h + lw) / (2 * Math.PI) + n) + .0053 * Math.sin(M) - .0069 * Math.sin(2 * L);
        const Jrise = Jnoon - (Jset - Jnoon);
        const fmt = t => t.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        this._text('sunrise', `Sunrise ${fmt(fromJulian(Jrise))}`);
        this._text('sunset', `Sunset ${fmt(fromJulian(Jset))}`);
        this._sunTimes = { rise: fromJulian(Jrise).getTime(), set: fromJulian(Jset).getTime() };
    }
    _wireStatistics() {
        const stat = this._stat ??= { range: 'Day', date: new Date(), selection: null, flows: null };
        this._id('statRange').value = stat.range;
        this._id('statRange').addEventListener('change', event => {
            Object.assign(stat, { range: event.target.value, date: new Date(), selection: null });
            this._loadStatistics();
        });
        const step = direction => {
            Object.assign(stat, { date: statStep(stat.range, stat.date, direction), selection: null });
            this._loadStatistics();
        };
        this._id('statPrev').addEventListener('click', () => step(-1));
        this._id('statNext').addEventListener('click', () => step(1));
        const chart = this._id('statChart');
        const choose = target => {
            const node = target.closest?.('[data-side]');
            if (!node) return;
            const { side } = node.dataset, index = Number(node.dataset.index);
            stat.selection = stat.selection?.side === side && stat.selection.index === index ? null : { side, index };
            this._drawStat();
        };
        chart.addEventListener('click', event => choose(event.target));
        chart.addEventListener('keydown', event => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            choose(event.target);
        });
        chart._redraw = () => this._drawStat();
        this._id('statCopy').addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(`${this._statJson}\n${this._id('statDebug').textContent}`);
                this._text('statCopied', 'Copied');
            } catch (error) {
                this._text('statCopied', 'Copy failed: use Download');
            }
        });
        this._statControls();
        this._drawStat();
    }
    _statCurrent() {
        return this._stat.range === 'Lifetime' || statWindow(this._stat.range, this._stat.date)[1] > new Date();
    }
    _statControls() {
        const { range, date } = this._stat, lifetime = range === 'Lifetime';
        this._text('statDate', statLabel(range, date));
        this._id('statPrev').disabled = lifetime;
        this._id('statNext').disabled = lifetime || statWindow(range, statStep(range, date, 1))[0] > new Date();
    }
    // Energy per interval comes from the lifetime counters' recorder statistics; each interval is split into
    // flows between sources and consumers and the splits are added up.
    async _loadStatistics() {
        const stat = this._stat;
        if (!this._hass || !stat || !this.config.show_energy_statistics) return;
        this._statControls();
        if (typeof this._hass.callWS !== 'function') {
            this._text('statStatus', 'Statistics unavailable');
            return;
        }
        // The load has no sensor: it is what the sources supply beyond the other consumers.
        const pick = defs => defs.map(def => { if (!def.key) return 'residual'; const id = this._ent(def.key); return this._has(id) ? id : null; });
        const sourceIds = pick(STAT_SOURCES), sinkIds = pick(STAT_SINKS);
        const ids = [...new Set([...sourceIds, ...sinkIds].filter(id => id && id !== 'residual'))];
        if (!ids.length) {
            stat.flows = null;
            this._drawStat();
            this._text('statStatus', 'No energy statistics sensors');
            return;
        }
        const request = this._statRequest = (this._statRequest ?? 0) + 1;
        this._text('statStatus', 'Loading…');
        const [start, end] = statWindow(stat.range, stat.date);
        const fetchStatistics = period => this._hass.callWS({ type: 'recorder/statistics_during_period', start_time: start.toISOString(), end_time: end.toISOString(), statistic_ids: ids, period, types: ['change'] });
        try {
            let period = STAT_PERIODS[stat.range], result = await fetchStatistics(period);
            if (period === '5minute' && !ids.some(id => result[id]?.length)) result = await fetchStatistics(period = 'hour');
            if (request !== this._statRequest) return;
            const buckets = new Map();
            for (const id of ids) {
                const factor = this._factor(id);
                for (const row of result[id] ?? []) {
                    const time = typeof row.start === 'number' ? row.start : Date.parse(row.start);
                    if (!buckets.has(time)) buckets.set(time, {});
                    buckets.get(time)[id] = Math.max(0, Number(row.change) * factor || 0);
                }
            }
            const flows = STAT_SOURCES.map(() => STAT_SINKS.map(() => 0)), raw = {};
            for (const values of buckets.values()) {
                const read = id => id ? values[id] ?? 0 : 0;
                for (const [id, value] of Object.entries(values)) raw[id] = (raw[id] ?? 0) + value;
                const sources = sourceIds.map(read), sinks = sinkIds.map(read), load = STAT_SINKS.findIndex(def => !def.key);
                sinks[load] = Math.max(0, sources.reduce((a, b) => a + b, 0) - sinks.reduce((a, b, j) => a + (j === load ? 0 : b), 0));
                allocateFlows(sources, sinks).forEach((row, i) => row.forEach((v, j) => { flows[i][j] += v; }));
            }
            Object.assign(stat, { flows, sourceIds, sinkIds });
            const debug = this._id('statDebug');
            debug.hidden = !this.config.debug_energy_statistics;
            debug.textContent = Object.entries(raw).map(([id, value]) => `${id}: ${value.toFixed(2)} kWh`).join('\n');
            const download = this._id('statDownload');
            this._id('statData').hidden = debug.hidden;
            if (!debug.hidden) {
                URL.revokeObjectURL(download.href);
                const data = { range: stat.range, period, start: start.toISOString(), end: end.toISOString(), ids, sourceIds, sinkIds, flows, buckets: [...buckets].sort((a, b) => a[0] - b[0]).map(([time, values]) => ({ time, ...values })) };
                this._statJson = JSON.stringify(data);
                download.href = URL.createObjectURL(new Blob([this._statJson], { type: 'application/json' }));
            }
            this._text('statStatus', buckets.size ? `Estimated split · ${{ '5minute': '5-minute', hour: 'hourly', day: 'daily' }[period]} data` : 'No recorded energy in this range');
            this._drawStat();
        } catch (error) {
            if (request !== this._statRequest) return;
            stat.flows = null;
            this._drawStat();
            this._text('statStatus', 'Statistics unavailable');
        }
    }
    _drawStat() {
        const stat = this._stat, svg = this._id('statChart');
        if (!svg || !stat) return;
        const summary = this._id('statSummary'), cards = this._id('statCards');
        const clear = () => {
            svg.replaceChildren();
            svg.removeAttribute('viewBox');
            svg.style.height = '';
            summary.hidden = true;
            cards.replaceChildren();
        };
        const { flows } = stat;
        if (!flows) return clear();
        const total = (list, i) => list === STAT_SOURCES ? flows[i].reduce((a, b) => a + b, 0) : flows.reduce((a, row) => a + row[i], 0);
        const nodes = (list, ids) => list.map((def, i) => ({ ...def, i, total: total(list, i), shown: !!ids[i] })).filter(node => node.shown);
        const left = nodes(STAT_SOURCES, stat.sourceIds), right = nodes(STAT_SINKS, stat.sinkIds);
        const sum = list => list.reduce((a, node) => a + node.total, 0);
        const most = Math.max(sum(left), sum(right));
        if (!(most > 0)) return clear();
        const kWh = v => v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const width = Math.max(280, svg.clientWidth || 600), compact = width < 520;
        const nodeWidth = compact ? 78 : 124, gap = 8, scale = ((compact ? 300 : 330) - gap * (Math.max(left.length, right.length) - 1)) / most;
        const place = list => {
            let y = 0;
            for (const node of list) {
                node.h = node.total > 0 ? Math.max(52, node.total * scale) : 22;
                node.y = y;
                y += node.h + gap;
            }
            return y - gap;
        };
        const height = Math.max(place(left), place(right)) + 4;
        const xLeft = nodeWidth, xRight = width - nodeWidth, middle = (xLeft + xRight) / 2;
        const selection = stat.selection && (stat.selection.side === 'src' ? left : right).find(node => node.i === stat.selection.index) ? stat.selection : null;
        const used = new Map(), ribbons = [];
        for (const a of left) for (const b of right) {
            const value = flows[a.i][b.i];
            if (!(value > 0)) continue;
            // Each end covers its node in proportion, so a node taller than its energy (minimum height) is still filled.
            const heightA = value / a.total * a.h, heightB = value / b.total * b.h, y0 = a.y + (used.get(a) ?? 0), y1 = b.y + (used.get(b) ?? 0);
            used.set(a, (used.get(a) ?? 0) + heightA);
            used.set(b, (used.get(b) ?? 0) + heightB);
            const related = selection && (selection.side === 'src' ? a.i === selection.index : b.i === selection.index);
            ribbons.push({ a, b, value, heightA, heightB, y0, y1, related });
        }
        const labels = ribbons.filter(r => r.related).map(r => ({ r, y: selection.side === 'src' ? r.y0 + r.heightA / 2 : r.y1 + r.heightB / 2 })).sort((p, q) => p.y - q.y);
        labels.forEach((label, i) => { if (i) label.y = Math.max(label.y, labels[i - 1].y + 13); });
        const touched = new Set(ribbons.filter(r => r.related).flatMap(r => [r.a, r.b]));
        const column = (list, side, x) => list.map(node => {
            const chosen = selection?.side === side && selection.index === node.i;
            const dim = selection && !chosen && !touched.has(node) ? ' dim' : '';
            // Opposite the selected node, a connected node shows just what it exchanged with the selection.
            const exchanged = selection && selection.side !== side && touched.has(node) ? (side === 'snk' ? flows[selection.index][node.i] : flows[node.i][selection.index]) : null;
            const value = kWh(exchanged ?? node.total), size = Math.min(15, (nodeWidth - 14) / (value.length * .6));
            const text = (exchanged ?? node.total) > 0
                ? `<text class="sktext" x="${x + 8}" y="${node.y + 15}" font-size="11" font-weight="700">${node.name}</text><text class="sktext" x="${x + 8}" y="${node.y + 32}" font-size="${size.toFixed(1)}" font-weight="700">${value}</text><text class="sktext" x="${x + 8}" y="${node.y + 45}" font-size="11">kWh</text>${selection || node.h < 76 ? '' : `<text class="sktext" x="${x + 8}" y="${node.y + node.h - 8}" font-size="11">${(node.total / sum(list) * 100).toFixed(2)}%</text>`}`
                : `<text class="sktext" x="${x + 8}" y="${node.y + 15}" font-size="${compact ? 10 : 11}" font-weight="700">${node.name} 0 kWh</text>`;
            return `<g class="skn${dim}" data-side="${side}" data-index="${node.i}" tabindex="0" role="button" aria-pressed="${chosen}" aria-label="${node.name} ${value} kWh"><rect class="skbox" x="${x}" y="${node.y}" width="${nodeWidth}" height="${node.h}" rx="6" fill="${node.color}"${chosen ? ' stroke="var(--fg)" stroke-width="2"' : ''}/>${text}</g>`;
        }).join('');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.style.height = `${height}px`;
        svg.innerHTML = `<defs>${ribbons.map(r => `<linearGradient id="skg${r.a.i}-${r.b.i}"><stop offset="0" style="stop-color:${r.a.color}"/><stop offset="1" style="stop-color:${r.b.color}"/></linearGradient>`).join('')}</defs>`
            + ribbons.map(r => `<path class="skr${selection ? (r.related ? ' on' : ' dim') : ''}" fill="url(#skg${r.a.i}-${r.b.i})" d="M${xLeft} ${r.y0} C${middle} ${r.y0} ${middle} ${r.y1} ${xRight} ${r.y1} L${xRight} ${r.y1 + r.heightB} C${middle} ${r.y1 + r.heightB} ${middle} ${r.y0 + r.heightA} ${xLeft} ${r.y0 + r.heightA} Z"/>`).join('')
            + labels.map(({ r, y }) => selection.side === 'src'
                ? `<text class="skpct" x="${xLeft + 6}" y="${y + 4}">${(r.value / r.a.total * 100).toFixed(2)}%</text>`
                : `<text class="skpct" text-anchor="end" x="${xRight - 6}" y="${y + 4}">${(r.value / r.b.total * 100).toFixed(2)}%</text>`).join('')
            + column(left, 'src', 0) + column(right, 'snk', width - nodeWidth);
        if (!selection) {
            summary.hidden = true;
            cards.replaceChildren();
            return;
        }
        const chosen = (selection.side === 'src' ? left : right).find(node => node.i === selection.index);
        const others = selection.side === 'src' ? right : left;
        summary.hidden = false;
        summary.innerHTML = `<b>${kWh(chosen.total)}</b><small>kWh</small><div>${chosen.caption}</div>`;
        cards.innerHTML = others.map(other => {
            const value = selection.side === 'src' ? flows[chosen.i][other.i] : flows[other.i][chosen.i];
            return value > 0 ? `<div class="statcard"><b>${(value / chosen.total * 100).toFixed(2)}%</b><span>${other.caption}</span><span>${kWh(value)} kWh</span></div>` : '';
        }).join('');
    }
    async _loadHistory() {
        if (!this._hass || this._loading)
            return;
        this._loading = true;
        const day = new Date();
        day.setHours(0, 0, 0, 0);
        const specs = CHART_SERIES.map(s => ({ ...s, id: this._ent(s.key) }))
            .filter(s => s.id && (!s.optional || this._has(s.id)));
        const ids = specs.map(s => s.id);
        try {
            const query = `history/period/${encodeURIComponent(day.toISOString())}?filter_entity_id=${encodeURIComponent(ids.join(','))}&minimal_response&no_attributes`;
            const response = await this._hass.callApi('GET', query);
            this._renderChart(response, specs, day.getTime());
            this._text('chartStatus', 'Midnight → now');
        } catch (e) {
            this._text('chartStatus', 'Recorder history unavailable');
            this._id('chart').replaceChildren();
        } finally {
            this._loading = false;
        }
    }
    _series(data, ids) {
        const rows = new Map((data || []).filter(a => a?.length).map(a => [a[0].entity_id, a]));
        return ids.map(id => (rows.get(id) || []).map(s => ({
            t: Date.parse(s.last_changed || s.last_updated),
            v: Number(s.state)
        })).filter(p => Number.isFinite(p.t) && Number.isFinite(p.v)));
    }
    _renderChart(data, specs, start) {
        const notes = {
            Battery: v => v > .005 ? 'charging' : v < -.005 ? 'discharging' : '',
            Grid: v => v > .005 ? 'import' : v < -.005 ? 'export' : '',
            DC: v => v > .005 ? 'charging' : v < -.005 ? 'discharging' : '',
        };
        const series = this._series(data, specs.map(s => s.id)).map((points, i) => {
            // Scale W/MW to kW, flip export, and apply battery_power_invert to the battery series.
            const k = this._chartFactor(specs[i]);
            return {
            points: k === 1 ? points : points.map(p => ({ t: p.t, v: p.v * k })),
            color: specs[i].color,
            name: specs[i].name,
            note: notes[specs[i].name]
        };
        });
        this._chartLive = { specs, series };
        if (!this._showChart(this._id('chart'), series, start, true))
            this._text('chartStatus', 'No recorded power history today');
    }
    _chartFactor(spec) {
        return this._factor(spec.id) * (spec.negate ? -1 : 1) * (spec.key === 'battery_power' ? this._batterySign() : 1);
    }
    // Extends the drawn series with the current sensor values and redraws just the chart.
    _advanceChart() {
        const live = this._chartLive;
        if (!live || !this._hass) return;
        const t = Date.now();
        live.specs.forEach((spec, i) => {
            const v = this._num(spec.id);
            if (v !== null) live.series[i].points.push({ t, v: v * this._chartFactor(spec) });
        });
        this._id('chart')._redraw?.();
    }
    // Draws now and again whenever the chart is resized; returns whether any series has data.
    _showChart(svg, series, start, signed, top) {
        svg._redraw = () => this._plot(svg, series, start, signed, top);
        svg._redraw();
        return series.some(s => s.points.length > 1);
    }
    _plot(svg, series, start, signed, top) {
        const end = Date.now();
        const width = Math.max(240, svg.clientWidth || 900), height = 170;
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.replaceChildren();
        const ns = 'http://www.w3.org/2000/svg';
        const add = (tag, attrs, text) => {
            const el = document.createElementNS(ns, tag);
            for (const [k, v] of Object.entries(attrs))
                el.setAttribute(k, v);
            if (text !== undefined)
                el.textContent = text;
            svg.append(el);
            return el;
        };
        const values = series.flatMap(s => s.points.map(p => Math.abs(p.v)));
        const max = top ?? (signed ? Math.max(1, ...values) : Math.max(1, Math.ceil(Math.max(0, ...values))));
        const left = 38, right = width - 12;
        const x = t => left + (right - left) * (t - start) / Math.max(1, end - start);
        const y = signed ? v => 78 - 65 * v / max : v => 143 - 130 * v / max;
        for (const v of signed ? [-max, 0, max] : [0, max / 2, max]) {
            add('line', { x1: left, y1: y(v), x2: right, y2: y(v), stroke: 'var(--grid)', 'stroke-dasharray': '3 5' });
            add('text', { x: 2, y: y(v) + 4, fill: 'var(--axis)', 'font-size': 10 }, v.toFixed(!signed && max < 10 && v % 1 ? 1 : 0));
        }
        for (let h = 0; h <= 24; h += 4) {
            const t = start + h * 3600000;
            if (t > end)
                break;
            add('text', { x: x(t), y: 162, fill: 'var(--axis)', 'font-size': 10, 'text-anchor': 'middle' }, `${String(h).padStart(2, '0')}:00`);
        }
        for (const { points, color } of series) {
            if (points.length < 2)
                continue;
            const d = points.map((p, j) => `${j ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
            add('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.7, 'stroke-linejoin': 'round' });
        }
        const cursor = add('g', { display: 'none' });
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('stroke', 'var(--cursor)');
        line.setAttribute('stroke-dasharray', '2 3');
        cursor.append(line);
        const dots = series.map(({ color }) => {
            const dot = document.createElementNS(ns, 'circle');
            dot.setAttribute('r', 3.5);
            dot.setAttribute('fill', color);
            dot.setAttribute('stroke', 'var(--dot-edge)');
            dot.setAttribute('stroke-width', 1.5);
            cursor.append(dot);
            return dot;
        });
        svg._chart = { series, start, end, width, left, right, x, y, top: y(max), bottom: y(signed ? -max : 0), cursor, line, dots };
        if (svg._lastHover !== undefined) this._hoverAt(svg, svg._tip, svg._lastHover);
    }
    // Linear interpolation between recorded states, matching the drawn line.
    _valueAt(points, t) {
        if (!points.length || t < points[0].t) return null;
        let lo = 0, hi = points.length - 1;
        if (t >= points[hi].t) return points[hi].v;
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (points[mid].t <= t) lo = mid; else hi = mid;
        }
        const a = points[lo], b = points[hi];
        return a.v + (b.v - a.v) * (t - a.t) / Math.max(1, b.t - a.t);
    }
    _hoverChart(svg, tip, e) {
        const chart = svg._chart;
        if (!chart) return;
        const rect = svg.getBoundingClientRect();
        const ux = (e.clientX - rect.left) * chart.width / rect.width;
        if (ux < chart.left || ux > chart.right) return this._hideHover(svg, tip);
        svg._tip = tip;
        svg._lastHover = chart.start + (ux - chart.left) / (chart.right - chart.left) * (chart.end - chart.start);
        this._hoverAt(svg, tip, svg._lastHover);
    }
    _hoverAt(svg, tip, t) {
        const chart = svg._chart;
        const px = chart.x(t);
        chart.line.setAttribute('x1', px);
        chart.line.setAttribute('x2', px);
        chart.line.setAttribute('y1', chart.top);
        chart.line.setAttribute('y2', chart.bottom);
        const rows = new Map();
        chart.series.forEach((s, i) => {
            const v = this._valueAt(s.points, t);
            chart.dots[i].setAttribute('display', v === null ? 'none' : 'inline');
            if (v === null) return;
            chart.dots[i].setAttribute('cx', px);
            chart.dots[i].setAttribute('cy', chart.y(v));
            const row = rows.get(s.name) ?? { ...s, v: 0 };
            row.v += v;
            rows.set(s.name, row);
        });
        chart.cursor.setAttribute('display', 'inline');
        const time = new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        // Series names can be PV string names from the card config, so the rows are built as text, not HTML.
        const lines = [...rows.values()].map(r => {
            const note = r.note?.(r.v);
            const line = document.createElement('div');
            const dot = Object.assign(document.createElement('i'), { className: 'dot' });
            dot.style.background = r.color;
            line.append(dot, String(r.name));
            if (note) line.append(' ', Object.assign(document.createElement('em'), { textContent: note }));
            line.append(Object.assign(document.createElement('span'), { textContent: r.unit ? `${Math.abs(r.v).toFixed(2)} ${r.unit}` : formatPower(r.v) }));
            return line;
        });
        tip.replaceChildren(Object.assign(document.createElement('b'), { textContent: time }), ...(lines.length ? lines : [Object.assign(document.createElement('em'), { textContent: 'No data' })]));
        tip.hidden = false;
        const scale = svg.getBoundingClientRect().width / chart.width;
        const cssX = px * scale;
        const fitsRight = cssX + 14 + tip.offsetWidth <= svg.clientWidth;
        tip.style.left = `${fitsRight ? cssX + 14 : Math.max(0, cssX - 14 - tip.offsetWidth)}px`;
    }
    _hideHover(svg, tip) {
        svg._lastHover = undefined;
        tip.hidden = true;
        if (svg._chart) svg._chart.cursor.setAttribute('display', 'none');
    }
}
// BEGIN ARTWORK (generated by tools/embed-artwork.py)
const ARTWORK = {
    'house_day.png': { size: [1676, 873], href: '/local/solar-dashboard/assets/placeholder.svg' },
    'house_night.png': { size: [1676, 873], href: '/local/solar-dashboard/assets/placeholder.svg' },
    'charger_day.png': { size: [22.41, 77.35], at: [446.88, 288.33], href: '/local/solar-dashboard/assets/placeholder.svg' },
    'charger_night.png': { size: [22.30, 78.75], at: [445.77, 287.84], href: '/local/solar-dashboard/assets/placeholder.svg' },
};
// END ARTWORK
if (!customElements.get('sigenergy-flow-card'))
    customElements.define('sigenergy-flow-card', SigenergyFlowCard);
window.customCards = window.customCards || [];
window.customCards.push({
    type: 'sigenergy-flow-card',
    name: 'Sigenergy Flow Card',
    description: 'Animated Sigenergy power-flow dashboard'
});
