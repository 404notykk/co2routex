from pathlib import Path

from railway_router.config import load_settings
from railway_router.railway_request import process_railway_request


def main():
    settings = load_settings(Path(__file__).with_name("config.yaml"))
    if settings.railway_request is None:
        raise ValueError("config.yaml has no railway_request section.")
    result = process_railway_request(settings.railway_request)
    print("Railway request processing completed successfully.")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()

