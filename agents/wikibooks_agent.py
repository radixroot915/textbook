import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.wikisource_agent import WikiSourceAgent


class WikibooksAgent(WikiSourceAgent):
    source_name = "wikibooks"
    api_url = "https://en.wikibooks.org/w/api.php"
    priority = 2
    min_hits = 2
    tier_affinity = {"foundational", "practical"}
