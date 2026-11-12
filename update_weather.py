import csv
import io
import json
import math
import re
import urllib.request
import zipfile

from datetime import datetime
from datetime import timedelta
from datetime import timezone
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

USER_AGENT = (
    "airport-weather-dashboard/2.0 "
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


MOSMIX_BASE_URL = (
    "https://opendata.dwd.de/"
    "weather/local_forecasts/mos/"
    "MOSMIX_L/single_stations/"
)


# ============================================================
# HTTP-FUNKTIONEN
# ============================================================

def download_bytes(url):
    """
    Datei vom DWD herunterladen.
    """

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=120,
    ) as response:
        return response.read()


def download_text(
    url,
    encoding="utf-8",
):
    """
    Textdatei vom DWD herunterladen.
    """

    data = download_bytes(url)

    return data.decode(
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


def round_or_none(
    value,
    digits=1,
):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


def kelvin_to_celsius(value):
    if value is None:
        return None

    return round(
        value - 273.15,
        1,
    )


def metres_per_second_to_kmh(value):
    if value is None:
        return None

    return round(
        value * 3.6,
        1,
    )


def pascal_to_hectopascal(value):
    if value is None:
        return None

    return round(
        value / 100.0,
        1,
    )


def utc_iso(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )

    value = value.astimezone(
        timezone.utc
    )

    return (
        value
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def distance_km(
    latitude_1,
    longitude_1,
    latitude_2,
    longitude_2,
):
    """
    Entfernung zweier geografischer Punkte
    nach der Haversine-Formel.
    """

    earth_radius_km = 6371.0

    phi_1 = math.radians(latitude_1)
    phi_2 = math.radians(latitude_2)

    delta_phi = math.radians(
        latitude_2 - latitude_1
    )

    delta_lambda = math.radians(
        longitude_2 - longitude_1
    )

    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_1)
        * math.cos(phi_2)
        * math.sin(delta_lambda / 2) ** 2
    )

    central_angle = 2 * math.atan2(
        math.sqrt(haversine),
        math.sqrt(1 - haversine),
    )

    return earth_radius_km * central_angle


# ============================================================
# CDC-STATIONEN
# ============================================================

def load_cdc_directory():
    """
    CDC-Verzeichnis einlesen.

    Liefert:
    - HTML-Inhalt
    - Mapping Stations-ID -> ZIP-URL
    """

    directory_html = download_text(
        CDC_BASE_URL,
        encoding="utf-8",
    )

    zip_mapping = {}

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

        zip_mapping[station_id] = url

    if not zip_mapping:
        raise RuntimeError(
            "Im CDC-Verzeichnis wurden keine "
            "aktuellen Temperatur-ZIP-Dateien gefunden."
        )

    return directory_html, zip_mapping


def load_cdc_stations(
    available_zip_mapping,
):
    """
    DWD-Stationsbeschreibung einlesen.

    Es werden nur Stationen übernommen, für die
    im recent-Verzeichnis auch eine aktuelle ZIP-Datei
    vorhanden ist.
    """

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

        # Format:
        # Stations_id
        # von_datum
        # bis_datum
        # Stationshoehe
        # geoBreite
        # geoLaenge
        # Stationsname
        # Bundesland

        match = re.match(
            r"^\s*(\d+)\s+"
            r"(\d{8})\s+"
            r"(\d{8})\s+"
            r"(-?[0-9.]+)\s+"
            r"(-?[0-9.]+)\s+"
            r"(-?[0-9.]+)\s+"
            r"(.+?)\s+"
            r"([A-Za-zÄÖÜäöüß /-]+)"
            r"\s*$",
            line,
        )

        if not match:
            continue

        station_id = (
            match.group(1).zfill(5)
        )

        latitude = float_or_none(
            match.group(5)
        )

        longitude = float_or_none(
            match.group(6)
        )

        station_name = (
            match.group(7).strip()
        )

        federal_state = (
            match.group(8).strip()
        )

        if latitude is None:
            continue

        if longitude is None:
            continue

        if station_id not in available_zip_mapping:
            continue

        stations.append(
            {
                "stationId": station_id,
                "latitude": latitude,
                "longitude": longitude,
                "name": station_name,
                "federalState": federal_state,
                "zipUrl":
                    available_zip_mapping[
                        station_id
                    ],
            }
        )

    if not stations:
        raise RuntimeError(
            "Es konnten keine verwendbaren "
            "CDC-Stationen eingelesen werden."
        )

    return stations


def nearest_stations(
    airport,
    stations,
    maximum_candidates=15,
):
    """
    Gibt mehrere nahe Stationen zurück.

    Dadurch hängt ein Airport nicht mehr nur von
    einer einzigen Station ab.
    """

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

    return candidates[
        :maximum_candidates
    ]


def load_latest_cdc_temperature(
    station,
):
    """
    Letzten gültigen CDC-Temperaturwert
    einer Station auslesen.
    """

    zip_data = download_bytes(
        station["zipUrl"]
    )

    with zipfile.ZipFile(
        io.BytesIO(zip_data)
    ) as archive:

        product_files = [
            filename
            for filename
            in archive.namelist()
            if filename.lower().endswith(
                ".txt"
            )
            and "produkt_tu_stunde"
            in filename.lower()
        ]

        if not product_files:
            raise RuntimeError(
                "Temperatur-Produktdatei "
                "fehlt im ZIP-Archiv."
            )

        product_filename = (
            product_files[0]
        )

        product_text = (
            archive
            .read(product_filename)
            .decode(
                "latin-1",
                errors="replace",
            )
        )

    reader = csv.DictReader(
        io.StringIO(product_text),
        delimiter=";",
    )

    latest_measurement = None

    for raw_row in reader:
        row = {}

        for key, value in raw_row.items():
            if key is None:
                continue

            clean_key = key.strip()

            clean_value = (
                value.strip()
                if isinstance(value, str)
                else value
            )

            row[clean_key] = clean_value

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
            latest_measurement is None
            or timestamp
            > latest_measurement["timestamp"]
        ):
            latest_measurement = {
                "timestamp": timestamp,
                "temperature": temperature,
            }

    if latest_measurement is None:
        raise RuntimeError(
            "Keine gültige Temperaturmessung "
            "in der CDC-Datei gefunden."
        )

    return {
        "temperature": round(
            latest_measurement[
                "temperature"
            ],
            1,
        ),
        "time": utc_iso(
            latest_measurement[
                "timestamp"
            ]
        ),
    }


def get_current_temperature(
    airport,
    stations,
):
    """
    Versucht mehrere nahe CDC-Stationen.

    Wenn die nächste Station keine Daten liefert,
    wird automatisch die nächste geeignete Station
    geprüft.
    """

    candidates = nearest_stations(
        airport,
        stations,
        maximum_candidates=15,
    )

    errors = []

    for station in candidates:
        try:
            measurement = (
                load_latest_cdc_temperature(
                    station
                )
            )

            return {
                "temperature":
                    measurement["temperature"],

                "time":
                    measurement["time"],

                "station": {
                    "stationId":
                        station["stationId"],

                    "name":
                        station["name"],

                    "federalState":
                        station["federalState"],

                    "distanceKm":
                        station["distanceKm"],
                },
            }

        except Exception as error:
            errors.append(
                f"{station['stationId']} "
                f"{station['name']}: {error}"
            )

    raise RuntimeError(
        "Keine der "
        f"{len(candidates)} nächsten "
        "CDC-Stationen lieferte Daten. "
        + " | ".join(errors[:3])
    )


# ============================================================
# MOSMIX-KML-HILFSFUNKTIONEN
# ============================================================

def local_name(tag):
    """
    XML-Namespace aus einem Tag entfernen.
    """

    return tag.split("}")[-1]


def element_attribute(
    element,
    attribute_name,
):
    for key, value in (
        element.attrib.items()
    ):
        if local_name(key) == attribute_name:
            return value

    return None


def element_text(element):
    if element is None:
        return ""

    return "".join(
        element.itertext()
    ).strip()


def find_elements_by_local_name(
    root,
    requested_name,
):
    return [
        element
        for element in root.iter()
        if local_name(element.tag)
        == requested_name
    ]


def parse_number_list(value):
    parsed_values = []

    for item in value.split():
        if item in {
            "",
            "-",
            "-999",
            "-999.0",
        }:
            parsed_values.append(None)
        else:
            parsed_values.append(
                float_or_none(item)
            )

    return parsed_values


def get_series_value(
    series,
    index,
):
    if index >= len(series):
        return None

    return series[index]


# ============================================================
# MOSMIX-PROGNOSE
# ============================================================

def load_mosmix_forecast(
    airport,
):
    """
    MOSMIX-L-Datei anhand der ICAO-Kennung laden.
    """

    icao = airport["icao"]

    kmz_url = (
        MOSMIX_BASE_URL
        + f"{icao}/kml/"
        + f"MOSMIX_L_LATEST_{icao}.kmz"
    )

    kmz_data = download_bytes(
        kmz_url
    )

    with zipfile.ZipFile(
        io.BytesIO(kmz_data)
    ) as archive:

        kml_files = [
            filename
            for filename
            in archive.namelist()
            if filename.lower().endswith(
                ".kml"
            )
        ]

        if not kml_files:
            raise RuntimeError(
                "MOSMIX-KMZ enthält "
                "keine KML-Datei."
            )

        kml_data = archive.read(
            kml_files[0]
        )

    root = ET.fromstring(
        kml_data
    )

    # --------------------------------------------------------
    # Zeitachse
    # --------------------------------------------------------

    timestamps = []

    time_elements = (
        find_elements_by_local_name(
            root,
            "TimeStep",
        )
    )

    for element in time_elements:
        value = element_text(element)

        if not value:
            timestamps.append(None)
            continue

        try:
            timestamp = (
                datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                )
                .astimezone(timezone.utc)
            )

            timestamps.append(timestamp)

        except ValueError:
            timestamps.append(None)

    if not timestamps:
        raise RuntimeError(
            "MOSMIX enthält keine Zeitachse."
        )

    # --------------------------------------------------------
    # Placemark suchen
    # --------------------------------------------------------

    placemarks = (
        find_elements_by_local_name(
            root,
            "Placemark",
        )
    )

    if not placemarks:
        raise RuntimeError(
            "MOSMIX enthält kein Placemark."
        )

    selected_placemark = None

    for placemark in placemarks:
        placemark_string = ET.tostring(
            placemark,
            encoding="unicode",
        )

        if icao in placemark_string:
            selected_placemark = (
                placemark
            )
            break

    # Single-Station-Datei sollte genau
    # ein relevantes Placemark enthalten.
    if selected_placemark is None:
        selected_placemark = placemarks[0]

    # --------------------------------------------------------
    # Wetterparameter einlesen
    # --------------------------------------------------------

    forecast_values = {}

    forecast_elements = (
        find_elements_by_local_name(
            selected_placemark,
            "Forecast",
        )
    )

    for forecast in forecast_elements:
        parameter_name = (
            element_attribute(
                forecast,
                "elementName",
            )
        )

        if not parameter_name:
            continue

        value_elements = [
            element
            for element in forecast.iter()
            if local_name(element.tag)
            == "value"
        ]

        if not value_elements:
            continue

        value_text = element_text(
            value_elements[0]
        )

        forecast_values[
            parameter_name
        ] = parse_number_list(
            value_text
        )

    temperatures = (
        forecast_values.get(
            "TTT",
            [],
        )
    )

    if not temperatures:
        raise RuntimeError(
            "MOSMIX-Parameter TTT "
            "für Temperatur fehlt."
        )

    dewpoints = (
        forecast_values.get(
            "Td",
            [],
        )
    )

    wind_speeds = (
        forecast_values.get(
            "FF",
            [],
        )
    )

    wind_directions = (
        forecast_values.get(
            "DD",
            [],
        )
    )

    gusts_1h = (
        forecast_values.get(
            "FX1",
            [],
        )
    )

    gusts_3h = (
        forecast_values.get(
            "FX3",
            [],
        )
    )

    pressures = (
        forecast_values.get(
            "PPPP",
            [],
        )
    )

    precipitation = (
        forecast_values.get("RR1c")
        or forecast_values.get("RR1")
        or []
    )

    # --------------------------------------------------------
    # Stündliche Datensätze
    # --------------------------------------------------------

    rows = []

    for index, timestamp in enumerate(
        timestamps
    ):
        if timestamp is None:
            continue

        temperature_kelvin = (
            get_series_value(
                temperatures,
                index,
            )
        )

        temperature_celsius = (
            kelvin_to_celsius(
                temperature_kelvin
            )
        )

        if temperature_celsius is None:
            continue

        dewpoint_celsius = (
            kelvin_to_celsius(
                get_series_value(
                    dewpoints,
                    index,
                )
            )
        )

        wind_kmh = (
            metres_per_second_to_kmh(
                get_series_value(
                    wind_speeds,
                    index,
                )
            )
        )

        gust_value = (
            get_series_value(
                gusts_1h,
                index,
            )
        )

        if gust_value is None:
            gust_value = (
                get_series_value(
                    gusts_3h,
                    index,
                )
            )

        gust_kmh = (
            metres_per_second_to_kmh(
                gust_value
            )
        )

        wind_direction = (
            round_or_none(
                get_series_value(
                    wind_directions,
                    index,
                ),
                0,
            )
        )

        pressure_hpa = (
            pascal_to_hectopascal(
                get_series_value(
                    pressures,
                    index,
                )
            )
        )

        precipitation_mm = (
            round_or_none(
                get_series_value(
                    precipitation,
                    index,
                ),
                1,
            )
        )

        rows.append(
            {
                "timestamp": timestamp,

                "time":
                    utc_iso(timestamp),

                "temperature":
                    temperature_celsius,

                "dewPoint":
                    dewpoint_celsius,

                "windKmh":
                    wind_kmh,

                "gustKmh":
                    gust_kmh,

                "windDirectionDegrees":
                    wind_direction,

                "pressureHpa":
                    pressure_hpa,

                "precipMm":
                    precipitation_mm,
            }
        )

    if not rows:
        raise RuntimeError(
            "MOSMIX enthält keine "
            "gültigen Temperaturwerte."
        )

    now = datetime.now(
        timezone.utc
    )

    forecast_end = (
        now
        + timedelta(hours=24)
    )

    next_24_hours = [
        row
        for row in rows
        if (
            row["timestamp"] >= now
            and row["timestamp"]
            <= forecast_end
        )
    ]

    if not next_24_hours:
        raise RuntimeError(
            "Keine MOSMIX-Prognosewerte "
            "für die nächsten 24 Stunden."
        )

    minimum_row = min(
        next_24_hours,
        key=lambda row:
            row["temperature"],
    )

    hourly_output = []

    for row in next_24_hours:
        hourly_output.append(
            {
                key: value
                for key, value
                in row.items()
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
            hourly_output,

        "kmzUrl":
            kmz_url,
    }


# ============================================================
# STATUSLOGIK
# ============================================================

def get_weather_status(
    minimum_temperature,
):
    if minimum_temperature is None:
        return "NO DATA"

    if minimum_temperature < -5:
        return "CRITICAL"

    if minimum_temperature < 0:
        return "WARNING"

    if minimum_temperature <= 3:
        return "WATCH"

    return "SAFE"


def get_gas_path_status(
    minimum_temperature,
):
    """
    Gas Path Cleaning darf nur gemacht werden,
    wenn das 24-h-Minimum mindestens +5 °C beträgt.
    """

    if minimum_temperature is None:
        return "NO DATA"

    if (
        minimum_temperature
        >= GAS_PATH_LIMIT_C
    ):
        return "GO"

    return "NO GO"


# ============================================================
# AIRPORT VERARBEITEN
# ============================================================

def create_empty_airport_result(
    airport,
):
    return {
        "code": airport["code"],
        "icao": airport["icao"],
        "city": airport["city"],

        "currentTemp": None,
        "currentTempTime": None,

        "min24h": None,
        "min24hTime": None,

        "forecastStart": None,
        "forecastEnd": None,

        "gasPathCleaning": "NO DATA",
        "weatherStatus": "NO DATA",

        "measurementStation": None,

        "hourly": [],

        "errors": [],
    }


def create_airport_result(
    airport,
    stations,
):
    print("")
    print("=" * 60)

    print(
        f"Verarbeite "
        f"{airport['code']} "
        f"({airport['icao']})"
    )

    result = (
        create_empty_airport_result(
            airport
        )
    )

    # --------------------------------------------------------
    # Aktueller CDC-Messwert
    # --------------------------------------------------------

    try:
        measurement = (
            get_current_temperature(
                airport,
                stations,
            )
        )

        result["currentTemp"] = (
            measurement["temperature"]
        )

        result["currentTempTime"] = (
            measurement["time"]
        )

        result[
            "measurementStation"
        ] = measurement["station"]

        print(
            "CDC erfolgreich:",
            result["currentTemp"],
            "°C",
            "Station:",
            measurement["station"]["name"],
            "Entfernung:",
            measurement["station"][
                "distanceKm"
            ],
            "km",
        )

    except Exception as error:
        error_message = (
            f"CDC: {error}"
        )

        result["errors"].append(
            error_message
        )

        print(error_message)

    # --------------------------------------------------------
    # MOSMIX-Prognose
    # --------------------------------------------------------

    try:
        forecast = (
            load_mosmix_forecast(
                airport
            )
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
            "MOSMIX erfolgreich:",
            "Minimum:",
            result["min24h"],
            "°C",
            "Zeit:",
            result["min24hTime"],
        )

    except Exception as error:
        error_message = (
            f"MOSMIX: {error}"
        )

        result["errors"].append(
            error_message
        )

        print(error_message)

    print(
        "Ergebnis:",
        airport["code"],
        "Aktuell:",
        result["currentTemp"],
        "Minimum:",
        result["min24h"],
        "Weather:",
        result["weatherStatus"],
        "Gas Path:",
        result["gasPathCleaning"],
    )

    return result


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():
    generated_at = datetime.now(
        timezone.utc
    )

    output = {
        "updatedAt":
            utc_iso(generated_at),

        "source":
            "Deutscher Wetterdienst "
            "CDC und MOSMIX L",

        "gasPathCleaningLimitC":
            GAS_PATH_LIMIT_C,

        "airports":
            [],
    }

    # --------------------------------------------------------
    # CDC-Metadaten einmalig laden
    # --------------------------------------------------------

    try:
        (
            directory_html,
            available_zip_mapping,
        ) = load_cdc_directory()

        print(
            len(available_zip_mapping),
            "aktuelle CDC-ZIP-Dateien gefunden."
        )

        stations = load_cdc_stations(
            available_zip_mapping
        )

        print(
            len(stations),
            "verwendbare CDC-Stationen geladen."
        )

    except Exception as error:
        print(
            "CDC-Grunddaten konnten "
            f"nicht geladen werden: {error}"
        )

        stations = []

    # --------------------------------------------------------
    # Alle Airports verarbeiten
    # --------------------------------------------------------

    for airport in AIRPORTS:
        try:
            airport_result = (
                create_airport_result(
                    airport,
                    stations,
                )
            )

        except Exception as error:
            airport_result = (
                create_empty_airport_result(
                    airport
                )
            )

            airport_result[
                "errors"
            ].append(
                f"Allgemeiner Fehler: {error}"
            )

        output["airports"].append(
            airport_result
        )

    # --------------------------------------------------------
    # JSON schreiben
    # --------------------------------------------------------

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

    print(
        "Enthaltene Airports:",
        ", ".join(
            item["code"]
            for item in output["airports"]
        ),
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
