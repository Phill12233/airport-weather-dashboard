import csv
import io
import json
import math
import re
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


# ------------------------------------------------------------
# Konfiguration
# ------------------------------------------------------------

AIRPORTS = [
    {
        "code": "FRA",
        "icao": "EDDF",
        "city": "Frankfurt",
        "latitude": 50.0379,
        "longitude": 8.5622,
    },
    {
        "code": "HAM",
        "icao": "EDDH",
        "city": "Hamburg",
        "latitude": 53.6304,
        "longitude": 9.9882,
    },
    {
        "code": "MUC",
        "icao": "EDDM",
        "city": "München",
        "latitude": 48.3538,
        "longitude": 11.7861,
    },
    {
        "code": "BER",
        "icao": "EDDB",
        "city": "Berlin Brandenburg",
        "latitude": 52.3667,
        "longitude": 13.5033,
    },
    {
        "code": "DUS",
        "icao": "EDDL",
        "city": "Düsseldorf",
        "latitude": 51.2895,
        "longitude": 6.7668,
    },
]

OUTPUT_FILE = Path("weather.json")

CDC_BASE_URL = (
    "https://opendata.dwd.de/"
    "climate_environment/CDC/"
    "observations_germany/climate/hourly/"
    "air_temperature/recent/"
)

CDC_STATION_FILE = (
    CDC_BASE_URL
    + "TU_Stundenwerte_Beschreibung_Stationen.txt"
)

MOSMIX_BASE_URL = (
    "https://opendata.dwd.de/"
    "weather/local_forecasts/mos/"
    "MOSMIX_L/single_stations/"
)

USER_AGENT = "airport-weather-dashboard/1.0"


# ------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# ------------------------------------------------------------

def download_bytes(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def download_text(url, encoding="utf-8"):
    return download_bytes(url).decode(
        encoding,
        errors="replace",
    )


def float_or_none(value):
    if value is None:
        return None

    value = str(value).strip().replace(",", ".")

    if value in {"", "-", "-999", "-999.0"}:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def round_or_none(value, digits=1):
    if value is None:
        return None

    return round(float(value), digits)


def kelvin_to_celsius(value):
    if value is None:
        return None

    return round(value - 273.15, 1)


def metres_per_second_to_kmh(value):
    if value is None:
        return None

    return round(value * 3.6, 1)


def pascal_to_hectopascal(value):
    if value is None:
        return None

    return round(value / 100.0, 1)


def utc_iso(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def distance_km(lat1, lon1, lat2, lon2):
    radius = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a),
    )

    return radius * c


# ------------------------------------------------------------
# CDC: aktuelle gemessene Temperatur
# ------------------------------------------------------------

def load_cdc_stations():
    text = download_text(
        CDC_STATION_FILE,
        encoding="latin-1",
    )

    stations = []

    for line in text.splitlines():
        if not line.strip():
            continue

        if line.lower().startswith("stations_id"):
            continue

        # DWD-Stationsdatei verwendet feste Spalten.
        # Zusätzlich werden mehrere Leerzeichen akzeptiert.
        match = re.match(
            r"^\s*(\d+)\s+"
            r"(\d{8})\s+"
            r"(\d{8})\s+"
            r"([0-9.]+)\s+"
            r"([0-9.]+)\s+"
            r"([0-9.]+)\s+"
            r"(.+?)\s+"
            r"([A-Za-zÄÖÜäöüß-]+)\s*$",
            line,
        )

        if not match:
            continue

        station_id = match.group(1).zfill(5)
        latitude = float(match.group(5))
        longitude = float(match.group(6))
        name = match.group(7).strip()

        stations.append(
            {
                "stationId": station_id,
                "latitude": latitude,
                "longitude": longitude,
                "name": name,
            }
        )

    if not stations:
        raise RuntimeError(
            "Die CDC-Stationsliste konnte nicht ausgewertet werden."
        )

    return stations


def nearest_station(airport, stations):
    candidates = []

    for station in stations:
        distance = distance_km(
            airport["latitude"],
            airport["longitude"],
            station["latitude"],
            station["longitude"],
        )

        candidates.append(
            (
                distance,
                station,
            )
        )

    candidates.sort(key=lambda item: item[0])

    distance, station = candidates[0]

    return {
        **station,
        "distanceKm": round(distance, 1),
    }


def find_cdc_zip_url(station_id):
    directory_html = download_text(
        CDC_BASE_URL,
        encoding="utf-8",
    )

    filename_pattern = re.compile(
        rf'href="([^"]*stundenwerte_TU_'
        rf'{re.escape(station_id)}'
        rf'_akt\.zip)"',
        re.IGNORECASE,
    )

    match = filename_pattern.search(directory_html)

    if not match:
        raise RuntimeError(
            f"Keine aktuelle CDC-Datei für Station "
            f"{station_id} gefunden."
        )

    filename = match.group(1)

    if filename.startswith("http"):
        return filename

    return CDC_BASE_URL + filename


def load_current_temperature(station_id):
    zip_url = find_cdc_zip_url(station_id)
    zip_data = download_bytes(zip_url)

    with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
        product_files = [
            name
            for name in archive.namelist()
            if "produkt_tu_stunde" in name.lower()
            and name.lower().endswith(".txt")
        ]

        if not product_files:
            raise RuntimeError(
                f"Keine Temperaturdatei in {zip_url} gefunden."
            )

        product_name = product_files[0]

        text = archive.read(product_name).decode(
            "latin-1",
            errors="replace",
        )

    reader = csv.DictReader(
        io.StringIO(text),
        delimiter=";",
    )

    latest = None

    for raw_row in reader:
        row = {
            key.strip(): (
                value.strip()
                if isinstance(value, str)
                else value
            )
            for key, value in raw_row.items()
            if key is not None
        }

        timestamp_text = row.get("MESS_DATUM")
        temperature = float_or_none(row.get("TT_TU"))

        if not timestamp_text or temperature is None:
            continue

        try:
            timestamp = datetime.strptime(
                timestamp_text,
                "%Y%m%d%H",
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if latest is None or timestamp > latest["time"]:
            latest = {
                "time": timestamp,
                "temperature": temperature,
            }

    if latest is None:
        raise RuntimeError(
            f"Keine gültige Temperatur für Station "
            f"{station_id} gefunden."
        )

    return {
        "temperature": round(
            latest["temperature"],
            1,
        ),
        "time": utc_iso(latest["time"]),
        "zipUrl": zip_url,
    }


# ------------------------------------------------------------
# MOSMIX: Temperaturprognose
# ------------------------------------------------------------

def local_name(tag):
    return tag.split("}")[-1]


def element_attribute(element, name):
    for key, value in element.attrib.items():
        if local_name(key) == name:
            return value

    return None


def element_text(element):
    if element is None:
        return ""

    return "".join(
        element.itertext()
    ).strip()


def find_elements_by_local_name(root, name):
    return [
        element
        for element in root.iter()
        if local_name(element.tag) == name
    ]


def parse_number_list(value):
    values = []

    for item in value.split():
        if item in {"-", ""}:
            values.append(None)
        else:
            values.append(float_or_none(item))

    return values


def get_mosmix_forecast(airport):
    icao = airport["icao"]

    kmz_url = (
        f"{MOSMIX_BASE_URL}"
        f"{icao}/kml/"
        f"MOSMIX_L_LATEST_{icao}.kmz"
    )

    kmz_data = download_bytes(kmz_url)

    with zipfile.ZipFile(io.BytesIO(kmz_data)) as archive:
        kml_files = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".kml")
        ]

        if not kml_files:
            raise RuntimeError(
                f"Keine KML-Datei für {icao} gefunden."
            )

        kml_data = archive.read(kml_files[0])

    root = ET.fromstring(kml_data)

    timestamps = []

    for element in find_elements_by_local_name(
        root,
        "TimeStep",
    ):
        value = element_text(element)

        if not value:
            continue

        try:
            timestamps.append(
                datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            )
        except ValueError:
            timestamps.append(None)

    placemarks = find_elements_by_local_name(
        root,
        "Placemark",
    )

    selected_placemark = None

    for placemark in placemarks:
        placemark_text = element_text(placemark)

        if icao in placemark_text:
            selected_placemark = placemark
            break

    if selected_placemark is None and placemarks:
        selected_placemark = placemarks[0]

    if selected_placemark is None:
        raise RuntimeError(
            f"Keine MOSMIX-Station für {icao} gefunden."
        )

    forecast_values = {}

    for forecast in find_elements_by_local_name(
        selected_placemark,
        "Forecast",
    ):
        element_name = element_attribute(
            forecast,
            "elementName",
        )

        if not element_name:
            continue

        value_elements = [
            element
            for element in forecast
            if local_name(element.tag) == "value"
        ]

        if not value_elements:
            continue

        forecast_values[element_name] = parse_number_list(
            element_text(value_elements[0])
        )

    temperatures = forecast_values.get("TTT", [])
    dewpoints = forecast_values.get("Td", [])
    wind_speeds = forecast_values.get("FF", [])
    gusts_1h = forecast_values.get("FX1", [])
    gusts_3h = forecast_values.get("FX3", [])
    wind_directions = forecast_values.get("DD", [])
    pressures = forecast_values.get("PPPP", [])
    precipitation = (
        forecast_values.get("RR1c")
        or forecast_values.get("RR1")
        or []
    )

    rows = []

    for index, timestamp in enumerate(timestamps):
        if timestamp is None:
            continue

        temperature_kelvin = (
            temperatures[index]
            if index < len(temperatures)
            else None
        )

        temperature = kelvin_to_celsius(
            temperature_kelvin
        )

        if temperature is None:
            continue

        dewpoint = (
            kelvin_to_celsius(dewpoints[index])
            if index < len(dewpoints)
            else None
        )

        wind = (
            metres_per_second_to_kmh(
                wind_speeds[index]
            )
            if index < len(wind_speeds)
            else None
        )

        gust_value = None

        if index < len(gusts_1h):
            gust_value = gusts_1h[index]

        if gust_value is None and index < len(gusts_3h):
            gust_value = gusts_3h[index]

        gust = metres_per_second_to_kmh(
            gust_value
        )

        wind_direction = (
            round_or_none(wind_directions[index], 0)
            if index < len(wind_directions)
            else None
        )

        pressure = (
            pascal_to_hectopascal(
                pressures[index]
            )
            if index < len(pressures)
            else None
        )

        precip = (
            round_or_none(
                precipitation[index],
                1,
            )
            if index < len(precipitation)
            else None
        )

        rows.append(
            {
                "time": utc_iso(timestamp),
                "timestamp": timestamp,
                "temperature": temperature,
                "dewPoint": dewpoint,
                "windKmh": wind,
                "gustKmh": gust,
                "windDirectionDegrees": wind_direction,
                "pressureHpa": pressure,
                "precipMm": precip,
            }
        )

    now = datetime.now(timezone.utc)
    forecast_end = now + timedelta(hours=24)

    next_24_hours = [
        row
        for row in rows
        if now <= row["timestamp"] <= forecast_end
    ]

    if not next_24_hours:
        raise RuntimeError(
            f"Keine 24-Stunden-Prognose für "
            f"{icao} gefunden."
        )

    minimum_row = min(
        next_24_hours,
        key=lambda row: row["temperature"],
    )

    hourly_output = []

    for row in next_24_hours:
        hourly_output.append(
            {
                key: value
                for key, value in row.items()
                if key != "timestamp"
            }
        )

    return {
        "min24h": minimum_row["temperature"],
        "min24hTime": minimum_row["time"],
        "forecastStart": next_24_hours[0]["time"],
        "forecastEnd": next_24_hours[-1]["time"],
        "hourly": hourly_output,
        "kmzUrl": kmz_url,
    }


# ------------------------------------------------------------
# Airport zusammenführen
# ------------------------------------------------------------

def create_airport_result(airport, stations):
    print(
        f"Verarbeite {airport['code']} "
        f"({airport['icao']}) ..."
    )

    result = {
        "code": airport["code"],
        "icao": airport["icao"],
        "city": airport["city"],
        "currentTemp": None,
        "currentTempTime": None,
        "min24h": None,
        "min24hTime": None,
        "gasPathCleaning": "NO DATA",
        "weatherStatus": "NO DATA",
        "measurementStation": None,
        "hourly": [],
        "errors": [],
    }

    # Tatsächlicher CDC-Messwert
    try:
        station = nearest_station(
            airport,
            stations,
        )

        measurement = load_current_temperature(
            station["stationId"]
        )

        result["currentTemp"] = measurement[
            "temperature"
        ]

        result["currentTempTime"] = measurement[
            "time"
        ]

        result["measurementStation"] = {
            "stationId": station["stationId"],
            "name": station["name"],
            "distanceKm": station["distanceKm"],
        }

    except Exception as error:
        result["errors"].append(
            f"CDC: {error}"
        )

    # MOSMIX-Prognose
    try:
        forecast = get_mosmix_forecast(
            airport
        )

        result["min24h"] = forecast["min24h"]
        result["min24hTime"] = forecast[
            "min24hTime"
        ]
        result["forecastStart"] = forecast[
            "forecastStart"
        ]
        result["forecastEnd"] = forecast[
            "forecastEnd"
        ]
        result["hourly"] = forecast["hourly"]

        minimum = result["min24h"]

        # Gas Path Cleaning:
        # Nur GO, wenn die vorhergesagte
        # Tiefsttemperatur mindestens 5 °C beträgt.
        if minimum >= 5:
            result["gasPathCleaning"] = "GO"
        else:
            result["gasPathCleaning"] = "NO GO"

        # Allgemeine Wetterampel
        if minimum < -5:
            result["weatherStatus"] = "CRITICAL"
        elif minimum < 0:
            result["weatherStatus"] = "WARNING"
        elif minimum <= 3:
            result["weatherStatus"] = "WATCH"
        else:
            result["weatherStatus"] = "SAFE"

    except Exception as error:
        result["errors"].append(
            f"MOSMIX: {error}"
        )

    return result


# ------------------------------------------------------------
# Hauptprogramm
# ------------------------------------------------------------

def main():
    generated_at = datetime.now(timezone.utc)

    output = {
        "updatedAt": utc_iso(generated_at),
        "source": (
            "Deutscher Wetterdienst "
            "CDC und MOSMIX L"
        ),
        "gasPathCleaningLimitC": 5.0,
        "airports": [],
    }

    try:
        stations = load_cdc_stations()
        print(
            f"{len(stations)} CDC-Stationen geladen."
        )
    except Exception as error:
        print(
            "CDC-Stationsliste konnte nicht "
            f"geladen werden: {error}"
        )
        stations = []

    for airport in AIRPORTS:
        try:
            result = create_airport_result(
                airport,
                stations,
            )
        except Exception as error:
            result = {
                "code": airport["code"],
                "icao": airport["icao"],
                "city": airport["city"],
                "currentTemp": None,
                "currentTempTime": None,
                "min24h": None,
                "min24hTime": None,
                "gasPathCleaning": "NO DATA",
                "weatherStatus": "NO DATA",
                "measurementStation": None,
                "hourly": [],
                "errors": [
                    str(error)
                ],
            }

        output["airports"].append(result)

    OUTPUT_FILE.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"{OUTPUT_FILE} wurde erfolgreich erzeugt."
    )

    for item in output["airports"]:
        print(
            item["code"],
            "aktuell:",
            item["currentTemp"],
            "Minimum:",
            item["min24h"],
            "Gas Path:",
            item["gasPathCleaning"],
        )


if __name__ == "__main__":
    main()
