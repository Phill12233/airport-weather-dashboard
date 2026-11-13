import csv
import io
import json
import math
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


# ============================================================
# KONFIGURATION
# ============================================================

AIRPORTS = [
    {
        "code": "FRA",
        "icao": "EDDF",
        "city": "Frankfurt",
        "wmo_id": "10637",
        "station_name": "Frankfurt/Main Flughafen",
        "latitude": 50.0379,
        "longitude": 8.5622,
    },
    {
        "code": "HAM",
        "icao": "EDDH",
        "city": "Hamburg",
        "wmo_id": "10147",
        "station_name": "Hamburg-Fuhlsbüttel",
        "latitude": 53.6304,
        "longitude": 9.9882,
    },
    {
        "code": "MUC",
        "icao": "EDDM",
        "city": "München",
        "wmo_id": "10870",
        "station_name": "München-Flughafen",
        "latitude": 48.3538,
        "longitude": 11.7861,
    },
    {
        "code": "BER",
        "icao": "EDDB",
        "city": "Berlin Brandenburg",
        "wmo_id": "10385",
        "station_name": "Berlin-Brandenburg",
        "latitude": 52.3667,
        "longitude": 13.5033,
    },
    {
        "code": "DUS",
        "icao": "EDDL",
        "city": "Düsseldorf",
        "wmo_id": "10400",
        "station_name": "Düsseldorf Flughafen",
        "latitude": 51.2895,
        "longitude": 6.7668,
    },
    {
        "code": "STR",
        "icao": "EDDS",
        "city": "Stuttgart",
        "wmo_id": "10738",
        "station_name": "Stuttgart-Echterdingen",
        "latitude": 48.6899,
        "longitude": 9.2219,
    },
]

OUTPUT_FILE = Path("weather.json")
GAS_PATH_LIMIT_C = 5.0
MAX_MEASUREMENT_AGE_HOURS = 3

USER_AGENT = "airport-weather-dashboard/3.1 (GitHub Actions; DWD Open Data)"

DWD_POI_URL_TEMPLATE = (
    "https://opendata.dwd.de/weather/weather_reports/poi/{wmo_id}-BEOB.csv"
)

DWD_MOSMIX_SINGLE_URL_TEMPLATE = (
    "https://opendata.dwd.de/weather/local_forecasts/mos/"
    "MOSMIX_L/single_stations/{wmo_id}/kml/MOSMIX_L_LATEST_{wmo_id}.kmz"
)


# ============================================================
# HTTP & HILFSFUNKTIONEN
# ============================================================

def download_bytes(url, timeout=60):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download_text(url, encoding="utf-8", timeout=60):
    return download_bytes(url, timeout=timeout).decode(encoding, errors="replace")


def float_or_none(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if text in {"", "-", "---", "-999", "-999.0", "-9999", "-9999.0"}:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def round_or_none(value, digits=1):
    return None if value is None else round(float(value), digits)


def kelvin_to_celsius(value):
    return None if value is None else round(value - 273.15, 1)


def metres_per_second_to_kmh(value):
    return None if value is None else round(value * 3.6, 1)


def pascal_to_hectopascal(value):
    return None if value is None else round(value / 100.0, 1)


def utc_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_name(tag):
    return tag.split("}")[-1]


def element_text(element):
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def element_attribute(element, requested_name):
    for key, value in element.attrib.items():
        if local_name(key) == requested_name:
            return value
    return None


def find_elements(root, requested_name):
    return [el for el in root.iter() if local_name(el.tag) == requested_name]


def parse_number_list(text):
    return [float_or_none(item) for item in text.split()]


def series_value(series, index):
    return None if index >= len(series) else series[index]


# ============================================================
# AKTUELLE MESSWERTE (DWD POI ECHTZEIT-BEOBACHTUNGEN)
# ============================================================

def get_current_temperature_dwd_poi(airport):
    url = DWD_POI_URL_TEMPLATE.format(wmo_id=airport["wmo_id"])
    csv_text = download_text(url, encoding="utf-8")

    lines = [line for line in csv_text.splitlines() if line.strip()]
    if len(lines) < 4:
        raise RuntimeError("POI-CSV enthält keine Datenzeilen.")

    # DWD POI-CSV hat 3 Header-Zeilen:
    # Zeile 0: Parameter-Kürzel (z. B. TTT für Lufttemperatur 2m in °C)
    # Zeile 1: Einheiten
    # Zeile 2: Beschreibungen
    # Zeile 3+: Messwerte (neueste oben)
    headers = [h.strip() for h in lines[0].split(";")]
    reader = csv.DictReader(lines[3:], fieldnames=headers, delimiter=";")

    latest = None

    for row in reader:
        date_str = (row.get("Parameter") or row.get("Datum") or "").strip()
        # In DWD POI-Dateien stehen Datum und Uhrzeit (UTC) in den ersten beiden Spalten
        raw_cols = [c.strip() for c in lines[3].split(";")] if not date_str else None
        
        # Sicherer Zugriff über Spaltenindex: Spalte 0 = Datum (TT.MM.JJ), Spalte 1 = Uhrzeit (HH:MM)
        row_values = list(row.values())
        if len(row_values) < 3:
            continue

        d_str = (row_values[0] or "").strip()
        t_str = (row_values[1] or "").strip()
        temp_val = float_or_none(row.get("TTT"))

        if not d_str or not t_str or temp_val is None:
            continue

        try:
            timestamp = datetime.strptime(
                f"{d_str} {t_str}", "%d.%m.%y %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if latest is None or timestamp > latest["timestamp"]:
            latest = {
                "timestamp": timestamp,
                "temperature": round(temp_val, 1),
            }

    if latest is None:
        raise RuntimeError("Kein gültiger TTT-Messwert in POI-Datei.")

    age_hours = (
        datetime.now(timezone.utc) - latest["timestamp"]
    ).total_seconds() / 3600

    if age_hours > MAX_MEASUREMENT_AGE_HOURS:
        raise RuntimeError(
            f"POI-Messwert ist {round(age_hours, 1)} h alt (Max: {MAX_MEASUREMENT_AGE_HOURS} h)."
        )

    return {
        "temperature": latest["temperature"],
        "time": utc_iso(latest["timestamp"]),
        "ageHours": round(age_hours, 1),
        "station": {
            "stationId": airport["wmo_id"],
            "name": airport["station_name"],
            "distanceKm": 0.0,
        },
    }


# ============================================================
# MOSMIX-L EINZELSTATION LADEN & AUSLESEN
# ============================================================

def parse_forecast_series(placemark):
    forecast_values = {}
    for forecast in find_elements(placemark, "Forecast"):
        parameter_name = element_attribute(forecast, "elementName")
        if not parameter_name:
            continue
        value_elements = find_elements(forecast, "value")
        if not value_elements:
            continue
        forecast_values[parameter_name] = parse_number_list(
            element_text(value_elements[0])
        )
    return forecast_values


def create_mosmix_forecast_single(airport):
    url = DWD_MOSMIX_SINGLE_URL_TEMPLATE.format(wmo_id=airport["wmo_id"])
    kmz_data = download_bytes(url)

    with zipfile.ZipFile(io.BytesIO(kmz_data)) as archive:
        kml_files = [
            f for f in archive.namelist() if f.lower().endswith(".kml")
        ]
        if not kml_files:
            raise RuntimeError("MOSMIX-KMZ enthält keine KML.")
        kml_data = archive.read(kml_files[0])

    root = ET.fromstring(kml_data)

    timestamps = []
    for element in find_elements(root, "TimeStep"):
        text = element_text(element)
        if not text:
            timestamps.append(None)
            continue
        try:
            ts = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
            timestamps.append(ts)
        except ValueError:
            timestamps.append(None)

    placemarks = find_elements(root, "Placemark")
    if not placemarks:
        raise RuntimeError("MOSMIX-Einzelstation enthält keinen Placemark.")

    placemark = placemarks[0]
    series = parse_forecast_series(placemark)
    temperatures = series.get("TTT", [])
    if not temperatures:
        raise RuntimeError("Temperaturparameter TTT fehlt in MOSMIX.")

    dewpoints = series.get("Td", [])
    winds = series.get("FF", [])
    directions = series.get("DD", [])
    gusts_1h = series.get("FX1", [])
    gusts_3h = series.get("FX3", [])
    pressures = series.get("PPPP", [])
    precipitation = series.get("RR1c") or series.get("RR1") or []

    rows = []
    for index, timestamp in enumerate(timestamps):
        if timestamp is None:
            continue

        temperature = kelvin_to_celsius(series_value(temperatures, index))
        if temperature is None:
            continue

        gust_value = series_value(gusts_1h, index)
        if gust_value is None:
            gust_value = series_value(gusts_3h, index)

        rows.append(
            {
                "timestamp": timestamp,
                "time": utc_iso(timestamp),
                "temperature": temperature,
                "dewPoint": kelvin_to_celsius(series_value(dewpoints, index)),
                "windKmh": metres_per_second_to_kmh(series_value(winds, index)),
                "gustKmh": metres_per_second_to_kmh(gust_value),
                "windDirectionDegrees": round_or_none(
                    series_value(directions, index), 0
                ),
                "pressureHpa": pascal_to_hectopascal(
                    series_value(pressures, index)
                ),
                "precipMm": round_or_none(series_value(precipitation, index), 1),
            }
        )

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=24)

    next_24_hours = [
        row for row in rows if now <= row["timestamp"] <= end
    ]

    if not next_24_hours:
        raise RuntimeError("Keine Forecastwerte für die nächsten 24 Stunden.")

    minimum_row = min(next_24_hours, key=lambda row: row["temperature"])

    hourly = [
        {k: v for k, v in row.items() if k != "timestamp"}
        for row in next_24_hours
    ]

    return {
        "min24h": minimum_row["temperature"],
        "min24hTime": minimum_row["time"],
        "forecastStart": next_24_hours[0]["time"],
        "forecastEnd": next_24_hours[-1]["time"],
        "hourly": hourly,
        "mosmixStation": {
            "id": f"{airport['wmo_id']} ({airport['icao']})",
            "distanceKm": 0.0,
            "latitude": airport["latitude"],
            "longitude": airport["longitude"],
        },
    }


# ============================================================
# FALLBACK: OPEN-METEO DWD ICON-D2 (FALLS DWD SERVER DOWN)
# ============================================================

def fetch_open_meteo_fallback(airport):
    url = (
        f"https://api.open-meteo.com/v1/dwd-icon"
        f"?latitude={airport['latitude']}&longitude={airport['longitude']}"
        f"&current=temperature_2m&hourly=temperature_2m,dew_point_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m,surface_pressure,precipitation"
        f"&forecast_days=2&timezone=UTC"
    )
    data = json.loads(download_text(url))
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=24)

    cur_temp = round(float(data["current"]["temperature_2m"]), 1)
    cur_time = datetime.fromisoformat(
        data["current"]["time"] + ":00+00:00"
    )

    hourly_data = data["hourly"]
    next_24_hours = []
    for i, t_str in enumerate(hourly_data["time"]):
        ts = datetime.fromisoformat(t_str + ":00+00:00")
        if now <= ts <= end:
            temp = hourly_data["temperature_2m"][i]
            if temp is not None:
                next_24_hours.append(
                    {
                        "timestamp": ts,
                        "time": utc_iso(ts),
                        "temperature": round(float(temp), 1),
                        "dewPoint": round_or_none(hourly_data["dew_point_2m"][i]),
                        "windKmh": round_or_none(hourly_data["wind_speed_10m"][i]),
                        "gustKmh": round_or_none(hourly_data["wind_gusts_10m"][i]),
                        "windDirectionDegrees": round_or_none(
                            hourly_data["wind_direction_10m"][i], 0
                        ),
                        "pressureHpa": round_or_none(
                            hourly_data["surface_pressure"][i]
                        ),
                        "precipMm": round_or_none(hourly_data["precipitation"][i]),
                    }
                )

    minimum_row = min(next_24_hours, key=lambda r: r["temperature"])
    hourly_clean = [
        {k: v for k, v in row.items() if k != "timestamp"}
        for row in next_24_hours
    ]

    return {
        "measurement": {
            "temperature": cur_temp,
            "time": utc_iso(cur_time),
            "ageHours": round((now - cur_time).total_seconds() / 3600, 1),
            "station": {
                "stationId": airport["wmo_id"],
                "name": f"{airport['station_name']} (ICON-D2)",
                "distanceKm": 0.0,
            },
        },
        "forecast": {
            "min24h": minimum_row["temperature"],
            "min24hTime": minimum_row["time"],
            "forecastStart": next_24_hours[0]["time"],
            "forecastEnd": next_24_hours[-1]["time"],
            "hourly": hourly_clean,
            "mosmixStation": {
                "id": f"{airport['wmo_id']} (ICON-D2)",
                "distanceKm": 0.0,
                "latitude": airport["latitude"],
                "longitude": airport["longitude"],
            },
        },
    }


# ============================================================
# STATUSLOGIK
# ============================================================

def get_weather_status(minimum):
    if minimum is None:
        return "NO DATA"
    if minimum < -5:
        return "CRITICAL"
    if minimum < 0:
        return "WARNING"
    if minimum <= 3:
        return "WATCH"
    return "SAFE"


def get_gas_path_status(minimum):
    if minimum is None:
        return "NO DATA"
    if minimum >= GAS_PATH_LIMIT_C:
        return "GO"
    return "NO GO"


# ============================================================
# AIRPORT-ERGEBNIS
# ============================================================

def empty_airport_result(airport):
    return {
        "code": airport["code"],
        "icao": airport["icao"],
        "city": airport["city"],
        "currentTemp": None,
        "currentTempTime": None,
        "currentTempAgeHours": None,
        "min24h": None,
        "min24hTime": None,
        "forecastStart": None,
        "forecastEnd": None,
        "gasPathCleaning": "NO DATA",
        "weatherStatus": "NO DATA",
        "measurementStation": None,
        "mosmixStation": None,
        "hourly": [],
        "errors": [],
    }


def create_airport_result(airport):
    print("\n" + "=" * 60)
    print(f"Verarbeite {airport['code']} ({airport['icao']}) - WMO {airport['wmo_id']}")

    result = empty_airport_result(airport)
    fallback_data = None

    # 1. Aktuelle Messung (DWD POI)
    try:
        measurement = get_current_temperature_dwd_poi(airport)
    except Exception as error:
        print(f"DWD POI Warnung ({airport['code']}): {error} -> Nutze ICON-D2 Fallback")
        fallback_data = fetch_open_meteo_fallback(airport)
        measurement = fallback_data["measurement"]

    result["currentTemp"] = measurement["temperature"]
    result["currentTempTime"] = measurement["time"]
    result["currentTempAgeHours"] = measurement["ageHours"]
    result["measurementStation"] = measurement["station"]
    print(f"Aktuell: {result['currentTemp']} °C ({result['measurementStation']['name']})")

    # 2. 24h-Prognose (DWD MOSMIX_L Einzelstation)
    try:
        forecast = create_mosmix_forecast_single(airport)
    except Exception as error:
        print(f"DWD MOSMIX Warnung ({airport['code']}): {error} -> Nutze ICON-D2 Fallback")
        if fallback_data is None:
            fallback_data = fetch_open_meteo_fallback(airport)
        forecast = fallback_data["forecast"]

    result["min24h"] = forecast["min24h"]
    result["min24hTime"] = forecast["min24hTime"]
    result["forecastStart"] = forecast["forecastStart"]
    result["forecastEnd"] = forecast["forecastEnd"]
    result["hourly"] = forecast["hourly"]
    result["mosmixStation"] = forecast["mosmixStation"]
    result["weatherStatus"] = get_weather_status(result["min24h"])
    result["gasPathCleaning"] = get_gas_path_status(result["min24h"])

    print(
        f"Prognose Min 24h: {result['min24h']} °C um {result['min24hTime']} "
        f"-> Status: {result['weatherStatus']} | Gas Path: {result['gasPathCleaning']}"
    )

    return result


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():
    generated_at = datetime.now(timezone.utc)

    output = {
        "updatedAt": utc_iso(generated_at),
        "source": "Deutscher Wetterdienst POI und MOSMIX_L",
        "gasPathCleaningLimitC": GAS_PATH_LIMIT_C,
        "maximumMeasurementAgeHours": MAX_MEASUREMENT_AGE_HOURS,
        "airports": [],
    }

    for airport in AIRPORTS:
        try:
            result = create_airport_result(airport)
        except Exception as error:
            result = empty_airport_result(airport)
            result["errors"].append(f"Allgemein: {error}")
            print(f"Fehler bei {airport['code']}: {error}")

        output["airports"].append(result)

    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print(f"{OUTPUT_FILE} wurde erfolgreich erzeugt.")


if __name__ == "__main__":
    main()
