import re

def analyze_technical_density(text, lexicon):
    if not text or not lexicon:
        return 0, []
    bag = set(re.findall(r'\w+', text.lower()))
    found = [w for w in lexicon if w.lower() in bag]
    return len(found), found

def post_scrape_organize(raw_data, carved_text, source_url, lexicon):
    score, markers = analyze_technical_density(carved_text, lexicon)
    return markers if score >= 3 else []