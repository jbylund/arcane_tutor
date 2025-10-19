import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

class GathererFetcher:
    def __init__(self):
        self.base_url = "https://gatherer.wizards.com"
        self.session = requests.Session()

        retries = Retry(
            total=3,
            backoff_factor=0.1,
        )
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        self.session.mount('http://', HTTPAdapter(max_retries=retries))

    def fetch_set(self, set_name: str):
        url = f"{self.base_url}/sets/{set_name}"
        page = 1
        set_cards = []
        while True:
            response = self.session.get(url, params={"page": page})
            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                if e.response.status_code == 404:
                    break
                raise
            page_text = response.text
            _, _, remainder = page_text.partition(r',\"items\":')
            if not remainder:
                raise ValueError("No remainder")

            # Find the end of the array by counting brackets
            bracket_count = 0
            end_pos = 0
            for i, char in enumerate(remainder):
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_pos = i + 1
                        break

            cards_array_str = remainder[:end_pos]
            cards_array = json.loads(json.loads('"' + cards_array_str + '"'))
            set_cards.extend(cards_array)
            page += 1
        return set_cards


def main():
    fetcher = GathererFetcher()
    tdm_cards = fetcher.fetch_set("TDM")
    print(json.dumps(tdm_cards, indent=4))

if __name__ == "__main__":
    main()
