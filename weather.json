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


# ============================================================
# KONFIGURATION
# ============================================================

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
    {
        "code": "STR",
        "icao": "EDDS",
        "city": "Stuttgart",
        "latitude": 48.6899,
        "longitude": 9.2219,
    },
]


OUTPUT_FILE = Path("weather.json")

GAS_PATH_LIMIT_C = 5.0

# Ein CDC-Messwert wird nur dann als aktuell übernommen,
# wenn er nicht älter als drei Stunden ist.
MAX_MEASUREMENT_AGE_HOURS = 3

# Maximale Entfernung für einen automatischen MOSMIX-Punkt.
# Bei den genannten Airports sollte der Abstand deutlich
# darunter liegen.
MAX_MOSMIX_DISTANCE_KM = 50

USER_AGENT = (
    "airport-weather-dashboard/3.0 "
    "(GitHub Actions; DWD Open Data)"
)


CDC_BASE_URL = (
    "https://opendata.dwd.de/"
    "climate_environment/CDC/"
    "observations_germany/climate/hourly/"
    "air_temperature/recent/"
)

CDC_STATION_FILE_URL = (
    CDC_BASE_URL
    + "TU_Stundenwerte_Beschreibung_Stationen.txt"
)


# Eine gemeinsame Datei mit allen MOSMIX-L-Punkten.
# Dadurch müssen keine möglicherweise falschen
# Stationskennungen fest programmiert werden.
MOSMIX_ALL_STATIONS_URL = (
    "https://opendata.dwd.de/"
    "weather/local_forecasts/mos/"
    "MOSMIX_L/all_stations/kml/"
    "MOSMIX_L_LATEST_240.kmz"
)


# ============================================================
# HTTP
# ============================================================

def download_bytes(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=180,
    ) as response:
        return response.read()


def download_text(url, encoding="utf-8"):
    return download_bytes(url).decode(
        encoding,
        errors="replace",
    )


# ============================================================
# ALLGEMEINE HILFSFUNKTIONEN
# ============================================================

def float_or_none(value):
    if value is None:
        return None

    text = str(value).strip()
    text = text.replace(",", ".")

    if text in {
        "",
        "-",
        "-999",
        "-999.0",
        "-9999",
        "-9999.0",
    }:
        return None

    try:
        number = float(text)

        if not math.isfinite(number):
            return None

        return number

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
        value = value.replace(
            tzinfo=timezone.utc
        )

    value = value.astimezone(timezone.utc)

    return (
        value
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

    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )

    central_angle = 2 * math.atan2(
        math.sqrt(value),
        math.sqrt(1 - value),
    )

    return radius * central_angle


def local_name(tag):
    return tag.split("}")[-1]


def element_text(element):
    if element is None:
        return ""

    return "".join(
        element.itertext()
    ).strip()


def element_attribute(element, requested_name):
    for key, value in element.attrib.items():
        if local_name(key) == requested_name:
            return value

    return None


def find_elements(root, requested_name):
    return [
        element
        for element in root.iter()
        if local_name(element.tag)
        == requested_name
    ]


def parse_number_list(text):
    values = []

    for item in text.split():
        values.append(
            float_or_none(item)
        )

    return values


def series_value(series, index):
    if index >= len(series):
        return None

    return series[index]


# ============================================================
# CDC-VERZEICHNIS UND STATIONEN
# ============================================================

def load_cdc_zip_mapping():
    directory_html = download_text(
        CDC_BASE_URL
    )

    mapping = {}

    pattern = re.compile(
        r'href="([^"]*stundenwerte_TU_'
        r'(\d{5})'
        r'_akt\.zip)"',
        re.IGNORECASE,
    )

    for match in pattern.finditer(
        directory_html
    ):
        filename = match.group(1)
        station_id = match.group(2)

        if filename.startswith("http"):
            url = filename
        else:
            url = CDC_BASE_URL + filename

        mapping[station_id] = url

    if not mapping:
        raise RuntimeError(
            "Keine CDC-Temperaturdateien gefunden."
        )

    return mapping


def load_cdc_stations(zip_mapping):
    station_text = download_text(
        CDC_STATION_FILE_URL,
        encoding="latin-1",
    )

    stations = []

    for line in station_text.splitlines():
        if not line.strip():
            continue

        if line.lower().startswith(
            "stations_id"
        ):
            continue

        # Nur die ersten sechs strukturierten Felder
        # werden über Split gelesen. Dadurch kann die
        # uneinheitliche letzte Metadatenspalte den
        # Parser nicht mehr beschädigen.
        parts = line.split()

        if len(parts) < 7:
            continue

        station_id_raw = parts[0]

        if not station_id_raw.isdigit():
            continue

        station_id = station_id_raw.zfill(5)

        latitude = float_or_none(parts[4])
        longitude = float_or_none(parts[5])

        if latitude is None or longitude is None:
            continue

        if station_id not in zip_mapping:
            continue

        # Stationsname aus dem Rest der Zeile.
        # Bundesland und zusätzliche Kennzeichen werden
        # absichtlich nicht mehr verarbeitet.
        remaining = parts[6:]

        station_name = " ".join(
            remaining
        ).strip()

        if not station_name:
            station_name = (
                "DWD-Station " + station_id
            )

        stations.append(
            {
                "stationId": station_id,
                "latitude": latitude,
                "longitude": longitude,
                "name": station_name,
                "zipUrl": zip_mapping[
                    station_id
                ],
            }
        )

    if not stations:
        raise RuntimeError(
            "Keine verwendbaren CDC-Stationen gefunden."
        )

    return stations


def nearest_cdc_stations(
    airport,
    stations,
    maximum_candidates=15,
):
    candidates = []

    for station in stations:
        distance = distance_km(
            airport["latitude"],
            airport["longitude"],
            station["latitude"],
            station["longitude"],
        )

        candidates.append(
            {
                **station,
                "distanceKm": round(
                    distance,
                    1,
                ),
            }
        )

    candidates.sort(
        key=lambda station:
            station["distanceKm"]
    )

    return candidates[:maximum_candidates]


def read_latest_cdc_value(station):
    zip_data = download_bytes(
        station["zipUrl"]
    )

    with zipfile.ZipFile(
        io.BytesIO(zip_data)
    ) as archive:

        product_files = [
            filename
            for filename in archive.namelist()
            if filename.lower().endswith(
                ".txt"
            )
            and "produkt_tu_stunde"
            in filename.lower()
        ]

        if not product_files:
            raise RuntimeError(
                "Temperaturdatei fehlt im ZIP."
            )

        product_text = (
            archive
            .read(product_files[0])
            .decode(
                "latin-1",
                errors="replace",
            )
        )

    reader = csv.DictReader(
        io.StringIO(product_text),
        delimiter=";",
    )

    latest = None

    for raw_row in reader:
        row = {}

        for key, value in raw_row.items():
            if key is None:
                continue

            row[key.strip()] = (
                value.strip()
                if isinstance(value, str)
                else value
            )

        timestamp_text = row.get(
            "MESS_DATUM"
        )

        temperature = float_or_none(
            row.get("TT_TU")
        )

        if not timestamp_text:
            continue

        if temperature is None:
            continue

        try:
            timestamp = datetime.strptime(
                timestamp_text,
                "%Y%m%d%H",
            ).replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

        if (
            latest is None
            or timestamp > latest["timestamp"]
        ):
            latest = {
                "timestamp": timestamp,
                "temperature": temperature,
            }

    if latest is None:
        raise RuntimeError(
            "Keine gültige Temperaturmessung."
        )

    age_hours = (
        datetime.now(timezone.utc)
        - latest["timestamp"]
    ).total_seconds() / 3600

    return {
        "temperature": round(
            latest["temperature"],
            1,
        ),
        "time": utc_iso(
            latest["timestamp"]
        ),
        "ageHours": round(
            age_hours,
            1,
        ),
    }


def get_current_temperature(
    airport,
    stations,
):
    candidates = nearest_cdc_stations(
        airport,
        stations,
    )

    errors = []

    for station in candidates:
        try:
            measurement = (
                read_latest_cdc_value(
                    station
                )
            )

            # Veraltete Werte werden nicht als
            # aktuelle Messung übernommen.
            if (
                measurement["ageHours"]
                > MAX_MEASUREMENT_AGE_HOURS
            ):
                errors.append(
                    f"{station['stationId']} "
                    f"{station['name']}: "
                    f"Messwert "
                    f"{measurement['ageHours']} h alt"
                )
                continue

            return {
                "temperature":
                    measurement["temperature"],

                "time":
                    measurement["time"],

                "ageHours":
                    measurement["ageHours"],

                "station": {
                    "stationId":
                        station["stationId"],

                    "name":
                        station["name"],

                    "distanceKm":
                        station["distanceKm"],
                },
            }

        except Exception as error:
            errors.append(
                f"{station['stationId']}: {error}"
            )

    raise RuntimeError(
        "Keine aktuelle CDC-Messung innerhalb "
        f"von {MAX_MEASUREMENT_AGE_HOURS} Stunden. "
        + " | ".join(errors[:4])
    )


# ============================================================
# MOSMIX-DATEI EINMALIG LADEN
# ============================================================

def load_mosmix_document():
    print(
        "Lade gemeinsame MOSMIX-L-Datei ..."
    )

    kmz_data = download_bytes(
        MOSMIX_ALL_STATIONS_URL
    )

    with zipfile.ZipFile(
        io.BytesIO(kmz_data)
    ) as archive:

        kml_files = [
            filename
            for filename in archive.namelist()
            if filename.lower().endswith(
                ".kml"
            )
        ]

        if not kml_files:
            raise RuntimeError(
                "MOSMIX-KMZ enthält keine KML."
            )

        kml_data = archive.read(
            kml_files[0]
        )

    root = ET.fromstring(kml_data)

    timestamps = []

    for element in find_elements(
        root,
        "TimeStep",
    ):
        text = element_text(element)

        if not text:
            timestamps.append(None)
            continue

        try:
            timestamp = datetime.fromisoformat(
                text.replace(
                    "Z",
                    "+00:00",
                )
            ).astimezone(
                timezone.utc
            )

            timestamps.append(timestamp)

        except ValueError:
            timestamps.append(None)

    if not timestamps:
        raise RuntimeError(
            "MOSMIX enthält keine Zeitachse."
        )

    placemarks = find_elements(
        root,
        "Placemark",
    )

    if not placemarks:
        raise RuntimeError(
            "MOSMIX enthält keine Prognosepunkte."
        )

    print(
        len(placemarks),
        "MOSMIX-Prognosepunkte geladen."
    )

    return {
        "timestamps": timestamps,
        "placemarks": placemarks,
    }


def placemark_coordinates(placemark):
    coordinate_elements = (
        find_elements(
            placemark,
            "coordinates",
        )
    )

    if not coordinate_elements:
        return None

    coordinate_text = element_text(
        coordinate_elements[0]
    )

    if not coordinate_text:
        return None

    first_coordinate = (
        coordinate_text
        .strip()
        .split()[0]
    )

    parts = first_coordinate.split(",")

    if len(parts) < 2:
        return None

    longitude = float_or_none(parts[0])
    latitude = float_or_none(parts[1])

    if latitude is None or longitude is None:
        return None

    return {
        "latitude": latitude,
        "longitude": longitude,
    }


def placemark_name(placemark):
    names = find_elements(
        placemark,
        "name",
    )

    if not names:
        return "Unbekannter MOSMIX-Punkt"

    return element_text(names[0])


def nearest_mosmix_placemark(
    airport,
    placemarks,
):
    candidates = []

    for placemark in placemarks:
        coordinates = placemark_coordinates(
            placemark
        )

        if coordinates is None:
            continue

        distance = distance_km(
            airport["latitude"],
            airport["longitude"],
            coordinates["latitude"],
            coordinates["longitude"],
        )

        candidates.append(
            {
                "placemark": placemark,
                "distanceKm": distance,
                "latitude":
                    coordinates["latitude"],
                "longitude":
                    coordinates["longitude"],
                "name":
                    placemark_name(placemark),
            }
        )

    if not candidates:
        raise RuntimeError(
            "Kein MOSMIX-Punkt mit Koordinaten."
        )

    candidates.sort(
        key=lambda item:
            item["distanceKm"]
    )

    selected = candidates[0]

    if (
        selected["distanceKm"]
        > MAX_MOSMIX_DISTANCE_KM
    ):
        raise RuntimeError(
            "Nächster MOSMIX-Punkt ist "
            f"{selected['distanceKm']:.1f} km "
            "entfernt."
        )

    selected["distanceKm"] = round(
        selected["distanceKm"],
        1,
    )

    return selected


# ============================================================
# MOSMIX-PARAMETER AUSLESEN
# ============================================================

def parse_forecast_series(placemark):
    forecast_values = {}

    for forecast in find_elements(
        placemark,
        "Forecast",
    ):
        parameter_name = (
            element_attribute(
                forecast,
                "elementName",
            )
        )

        if not parameter_name:
            continue

        value_elements = find_elements(
            forecast,
            "value",
        )

        if not value_elements:
            continue

        forecast_values[
            parameter_name
        ] = parse_number_list(
            element_text(
                value_elements[0]
            )
        )

    return forecast_values


def create_mosmix_forecast(
    airport,
    mosmix_document,
):
    selected = nearest_mosmix_placemark(
        airport,
        mosmix_document["placemarks"],
    )

    series = parse_forecast_series(
        selected["placemark"]
    )

    temperatures = series.get(
        "TTT",
        [],
    )

    if not temperatures:
        raise RuntimeError(
            "Temperaturparameter TTT fehlt."
        )

    dewpoints = series.get("Td", [])
    winds = series.get("FF", [])
    directions = series.get("DD", [])
    gusts_1h = series.get("FX1", [])
    gusts_3h = series.get("FX3", [])
    pressures = series.get("PPPP", [])

    precipitation = (
        series.get("RR1c")
        or series.get("RR1")
        or []
    )

    rows = []

    for index, timestamp in enumerate(
        mosmix_document["timestamps"]
    ):
        if timestamp is None:
            continue

        temperature = kelvin_to_celsius(
            series_value(
                temperatures,
                index,
            )
        )

        if temperature is None:
            continue

        gust_value = series_value(
            gusts_1h,
            index,
        )

        if gust_value is None:
            gust_value = series_value(
                gusts_3h,
                index,
            )

        rows.append(
            {
                "timestamp": timestamp,

                "time": utc_iso(timestamp),

                "temperature": temperature,

                "dewPoint":
                    kelvin_to_celsius(
                        series_value(
                            dewpoints,
                            index,
                        )
                    ),

                "windKmh":
                    metres_per_second_to_kmh(
                        series_value(
                            winds,
                            index,
                        )
                    ),

                "gustKmh":
                    metres_per_second_to_kmh(
                        gust_value
                    ),

                "windDirectionDegrees":
                    round_or_none(
                        series_value(
                            directions,
                            index,
                        ),
                        0,
                    ),

                "pressureHpa":
                    pascal_to_hectopascal(
                        series_value(
                            pressures,
                            index,
                        )
                    ),

                "precipMm":
                    round_or_none(
                        series_value(
                            precipitation,
                            index,
                        ),
                        1,
                    ),
            }
        )

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=24)

    next_24_hours = [
        row
        for row in rows
        if (
            row["timestamp"] >= now
            and row["timestamp"] <= end
        )
    ]

    if not next_24_hours:
        raise RuntimeError(
            "Keine Forecastwerte für die "
            "nächsten 24 Stunden."
        )

    minimum_row = min(
        next_24_hours,
        key=lambda row:
            row["temperature"],
    )

    hourly = []

    for row in next_24_hours:
        hourly.append(
            {
                key: value
                for key, value in row.items()
                if key != "timestamp"
            }
        )

    return {
        "min24h":
            minimum_row["temperature"],

        "min24hTime":
            minimum_row["time"],

        "forecastStart":
            next_24_hours[0]["time"],

        "forecastEnd":
            next_24_hours[-1]["time"],

        "hourly":
            hourly,

        "mosmixStation": {
            "id": selected["name"],
            "distanceKm":
                selected["distanceKm"],
            "latitude":
                selected["latitude"],
            "longitude":
                selected["longitude"],
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


def create_airport_result(
    airport,
    cdc_stations,
    mosmix_document,
):
    print("")
    print("=" * 60)
    print(
        f"Verarbeite {airport['code']} "
        f"({airport['icao']})"
    )

    result = empty_airport_result(
        airport
    )

    # CDC-Messung
    try:
        measurement = (
            get_current_temperature(
                airport,
                cdc_stations,
            )
        )

        result["currentTemp"] = (
            measurement["temperature"]
        )

        result["currentTempTime"] = (
            measurement["time"]
        )

        result["currentTempAgeHours"] = (
            measurement["ageHours"]
        )

        result["measurementStation"] = (
            measurement["station"]
        )

        print(
            "CDC:",
            result["currentTemp"],
            "°C |",
            result["measurementStation"]["name"],
        )

    except Exception as error:
        message = f"CDC: {error}"
        result["errors"].append(message)
        print(message)

    # MOSMIX-Prognose
    try:
        forecast = create_mosmix_forecast(
            airport,
            mosmix_document,
        )

        result["min24h"] = (
            forecast["min24h"]
        )

        result["min24hTime"] = (
            forecast["min24hTime"]
        )

        result["forecastStart"] = (
            forecast["forecastStart"]
        )

        result["forecastEnd"] = (
            forecast["forecastEnd"]
        )

        result["hourly"] = (
            forecast["hourly"]
        )

        result["mosmixStation"] = (
            forecast["mosmixStation"]
        )

        result["weatherStatus"] = (
            get_weather_status(
                result["min24h"]
            )
        )

        result["gasPathCleaning"] = (
            get_gas_path_status(
                result["min24h"]
            )
        )

        print(
            "MOSMIX:",
            result["min24h"],
            "°C |",
            result["min24hTime"],
            "| Punkt:",
            result["mosmixStation"]["id"],
            "| Entfernung:",
            result["mosmixStation"][
                "distanceKm"
            ],
            "km",
        )

    except Exception as error:
        message = f"MOSMIX: {error}"
        result["errors"].append(message)
        print(message)

    return result


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():
    generated_at = datetime.now(
        timezone.utc
    )

    output = {
        "updatedAt": utc_iso(
            generated_at
        ),

        "source":
            "Deutscher Wetterdienst "
            "CDC und MOSMIX L",

        "gasPathCleaningLimitC":
            GAS_PATH_LIMIT_C,

        "maximumMeasurementAgeHours":
            MAX_MEASUREMENT_AGE_HOURS,

        "airports": [],
    }

    # CDC laden
    try:
        zip_mapping = (
            load_cdc_zip_mapping()
        )

        print(
            len(zip_mapping),
            "CDC-ZIP-Dateien gefunden."
        )

        cdc_stations = (
            load_cdc_stations(
                zip_mapping
            )
        )

        print(
            len(cdc_stations),
            "CDC-Stationen geladen."
        )

    except Exception as error:
        print(
            "CDC-Grunddatenfehler:",
            error,
        )

        cdc_stations = []

    # MOSMIX nur einmal herunterladen.
    try:
        mosmix_document = (
            load_mosmix_document()
        )

    except Exception as error:
        print(
            "MOSMIX-Grunddatenfehler:",
            error,
        )

        mosmix_document = {
            "timestamps": [],
            "placemarks": [],
        }

    # Airports verarbeiten
    for airport in AIRPORTS:
        try:
            result = create_airport_result(
                airport,
                cdc_stations,
                mosmix_document,
            )

        except Exception as error:
            result = empty_airport_result(
                airport
            )

            result["errors"].append(
                f"Allgemein: {error}"
            )

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

    print("")
    print("=" * 60)
    print(
        f"{OUTPUT_FILE} wurde erzeugt."
    )

    for item in output["airports"]:
        print(
            item["code"],
            "| aktuell:",
            item["currentTemp"],
            "| Minimum:",
            item["min24h"],
            "| Gas Path:",
            item["gasPathCleaning"],
            "| Fehler:",
            item["errors"],
        )


if __name__ == "__main__":
    main()
