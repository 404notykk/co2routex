from pathlib import Path

from railway_router.config import load_settings
from railway_router.station_distances import calculate_station_distances


def main():
    settings = load_settings(Path(__file__).with_name("config.yaml"))
    if settings.distance_calculation is None:
        raise ValueError("config.yaml has no distance_calculation section.")
    result = calculate_station_distances(settings.distance_calculation)
    print("Station distance calculation completed successfully.")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()

