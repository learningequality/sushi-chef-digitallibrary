import os

import requests
from bs4 import BeautifulSoup

from le_utils.constants import languages
from ricecooker.chefs import SushiChef
from ricecooker.classes.nodes import ChannelNode, H5PAppNode, TopicNode
from ricecooker.utils.caching import CacheForeverHeuristic, FileCache, CacheControlAdapter
from ricecooker.classes.files import H5PFile
from ricecooker.classes.licenses import CC_BYLicense, CC_BY_NCLicense, CC_BY_NC_SALicense

from requests.packages.urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

retry_strategy = Retry(
    total=5,
    backoff_factor=1
)
adapter = HTTPAdapter(max_retries=retry_strategy)

sess = requests.Session()
cache = FileCache('.webcache')
forever_adapter = CacheControlAdapter(heuristic=CacheForeverHeuristic(), cache=cache)

sess.mount('http://', forever_adapter)
sess.mount('https://', forever_adapter)
sess.mount('http://', adapter)
sess.mount('https://', adapter)

CHANNEL_NAME = "Global Digital Library - Book Catalog"  # Name of Kolibri channel
# This is the right source id from the public channel that is used - even though it says -testing.
CHANNEL_SOURCE_ID = "digitallibrary-testing"  # Unique ID for content source
CHANNEL_DOMAIN = "digitallibrary.io"  # Who is providing the content
CHANNEL_LANGUAGE = "mul"  # Language of channel
CHANNEL_DESCRIPTION = """The Global Digital Library (GDL) is being developed to '
                    'increase the availability of high quality reading resources '
                    'in languages children and youth speak and understand."""
CHANNEL_THUMBNAIL = 'gdl.png'
FOLDER_STORAGE = os.path.join("storage")
BOOKS_LINK = "https://digitallibrary.io/wp-json/content-api/v1/books/"

SESSION = requests.Session()
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36'
}
SESSION.headers = headers


def scrape_all_languages():
    dict_languages = {}
    response = SESSION.get('https://www.{}'.format(CHANNEL_DOMAIN))
    page = BeautifulSoup(response.text, 'html5lib')
    lst_items = page.find_all('li', {'class': 'fl-languages__list__item'})
    for item in lst_items:
        url = item.find('a')
        dict_languages[url.text] = url['href']
    return dict_languages


def get_all_books(language):
    try:
        book_url = "{}{}".format(BOOKS_LINK, language)
        response = requests.get(book_url)
        response_json = response.json()
        lst_dict_books = response_json.get('books')
        return lst_dict_books
    except Exception as ex:
        print(ex)


def create_book_structure(lst_dict_books):
    dict_channel_structure = {}
    for dict_book in lst_dict_books:
        lst_level_objects = dict_book.get('level')
        if lst_level_objects:
            level_object = lst_level_objects[0]
            book_obj = {
                'title': dict_book.get('title'),
                'download_url': dict_book.get('h5pUrl'),
                'description': dict_book.get('description'),
                'thumbnail': dict_book.get('thumbnail'),
                'code': dict_book.get('language')[0].get('slug'),
                'language': dict_book.get('language')[0].get('name'),
                'post_name': dict_book.get('post_name'),
                'publisher': dict_book.get('publisher')
            }

            if not dict_channel_structure.get(level_object.get('name')):
                dict_channel_structure[level_object.get('name')] = {dict_book.get('title'): book_obj}
            else:
                dict_level = dict_channel_structure.get(level_object.get('name'))
                dict_level.update({dict_book.get('title'): book_obj})
    return dict_channel_structure


def guess_license_id_from_string(lisence_long_name, holder):
    lookup_table = {
        'CC-BY-4.0': CC_BYLicense(holder),
        'CC-BY-NC-4.0': CC_BY_NCLicense(holder),
        'cc-by-nc-sa-4-0': CC_BY_NC_SALicense(holder),
    }
    if not holder:
        holder = "Digital Library"
    license = lookup_table.get(lisence_long_name, None)
    if license is None:
        license = CC_BYLicense(holder)  # default to licenses.CC_BY
    return license


class GlobalDigitalLibrary(SushiChef):
    channel_info = {
        # 'CHANNEL_ID': CHANNEL_ID,
        'CHANNEL_SOURCE_DOMAIN': CHANNEL_DOMAIN,
        'CHANNEL_SOURCE_ID': CHANNEL_SOURCE_ID,
        'CHANNEL_TITLE': CHANNEL_NAME,
        'CHANNEL_LANGUAGE': CHANNEL_LANGUAGE,
        'CHANNEL_DESCRIPTION': CHANNEL_DESCRIPTION,
        'CHANNEL_THUMBNAIL': CHANNEL_THUMBNAIL
    }
    translator = None

    def construct_channel(self, **kwargs):
        channel_info = self.channel_info
        LANGUAGE = kwargs.get("lang", "en")
        title = channel_info['CHANNEL_TITLE']
        description = channel_info.get('CHANNEL_DESCRIPTION')

        channel = ChannelNode(
            source_domain=channel_info['CHANNEL_SOURCE_DOMAIN'],
            source_id=channel_info['CHANNEL_SOURCE_ID'],
            title=title,
            thumbnail=channel_info.get('CHANNEL_THUMBNAIL'),
            description=description,
            language=LANGUAGE
        )
        dict_languages = scrape_all_languages()
        dict_structure_content = {}
        sorted_keys = sorted(dict_languages.keys())
        for key_name in sorted_keys:
            url_lang_path = dict_languages[key_name]
            language = url_lang_path.split('?')[0].split('/')[-1]
            lst_dict_books = get_all_books(language)
            if lst_dict_books:
                book_structure = create_book_structure(lst_dict_books)
                dict_structure_content[key_name] = book_structure
        self.upload_content(dict_structure_content, channel)
        return channel

    def upload_content(self, dict_content_structure, channel):
        for language in dict_content_structure:
            dict_content = dict_content_structure[language]
            language_topic = TopicNode(source_id='{}'.format(language), title=language)
            for key_level in dict_content:
                dict_level = dict_content.get(key_level)
                dict_book = list(dict_level.values())[0]
                language_code = dict_book.get('code')
                level_language = languages._parse_out_iso_639_code(language_code)
                level_topic = TopicNode(source_id='{}-{}'.format(key_level, level_language), title=key_level)
                for key_name in dict_level:
                    dict_book = dict_level[key_name]
                    license_book = guess_license_id_from_string(dict_book.get('license'), dict_book.get('publisher'))
                    language_code = dict_book.get('code')
                    language = languages._parse_out_iso_639_code(language_code)
                    if not language:
                        if 'rw-sign' in dict_book.get('code'):
                            language = 'rsn'
                        elif 'sgn-kh' in dict_book.get('code'):
                            language = 'csx'
                    title = dict_book.get('title').replace('&#039;', '\'')
                    replace_title = (str(title)).replace('&amp;','&')
                    book_node = H5PAppNode(
                        source_id=str(dict_book.get('post_name')),
                        title=replace_title,
                        license=license_book,
                        description=dict_book.get('description'),
                        thumbnail=dict_book.get('thumbnail'),
                        language=language,
                        files=[H5PFile(dict_book.get('download_url'))]
                    )
                    level_topic.add_child(book_node)
                level_topic.sort_children()
                language_topic.add_child(level_topic)
            language_topic.sort_children()
            channel.add_child(language_topic)
        return channel


if __name__ == '__main__':
    GlobalDigitalLibrary().main()
    # print(scrape_all_languages())
