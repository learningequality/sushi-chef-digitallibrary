#!/usr/bin/env python
import logging
from collections import defaultdict
import re
from le_utils.constants.languages import getlang_by_alpha2, getlang_by_name, getlang_by_native_name
from le_utils.constants import content_kinds, licenses, file_types

from ricecooker.classes.licenses import get_license
from ricecooker.config import LOGGER
from ricecooker.chefs import JsonTreeChef
from ricecooker.utils.jsontrees import write_tree_to_json_tree

import requests

import feedparser
import pycountry



# LOGGING SETTINGS
################################################################################
logging.getLogger("cachecontrol.controller").setLevel(logging.WARNING)
logging.getLogger("requests.packages").setLevel(logging.WARNING)
LOGGER.setLevel(logging.DEBUG)


FEED_ROOT_URL = 'https://opds.staging.digitallibrary.io/root.xml'



_REL_SUBSECTION = 'subsection'
_REL_OPDS_POPULAR = u'http://opds-spec.org/sort/popular'
_REL_OPDS_NEW = u'http://opds-spec.org/sort/new'
_REL_ALTERNATE = 'alternate'
_REL_CRAWLABLE = 'http://opds-spec.org/crawlable'


_REL_OPDS_IMAGE = 'http://opds-spec.org/image'
_REL_OPDS_THUMBNAIL = 'http://opds-spec.org/image/thumbnail'
_REL_OPDS_ACQUISTION = u'http://opds-spec.org/acquisition'
_REL_OPDS_OPEN_ACCESS = 'http://opds-spec.org/acquisition/open-access'


_ALPHA_3_LANG_RE = re.compile(r"digitallibrary.io/(?P<lang3>.{3})/root\.xml")





# UTILS
################################################################################

def build_lang_lookup_table(FEED_ROOT_URL):
    """
    Extracts all the root URLs of the languages, based on the links
    with face `Languages` in FEED_ROOT_URL.
    """
    OPDS_LANG_ROOTS = {}

    feed = feedparser.parse(FEED_ROOT_URL)

    lang_links = []
    for link in feed.feed.links:
        if 'opds:facetgroup' in link:
            fg = link['opds:facetgroup']
            if fg == 'Languages':
                lang_links.append(link)

    # Build lookup table    lang_code --> dict with info about content in that langauge
    # where lang_code is the Learning Equality internal language codes defined in le_utils
    # Assume the chef scrill will be run on the command line using   lang=lang_code
    # E.g. lang_code for Zulu is `zul`, for Amharic it's `am`, and for Nepali it's `ne-NP`
    for link in lang_links:
        href = link['href']
        m = _ALPHA_3_LANG_RE.search(href)
        if not m:
            raise ValueError('Cannot find 3-letter language code in href' + str(href))
        lang3 = m.groupdict()['lang3']
        lang_title = link['title']
        #
        # ATTEMPT 1 ##############
        lang_obj = getlang_by_name(lang_title)
        if not lang_obj:
            lang_obj = getlang_by_native_name(lang_title)
            #
            # ATTEMPT 2 ##########
            if not lang_obj:
                pyc_lang = pycountry.languages.get(alpha_3=lang3)
                if hasattr(pyc_lang, 'alpha_2'):
                    #
                    # ATTEMPT 3 ##############
                    lang_obj = getlang_by_alpha2(pyc_lang.alpha_2)
                    if not lang_obj:
                        print('ERROR lang_obj is none', lang3, pyc_lang)
                else:
                    print('ERROR no alpha_2 code in pycountries for ', lang3, pyc_lang)
        assert lang_obj, 'ERROR no lang_obj found despite three attempts'
        lang_code = lang_obj.code
        OPDS_LANG_ROOTS[lang_code] = dict(
            alpha_3=lang3,
            lang_title=lang_title,
            href=href,
            name=lang_obj.name,
            native_name=lang_obj.native_name,
        )

    return OPDS_LANG_ROOTS


# CRAWLING
################################################################################

def parse_entire_feed(start_url):
    all_entries = []
    feed = feedparser.parse(start_url)
    feed_dict = parse_feed_metadata(feed)
    all_entries.extend(feed.entries)
    next_url = get_next_link(feed)
    while next_url is not None:
        feed = feedparser.parse(next_url)
        all_entries.extend(feed.entries)
        next_url = get_next_link(feed)
    return feed_dict, all_entries

def get_next_link(feed):
    next_link = None
    for link in feed.feed.links:
        if 'rel' in link and link['rel'] == 'next':
            next_link = link['href']
    return next_link

def parse_feed_metadata(feed):
    return feed.feed


def _get_reading_level(entry):
    readingLevel = None
    if 'lrmi_educationalalignment' in entry:
        lrmi_edal_dict = entry['lrmi_educationalalignment']
        if lrmi_edal_dict['alignmenttype'] == 'readingLevel':
            readingLevel = lrmi_edal_dict['targetname']
    if not readingLevel:
        readingLevel = 'Unknown'
    return readingLevel


# BUILD WEB RESOURCE TREE
################################################################################

def content_node_from_entry(entry, lang_code):
    pdf_link = None
    thumbnail_url = None
    for link in entry.links:
        if link['type'] == 'application/pdf':
            pdf_link = link
        elif link['rel'] == _REL_OPDS_IMAGE: # _REL_OPDS_THUMBNAIL:
            thumbnail_url = link['href']
        else:
            pass
            # print('Skipping link', link)

    GDL_LICENSE = get_license(licenses.CC_BY, copyright_holder=entry['dcterms_publisher']).as_dict()

    if pdf_link:
        pdf_url = pdf_link['href']
        child_node = dict(
            kind=content_kinds.DOCUMENT,
            source_id=entry['id'],
            language=lang_code,
            title=entry['title'],
            description=entry.get('summary', None),
            author=entry['author'],
            license=GDL_LICENSE,
            thumbnail=thumbnail_url,
            files=[],
        )
        pdf_file = dict(
            file_type=file_types.DOCUMENT,
            path=pdf_url,
            language=lang_code,
        )
        child_node['files'] = [pdf_file]
        LOGGER.debug('Created PDF Document Node from url ' + pdf_url)
        return child_node

    else:
        print('***** Skipping content, because no supported formats found', entry)
        return None


def build_ricecooker_json_tree(args, options, json_tree_path):
    print('json_tree_path=', json_tree_path)
    """
    Convert the OPDS feed into a Ricecooker JSON tree, with the following strucutre:
        Channel
            --> Language (TopicNode)
                    --> readingLevel (from lrmi_educationalalignment
                            --> Book.pdf  (DocumentNode)
    """
    LOGGER.info('Starting to build the ricecooker_json_tree')
    # if 'lang' not in options:
    #     raise ValueError('Must specify lang=?? on the command line. Supported languages are `en` and `fr`')
    # lang = options['lang']

    # Ricecooker tree for the channel
    ricecooker_json_tree = dict(
        source_domain = 'digitallibrary.io',
        source_id = 'digitallibrary-testing',  # feed_dict['id'],
        title = 'Global Digital Library - Book Catalog',  # ({})'.format(lang),
        thumbnail = './content/globaldigitallibrary_logo.png',
        description = 'The Global Digital Library (GDL) is being developed to '
                      'increase the availability of high quality reading resources '
                      'in languages children and youth speak and understand.',
        language = 'en', # lang,
        children=[],
    )


    OPDS_LANG_ROOTS = build_lang_lookup_table(FEED_ROOT_URL)
    for lang_code in sorted(OPDS_LANG_ROOTS.keys()):
        print("Processing lang_code", lang_code)
        lang_dict = OPDS_LANG_ROOTS[lang_code]
        start_url = lang_dict['href']
        feed_dict, all_entries = parse_entire_feed(start_url)

        lang_topic = dict(
            kind=content_kinds.TOPIC,
            source_id=start_url,
            title=lang_dict['lang_title'],
            author='',
            description='',
            language=lang_code,
            thumbnail=None,
            children=[],
        )
        ricecooker_json_tree['children'].append(lang_topic)

        # Group entries by their  lrmi_educationalalignment readingLevel value
        entries_by_readingLevel = defaultdict(list)
        for entry in all_entries:
            level = _get_reading_level(entry)
            entries_by_readingLevel[level].append(entry)

        # Make a subtopic from each level
        levels = sorted(entries_by_readingLevel.keys())
        for level in levels:
            entries = entries_by_readingLevel[level]
            print("Processing level", level)
            level_topic = dict(
                kind=content_kinds.TOPIC,
                source_id='digitallibrary.io'+':'+lang_code+':'+level,
                title='Level '+level,
                author='',
                description='',
                language=lang_code,
                thumbnail=None,
                children=[],
            )
            lang_topic['children'].append(level_topic)

            # Make a subtopic from each level
            for entry in entries:
                content_node = content_node_from_entry(entry, lang_code)
                if content_node:
                    level_topic['children'].append(content_node)
                else:
                    print('content_node None for entry', entry)

    # Write out ricecooker_json_tree.json
    write_tree_to_json_tree(json_tree_path, ricecooker_json_tree)





# CHEF
################################################################################

class GDLChef(JsonTreeChef):
    """
    Import
    """
    RICECOOKER_JSON_TREE = 'ricecooker_json_tree.json'

    def pre_run(self, args, options):
        """
        Run the preliminary step.
        """
        json_tree_path = self.get_json_tree_path()
        build_ricecooker_json_tree(args, options, json_tree_path)


if __name__ == '__main__':
    chef = GDLChef()
    chef.main()
