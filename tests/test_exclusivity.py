from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
import requests

from nintendo_backend import exclusivity as ex


URL = "https://www.nintendo.com/us/store/products/the-duskbloods-switch-2/"


def game(name="The Duskbloods", igdb_id=338616, platform_id=508):
    platform = {"id": platform_id, "name": "Nintendo Switch 2" if platform_id == 508 else "Nintendo Switch",
                "code": "NS2" if platform_id == 508 else "NS"}
    return {"id": f"igdb:{igdb_id}", "igdb_id": igdb_id, "name_en": name,
            "display_name": name, "hypes": 100, "platforms": [platform],
            "known_platforms": [deepcopy(platform)], "platform_data_complete": True,
            "exclusivity": {"status": "listed_only", "platform": platform["code"], "source": "IGDB"},
            "nintendo_url": URL, "releases": [{"date": "2026-10-22", "platform": platform["code"]}]}


class Response:
    def __init__(self, text, status=200, url=URL):
        self.status_code, self.url, self.text = status, url, text
        self.content = text.encode()


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


HTML = '<title>The Duskbloods – Nintendo Switch 2 Exclusive - Nintendo Official Site</title>'


def enrich(row=None, html=HTML):
    catalog = {"games": [row or game()], "source": {"complete": True}}
    session = Session(Response(html))
    return catalog, ex.enrich_exclusivity(catalog, session), session


def test_metadata_matches_actual_game_and_confirms_without_mutating_input():
    original, result, session = enrich()
    confirmed = result["games"][0]
    assert confirmed["exclusivity"]["status"] == "confirmed"
    assert confirmed["exclusivity"]["platform"] == "NS2"
    assert original["games"][0]["exclusivity"]["status"] == "listed_only"
    assert result["source"] == original["source"]
    assert session.calls[0] == (URL, {"timeout": 5, "allow_redirects": False})
    assert ex.validate_enriched_game(original["games"][0], confirmed)


def test_scoped_product_json_accepts_description_without_repeated_game_name():
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [
        {"@type": "Product", "name": "Other Game", "description": "Only on Nintendo Switch"},
        {"@type": "VideoGame", "name": "The Duskbloods", "description": "Available exclusively for the Nintendo Switch™ 2 system."}
    ]}) + '</script>'
    _, result, _ = enrich(html=html)
    assert result["games"][0]["exclusivity"]["platform"] == "NS2"


def test_og_description_must_name_the_same_game():
    html = '<meta property="og:title" content="The Duskbloods"><meta name="description" content="From the creators, The Duskbloods is a Nintendo Switch 2 exclusive.">'
    assert enrich(html=html)[1]["games"][0]["exclusivity"]["status"] == "confirmed"
    other = html.replace('From the creators, The Duskbloods', 'Some Other Game')
    assert enrich(html=other)[1]["games"][0]["exclusivity"]["status"] == "listed_only"


@pytest.mark.parametrize("html", [
    '<title>Other Game – Nintendo Switch 2 Exclusive - Nintendo Official Site</title>',
    '<title>The Duskbloods - Nintendo Official Site</title><footer>Only on Nintendo Switch 2</footer>',
    '<title>The Duskbloods - Nintendo Official Site</title><nav>Nintendo Switch 2 exclusive</nav>',
    '<meta property="og:title" content="The Duskbloods"><meta name="description" content="The Duskbloods is not exclusive to Nintendo Switch 2">',
    '<meta property="og:title" content="The Duskbloods"><meta name="description" content="The Duskbloods has Nintendo Switch 2 exclusive features.">',
    '<meta property="og:title" content="The Duskbloods"><meta name="description" content="The Duskbloods offers exclusive content for Nintendo Switch 2.">',
    '<script type="application/ld+json">{broken</script>',
])
def test_unrelated_or_non_game_exclusivity_is_never_a_positive_label(html):
    _, result, _ = enrich(html=html)
    assert result["games"][0]["exclusivity"]["status"] == "listed_only"


def test_nintendo_switch_is_distinct_from_nintendo_switch_2():
    row = game(name="Example Game", igdb_id=1, platform_id=130)
    html = '<script type="application/ld+json">' + json.dumps({"@type": "Product", "name": "Example Game", "description": "Available exclusively for the Nintendo Switch system."}) + '</script>'
    original, result, _ = enrich(row, html)
    assert result["games"][0]["exclusivity"]["platform"] == "NS"
    assert ex.validate_enriched_game(original["games"][0], result["games"][0])
    wrong = html.replace('Switch system', 'Switch 2 system')
    assert enrich(row, wrong)[1]["games"][0]["exclusivity"]["status"] == "listed_only"


def test_sg1000_platform_84_is_not_nintendo_switch():
    # Canonical IGDB IDs prevent mutually consistent but incorrect fixtures
    # from accidentally proving the wrong runtime mapping.
    row = game(name="Example Game", igdb_id=1, platform_id=84)
    row['known_platforms'][0]['name'] = 'SG-1000'
    row['platforms'][0]['name'] = 'SG-1000'
    html = '<script type="application/ld+json">' + json.dumps({
        '@type': 'Product', 'name': 'Example Game',
        'description': 'Available exclusively for the Nintendo Switch system.'}) + '</script>'
    _, result, session = enrich(row, html)
    assert session.calls == []
    assert result['games'][0]['exclusivity']['status'] == 'listed_only'


@pytest.mark.parametrize("alter", [
    lambda row: row["known_platforms"].append({"id": 6, "name": "PC"}),
    lambda row: row.update(platform_data_complete=False),
    lambda row: row.update(platforms=[{"id": 130, "code": "NS"}]),
    lambda row: row.update(known_platforms=[{"id": "508"}]),
    lambda row: row['platforms'][0].update(code='NS'),
])
def test_conflicting_or_incomplete_platforms_block_official_enrichment(alter):
    row = game()
    alter(row)
    _, result, session = enrich(row)
    assert result["games"][0]["exclusivity"]["status"] == "listed_only"
    assert session.calls == []


@pytest.mark.parametrize("url", [
    'https://www.nintendo.com.evil.example/us/store/products/the-duskbloods-switch-2/',
    'http://www.nintendo.com/us/store/products/the-duskbloods-switch-2/',
    'https://user:secret@www.nintendo.com/us/store/products/the-duskbloods-switch-2/',
    'https://www.nintendo.com:444/us/store/products/the-duskbloods-switch-2/',
    'https://www.nintendo.com/us/',
])
def test_untrusted_nonproduct_urls_are_never_requested(url):
    row = game(name='Unrelated Game', igdb_id=1)
    row['nintendo_url'] = url
    _, result, session = enrich(row)
    assert session.calls == []
    assert result['games'][0]['exclusivity']['status'] == 'listed_only'


@pytest.mark.parametrize("response", [Response(HTML, status=429), Response(HTML, status=302),
                                         Response(HTML, url='https://www.example.com'), requests.Timeout('internal transport text')])
def test_http_limit_redirect_or_timeout_preserves_the_listing(response):
    session = Session(response)
    result = ex.enrich_exclusivity({'games': [game()]}, session)
    assert result['games'][0]['exclusivity']['status'] == 'listed_only'
    assert len(session.calls) == 1


def test_finite_page_budget_and_same_url_cache(monkeypatch):
    monkeypatch.setattr(ex, 'MAX_PAGES', 2)
    rows = [game(name=f'Example {index}', igdb_id=index + 1) for index in range(8)]
    for index, row in enumerate(rows):
        row['nintendo_url'] = f'https://www.nintendo.com/us/store/products/example-{index}/'
    session = Session(Response('', status=429))
    ex.enrich_exclusivity({'games': rows}, session)
    assert len(session.calls) == 2
    same = Session(Response(HTML))
    result = ex.enrich_exclusivity({'games': [game(), game()]}, same)
    assert len(same.calls) == 1
    assert all(row['exclusivity']['status'] == 'confirmed' for row in result['games'])


def test_elapsed_deadline_stops_requests(monkeypatch):
    ticks = iter([0, 91])
    monkeypatch.setattr(ex.time, 'monotonic', lambda: next(ticks))
    session = Session(Response(HTML))
    result = ex.enrich_exclusivity({'games': [game()]}, session)
    assert session.calls == []
    assert result['games'][0]['exclusivity']['status'] == 'listed_only'


def test_verified_fire_emblem_fallback_requires_exact_identity_and_complete_platforms():
    row = game(name="Fire Emblem: Fortune’s Weave", igdb_id=366896)
    original, result, session = enrich(row)
    confirmed = result['games'][0]
    assert confirmed['exclusivity']['evidence_method'] == 'curated_verified_fact'
    assert session.calls == []
    assert ex.validate_enriched_game(original['games'][0], confirmed)
    other = game(name="Fire Emblem: Fortune’s Weave", igdb_id=999)
    other['nintendo_url'] = None
    _, altered, _ = enrich(other, html='')
    assert altered['games'][0]['exclusivity']['status'] == 'listed_only'


@pytest.mark.parametrize('mutation', [
    lambda row: row.update(hypes=999),
    lambda row: row.update(display_name='Other Name'),
    lambda row: row['exclusivity'].update(platform='NS'),
    lambda row: row['exclusivity'].update(evidence_title='Other Game'),
    lambda row: row['exclusivity'].update(url='https://www.nintendo.com.evil.example'),
    lambda row: row['exclusivity'].update(evidence='Nintendo Switch 2 exclusive content'),
    lambda row: row['exclusivity'].update(checked_at=(datetime.now(timezone.utc) - timedelta(days=31)).isoformat()),
])
def test_publish_guard_rejects_non_evidence_edits_and_invalid_confirmation(mutation):
    original, enriched, _ = enrich()
    row = enriched['games'][0]
    mutation(row)
    assert not ex.validate_enriched_game(original['games'][0], row)


def test_cli_syncs_only_evidence_into_master_and_preserves_completion(tmp_path, monkeypatch):
    row = game()
    master_row = {**deepcopy(row), 'raw': {'id': row['igdb_id'], 'hypes': 100},
                  'eligibility_reasons': [], 'content_screening': {'status': 'screened'}}
    documents = {'nintendo_upcoming.json': {'games': [row], 'source': {'complete': True}},
                 'nintendo_master.json': {'games': {row['id']: master_row}},
                 'nintendo_refresh_status.json': {'complete': True, 'candidate_count': 1, 'public_count': 1}}
    for filename, doc in documents.items():
        (tmp_path / filename).write_text(json.dumps(doc), encoding='utf-8')
    monkeypatch.setattr(ex.requests, 'Session', lambda: Session(Response(HTML)))
    # The production function owns and closes its real Session. Keep this test
    # transport minimal while recording that the CLI does not touch raw data.
    monkeypatch.setattr(Session, 'close', lambda self: None, raising=False)
    assert ex.main(['--output-dir', str(tmp_path)]) == 0
    public = json.loads((tmp_path / 'nintendo_upcoming.json').read_text())
    master = json.loads((tmp_path / 'nintendo_master.json').read_text())
    status = json.loads((tmp_path / 'nintendo_refresh_status.json').read_text())
    assert master['games'][row['id']]['raw'] == master_row['raw']
    assert public['games'][0]['exclusivity'] == master['games'][row['id']]['exclusivity']
    assert status['complete'] is True and status['public_count'] == 1
    assert status['exclusivity_counts']['confirmed'] == 1


def test_ps5_single_platform_never_inherits_nintendo_exclusivity_or_store_branding():
    row = game(name="A PS5 game", igdb_id=1)
    row["platforms"] = [{"id": 167, "name": "PlayStation 5", "code": "PS5"}]
    row["known_platforms"] = deepcopy(row["platforms"])
    row["exclusivity"].update(platform="PS5")
    row["playstation_url"] = "https://store.playstation.com/zh-hant-tw/product/HP0001-PPSA12345_00-BASEGAME00000000"
    original, result, session = enrich(row)
    assert len(session.calls) == 1  # A Sony listing alone never proves exclusivity.
    assert result["games"][0] == row and result["games"][0]["exclusivity"]["status"] == "listed_only"
    assert ex.validate_enriched_game(original["games"][0], result["games"][0])


def ps5_game():
    row = game(name="A PS5 game", igdb_id=1)
    row["platforms"] = [{"id": 167, "name": "PlayStation 5", "code": "PS5"}]
    row["known_platforms"] = deepcopy(row["platforms"])
    row["exclusivity"].update(platform="PS5")
    row["nintendo_url"] = None
    row["playstation_url"] = "https://www.playstation.com/en-tw/games/a-ps5-game"
    row["platform_urls"] = {"PS5": row["playstation_url"]}
    return row


def test_ps5_exact_official_product_metadata_confirms_and_publisher_validates():
    row = ps5_game()
    html = '<meta property="og:title" content="A PS5 game - PS5 Games | PlayStation (Taiwan)">' \
           '<meta name="description" content="A PS5 game is available exclusively for PlayStation 5.">'
    session = Session(Response(html, url=row["playstation_url"]))
    updated = ex.enrich_exclusivity({"games": [row]}, session)["games"][0]
    assert updated["exclusivity"]["status"] == "confirmed" and updated["exclusivity"]["source"] == "PlayStation official"
    assert updated["nintendo_url"] is None and updated["playstation_url"] == row["playstation_url"]
    assert ex.validate_enriched_game(row, updated)
    updated["nintendo_url"] = URL
    assert not ex.validate_enriched_game(row, updated)


@pytest.mark.parametrize("phrase", [
    "A PS5 game is available on PS5.", "Other game is only on PS5.",
    "A PS5 game is not exclusively for PlayStation 5.",
    "A PS5 game has PS5 exclusive content.", "A PS5 game is a PS5 console exclusive.",
    "A PS5 game is only on PS5 and PC.", "A PS5 game is a timed exclusive for PS5.",
    "A PS5 game has bonus content available only on PS5.",
    "A PS5 game has content only on PS5 bonus.",
    "A PS5 game has DLC available exclusively on PlayStation 5.",
])
def test_ps5_unproven_timed_content_or_other_game_exclusivity_stays_unconfirmed(phrase):
    row = ps5_game()
    html = '<meta property="og:title" content="A PS5 game"><meta name="description" content="' + phrase + '">'
    session = Session(Response(html, url=row["playstation_url"]))
    assert ex.enrich_exclusivity({"games": [row]}, session)["games"][0] == row


def test_ps5_scoped_json_product_and_cross_platform_evidence_remain_separate():
    row = ps5_game()
    html = '<script type="application/ld+json">' + json.dumps({"@type": "VideoGame", "name": "A PS5 game", "description": "Only on PS5."}) + '</script>'
    updated = ex.enrich_exclusivity({"games": [row]}, Session(Response(html, url=row["playstation_url"])))["games"][0]
    assert ex.validate_enriched_game(row, updated)
    row["known_platforms"].append({"id": 6, "name": "PC"})
    session = Session(Response(html, url=row["playstation_url"]))
    assert ex.enrich_exclusivity({"games": [row]}, session)["games"][0] == row and session.calls == []
