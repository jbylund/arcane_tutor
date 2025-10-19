"""Import Magic: The Gathering card data directly from Gatherer."""

import json
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# HTTP status codes
HTTP_NOT_FOUND = 404


class GathererFetcher:
    """Fetches card data from Gatherer website."""

    def __init__(self) -> None:
        """Initialize the fetcher with session configuration."""
        self.base_url = "https://gatherer.wizards.com"
        self.session = requests.Session()

        retries = Retry(
            total=3,
            backoff_factor=0.1,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.mount("http://", HTTPAdapter(max_retries=retries))

    def fetch_all_sets(self) -> list:
        """Fetch the list of all sets from Gatherer."""
        url = f"{self.base_url}/sets"
        # https://gatherer.wizards.com/sets?page=2
        page = 1
        all_sets = []


        while True:
            response = self.session.get(url, params={
                "page": page,
            })
            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                if e.response.status_code == HTTP_NOT_FOUND:
                    break
                raise

            page_text = response.text

            # Extract the items array from the response
            _, _, remainder = page_text.partition(r',\"items\":')
            if not remainder:
                msg = "No remainder"
                raise AssertionError(msg)

            # Find the end of the array by counting brackets
            bracket_count = 0
            end_pos = 0
            for i, char in enumerate(remainder):
                if char == "[":
                    bracket_count += 1
                elif char == "]":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_pos = i + 1
                        break

            if end_pos == 0:
                # Couldn't find the end of the array
                break

            sets_array_str = remainder[:end_pos]
            sets_array = json.loads(json.loads('"' + sets_array_str + '"'))

            if not sets_array:
                break

            all_sets.extend(sets_array)
            page += 1

        return [
            r["setCode"] for r in all_sets
        ]

    def fetch_set(self, set_name: str) -> list:
        """Fetch all cards from a specific set."""
        url = f"{self.base_url}/sets/{set_name}"
        page = 1
        set_cards = []
        while True:
            response = self.session.get(url, params={"page": page})
            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                if e.response.status_code == HTTP_NOT_FOUND:
                    break
                raise
            page_text = response.text
            _, _, remainder = page_text.partition(r',\"items\":')
            if not remainder:
                msg = "No remainder"
                raise ValueError(msg)

            # Find the end of the array by counting brackets
            bracket_count = 0
            end_pos = 0
            for i, char in enumerate(remainder):
                if char == "[":
                    bracket_count += 1
                elif char == "]":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_pos = i + 1
                        break

            cards_array_str = remainder[:end_pos]
            cards_array = json.loads(json.loads('"' + cards_array_str + '"'))
            set_cards.extend(cards_array)
            page += 1
        return set_cards

    def save_set_to_json(self, set_name: str, output_dir: str = "gatherer_data") -> Path:
        """Fetch a set and save it to a JSON file."""
        cards = self.fetch_set(set_name)

        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Save to file
        output_file = output_path / f"{set_name}.json"
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(cards, f, indent=2, ensure_ascii=False)

        return output_file


def main() -> None:
    """Example usage: fetch TDM set."""
    fetcher = GathererFetcher()
    fetcher.fetch_all_sets()

if __name__ == "__main__":
    main()
