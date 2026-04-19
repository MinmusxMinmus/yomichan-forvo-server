import http.server
import socketserver
import re
import json
import base64

from http import HTTPStatus
from urllib.parse import urlparse
from urllib.parse import parse_qs
from dataclasses import dataclass, field
from typing import List

import curl_cffi
from lxml.html import fromstring as html_fromstring
from lxml import etree

# Config default values
@dataclass
class ForvoConfig():
    port: int = 8770
    language: str = 'zh'
    preferred_usernames: List[str] = field(default_factory=list)
    preferred_countries: List[str] = field(default_factory=list)
    show_gender: bool = True
    show_country: bool = False

    def set(self, config):
        self.__init__(**config)

    def __post_init__(self):
        self.preferred_countries = [c.lower() for c in self.preferred_countries]

_forvo_config = ForvoConfig()

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

class Forvo():
    """
    Forvo web-scraper utility class that matches YomiChan's expected output for a custom audio source
    """
    _SERVER_HOST = "https://forvo.com"
    _AUDIO_HTTP_HOST = "https://audio12.forvo.com"
    def __init__(self, config=_forvo_config):
        self.config = config

    def _get(self, path):
        """
        Makes a GET request assuming base url.
        """
        url = self._SERVER_HOST + path
        ret = curl_cffi.get(url, timeout=10, impersonate="chrome").text
        return ret.removeprefix('<!doctype html>\n')

    def word(self, w):
        """
        Scrape forvo's word page for audio sources
        """
        w = w.strip()
        if len(w) == 0:
            return []
        path = f"/word/{w}/"
        html = self._get(path)

        # Forvo's word page returns multiple result sets grouped by langauge like:
        # <div id="language-container-ja">
        #   <article>
        #       <ul class="show-all-pronunciations">
        #           <li>
        #              <span class="play" onclick"(some javascript to play the word audio)"></span>
        #                "Pronunciation by <span><a href="/username/link">skent</a></span>"
        #              <div class="more">...</div>
        #           </li>
        #       </ul>
        #       ...
        #   </article>
        #   <article id="extra-word-info-76">...</article>
        # </ul>
        # We also filter out ads
        results = html_fromstring(html).xpath(f'.//div[@id="language-container-{self.config.language}"]/article/ul[@id="pronunciations-list-{self.config.language}"]/li')

        pronunciations = []
        for i in results:
            url = self._extract_url(i.xpath("./div[contains(@class,'play')]/@onclick")[0])

            # Capture the username of the user
            # Some users have deleted accounts which is why can't just parse it from the <a> tag
            username_span = i.xpath("./span/span/@data-p2")

            # Sometimes the username won't be inside a second <span>,
            # it'll just be the last part of the first one.
            if len(username_span) == 0:
                text = i.xpath("./span")[0].text
                username = text.strip().split("\t")[-1]
            else:
                username = username_span[0].strip()

            pronunciation = {
                'username': username,
                'url': url
            }
            res_str = etree.tostring(i, encoding="unicode")
            if self.config.show_gender:
                m = re.search(r"\((Male|Female)", res_str)
                if m:
                    pronunciation['gender'] = m.group(1).strip()
            if self.config.show_country or self.config.preferred_countries:
                countryMatch = re.search(r"\((?:Male|Female) from ([^)]+)\)", res_str)
                if countryMatch:
                    pronunciation['country'] = countryMatch.group(1).strip()

            pronunciations.append(pronunciation)

        # Order the list based on preferred_usernames and preferred_countries
        # preferred usernames takes priority over preferred countries
        if self.config.preferred_usernames or self.config.preferred_countries:
            preferred_usernames = self.config.preferred_usernames
            preferred_countries = self.config.preferred_countries

            def get_index(pronunciation):
                pronunciation_username = pronunciation['username']
                pronunciation_country = pronunciation.get('country', 'unknown').lower()
                if pronunciation_username in preferred_usernames:
                    return preferred_usernames.index(pronunciation_username)

                if pronunciation_country in preferred_countries:
                    return preferred_countries.index(pronunciation_country) + len(preferred_usernames)

                # If the username isn't in the preferred lists, put it at the end
                return len(preferred_usernames) + len(preferred_countries)

            pronunciations = sorted(pronunciations, key=get_index)

        # Transform the list of pronunciations into Yomichan format
        audio_sources = []
        for pronunciation in pronunciations:
            genderSymbol = {
                "Male": '♂',
                "Female": '♀',
            }.get(pronunciation.get("gender"), "")

            name = f"Forvo ({genderSymbol}{pronunciation['username']})"
            if(country := pronunciation.get("country")):
                name = re.sub(r"\)$", f", {country})", name)

            audio_sources.append({
                "url": pronunciation['url'],
                "name": name
            })
        return audio_sources

    @classmethod
    def _extract_url(cls, onclick):
        # We are interested in Forvo's javascript Play function which takes in some parameters to play the audio
        # Example: Play(3060224,'OTQyN...','OTQyN..',false,'Yy9wL2NwXzk0MjYzOTZfNzZfMzM1NDkxNS5tcDM=','Yy9wL...','h')
        # Match anything that isn't commas, parentheses or quotes to capture the function arguments
        # Regex will match something like ["Play", "3060224", ...]
        play_args = re.findall(r"([^',\(\)]+)", onclick)

        # Forvo has two locations for mp3, /audios/mp3 and just /mp3
        # /audios/mp3 is normalized and has the filename in the 5th argument of Play base64 encoded
        # /mp3 is raw and has the filename in the 2nd argument of Play encoded
        try:
            file = base64.b64decode(play_args[5]).decode("utf-8")
            url = f"{cls._AUDIO_HTTP_HOST}/audios/mp3/{file}"
        # Some pronunciations don't have a normalized version so fallback to raw
        except:
            file = base64.b64decode(play_args[2]).decode("utf-8")
            url = f"{cls._AUDIO_HTTP_HOST}/mp3/{file}"
        return url

    def search(self, s):
        """
        Scrape Forvo's search page for audio sources. Note that the search page omits the username
        """
        s = s.strip()
        if len(s) == 0:
            return []
        path = f"/search/{s}/{self.config.language}/"
        html = self._get(path)

        # Forvo's search page returns two result sets like:
        # <ul class="word-play-list-icon-size-l">
        #   <li><span class="play" onclick"(some javascript to play the word audio)"></li>
        # </ul>
        results = html_fromstring(html).xpath(f'//ul[@class="word-play-list-icon-size-l"]/li/div[contains(@class, "play")]/@onclick')
        audio_sources = []
        for i in results:
            url = self._extract_url(i)
            audio_sources.append({"name":"Forvo Search","url":url})
        return audio_sources


class ForvoHandler(http.server.SimpleHTTPRequestHandler):
    forvo = Forvo(config=_forvo_config)

    def do_GET(self):
        # Extract 'term' and 'reading' query parameters
        query_components = parse_qs(urlparse(self.path).query)
        term = query_components["term"][0] if "term" in query_components else ""

        # Yomichan used to use "expression" but renamed to term. Still support "expression" for older versions
        expression = query_components["expression"][0] if "expression" in query_components else ""
        if term == "":
            term = expression

        reading = query_components["reading"][0] if "reading" in query_components else ""
        debug = query_components["debug"][0] if "debug" in query_components else False

        # Allow overriding the language
        self.forvo.config.language = query_components.get("language", [self.forvo.config.language])[0]

        if debug:
            debug_resp = {}
            debug_resp['debug'] = True
            debug_resp['reading'] = reading
            debug_resp['term'] = term
            debug_resp['word.term'] = self.forvo.word(term)
            debug_resp['word.reading'] = self.forvo.word(reading)
            debug_resp['search.term'] = self.forvo.search(term)
            debug_resp['search.reading'] = self.forvo.search(reading)
            self.wfile.write(bytes(json.dumps(debug_resp), "utf8"))
            return

        audio_sources = []

        # Try looking for word sources for 'term' first
        audio_sources = self.forvo.word(term)

        # Try looking for word sources for 'reading'
        if len(audio_sources) == 0:
            audio_sources += self.forvo.word(reading)

        # Finally use forvo search to look for similar words
        if len(audio_sources) == 0:
            audio_sources += self.forvo.search(term)

        if len(audio_sources) == 0:
            audio_sources += self.forvo.search(reading)

        # Build JSON that yomichan requires
        # Ref: https://github.com/FooSoft/yomichan/blob/master/ext/data/schemas/custom-audio-list-schema.json
        resp = {
            "type": "audioSourceList",
            "audioSources": audio_sources
        }

        # Writing the JSON contents with UTF-8
        payload = bytes(json.dumps(resp), "utf8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            self.log_error("BrokenPipe when sending reply")

        return

def run():
    httpd = ReusableTCPServer(('localhost', 8770), ForvoHandler, )
    try:
        print("Running in debug mode...")
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
        httpd.shutdown()
        httpd.server_close()
