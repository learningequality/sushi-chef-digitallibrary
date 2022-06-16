#!/usr/bin/env python
import logging
from collections import defaultdict
import os
import re
import subprocess

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
FEED_ROOT_URL = 'https://api.digitallibrary.io/book-api/opds/v1/root.xml'

_REL_SUBSECTION = 'subsection'
_REL_OPDS_POPULAR = u'http://opds-spec.org/sort/popular'
_REL_OPDS_NEW = u'http://opds-spec.org/sort/new'
_REL_ALTERNATE = 'alternate'
_REL_CRAWLABLE = 'http://opds-spec.org/crawlable'

_REL_OPDS_IMAGE = 'http://opds-spec.org/image'
_REL_OPDS_THUMBNAIL = 'http://opds-spec.org/image/thumbnail'
logging.getLogger("requests.packages").setLevel(logging.WARNING)
LOGGER.setLevel(logging.DEBUG)

_REL_OPDS_ACQUISTION = u'http://opds-spec.org/acquisition'
_REL_OPDS_OPEN_ACCESS = 'http://opds-spec.org/acquisition/open-access'

_LANG_CODE_RE = re.compile(r"digitallibrary.io/v1/(?P<gdl_lang_code>.+)/root\.xml")

BOOK_DATA_DIR = 'chefdata/pdfbooks'
BOOK_PUBLISHERS_TO_CROP = ['African Storybook Initiative']


# UTILS
################################################################################

def guess_license_id_from_string(lisence_long_name):
    lookup_table = {
        'Creative Commons Attribution 4.0 International': licenses.CC_BY,
        'Creative Commons Attribution Non Commercial 4.0 International': licenses.CC_BY_NC,
        'Creative Commons Attribution Non Commercial Share Alike 4.0 International': licenses.CC_BY_NC_SA,
        'Creative Commons Attribution Non Commercial 3.0 Unported': licenses.CC_BY_NC,
        'Creative Commons Attribution 3.0 Unported': licenses.CC_BY,
    }
    license_id = lookup_table.get(lisence_long_name, None)
    if license_id is None:
        LOGGER.warning('Encountered new license called {}'.format(lisence_long_name))
        license_id = licenses.CC_BY  # default to licenses.CC_BY
    return license_id


def crop_pdf_from_url(pdf_url):
    orig_filename = os.path.basename(pdf_url)
    orig_path = os.path.join(BOOK_DATA_DIR, orig_filename)
    cropped_filename = orig_filename.replace('.pdf', '-cropped.pdf')
    cropped_path = os.path.join(BOOK_DATA_DIR, cropped_filename)
    #
    # download original PDF if needed
    if not os.path.exists(orig_path):
        response = requests.get(pdf_url)
        with open(orig_path, 'wb') as pdf_file:
            pdf_file.write(response.content)
    #
    # convert pdf if needed
    if not os.path.exists(cropped_path):
        command = ["pdfcrop", "--margins", "7", orig_path, cropped_path]
        subprocess.check_output(command, stderr=subprocess.STDOUT)
    #
    return cropped_path


def build_lang_lookup_table(FEED_ROOT_URL):
    """
    Extracts all the root URLs of the languages, based on the links
    with face `Languages` in FEED_ROOT_URL.
    """
    OPDS_LANG_ROOTS = {}

    # Check for languages we don't yet support in Kolibri.
    langs_not_found = []

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
        m = _LANG_CODE_RE.search(href)
        if not m:
            raise ValueError('Cannot find language code in href: ' + str(href))
        gdl_lang_code = m.groupdict()['gdl_lang_code']
        lang_title = link['title']
        if lang_title == "isiNdebele seSewula":
            lang_title = "isiNdebele"
        elif lang_title == 'বাঙালি':
            lang_title = 'বাংলা'

        print('Processig lang_title', lang_title)
        #
        # ATTEMPT 1 ##############
        lang_obj = getlang_by_name(lang_title)
        if not lang_obj:
            lang_obj = getlang_by_native_name(lang_title)
            #
            # ATTEMPT 2 #########
            if not lang_obj:
                pyc_lang = pycountry.languages.lookup(gdl_lang_code)
                code = pyc_lang.alpha_3
                if hasattr(pyc_lang, 'alpha_2'):
                    #
                    # ATTEMPT 3 ##############
                    code = pyc_lang.alpha_2

                # getlang_by_alpha2 is a misnomer, codes can be alpha2, alpha3, or lang+locale.
                lang_obj = getlang_by_alpha2(code)
                if not lang_obj:
                    langs_not_found.append((pyc_lang, lang_title))
                    print('ERROR could not find Kolibri lang info for ', pyc_lang)
                    continue
        lang_code = lang_obj.code
        OPDS_LANG_ROOTS[lang_code] = dict(
            alpha_3=gdl_lang_code,
            lang_title=lang_title,
            href=href,
            name=lang_obj.name,
            native_name=lang_obj.native_name,
        )

    # For now, make missing languages a hard error so we can evaluate new language support case-by-case.
    if len(langs_not_found) > 0:
        lang_codes = []
        for pyc_lang, lang_title in langs_not_found:
            lang_codes.append(pyc_lang.alpha_3)
        message = "The following languages are not yet supported in Kolibri: {}".format(",".join(lang_codes))
        assert len(langs_not_found) == 0, message

    return OPDS_LANG_ROOTS


# CRAWLING
################################################################################

def parse_entire_feed(start_url):
    all_entries = []
    feed = feedparser.parse(start_url)
    if 'links' not in feed.feed:
        LOGGER.warning('Encountered empty feed at url={}'.format(start_url))
        return None, None
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

def join_with_commas_and_and(authors):
    if len(authors) == 0:
        return ''
    elif len(authors) == 1:
        return authors[0]
    elif len(authors) == 2:
        return authors[0] + ' and ' + authors[1]
    else:
        authors_except_last = authors[0:-1]
        last = authors[-1]
        authors_str = ', '.join(authors_except_last)
        authors_str += ', and '
        authors_str += last
        return authors_str


def _author_from_entry(entry):
    """
    Generates a concatenation for all author names
    """
    if 'authors' in entry:
        authors = [a['name'] for a in entry['authors']]
        if 'contributors' in entry:
            authors.extend([c['name'] for c in entry['contributors']])
        authors_str = join_with_commas_and_and(authors)
    elif 'author' in entry:
        authors_str = entry['author']
    else:
        referrer = entry['title_detail']['base']
        LOGGER.warning('Empty author for id={}, title={}, referrer={}'.format(entry['id'], entry['title'], referrer))
        authors_str = entry['dcterms_publisher'] + ' authors'
    return authors_str


def content_node_from_entry(entry, lang_code):
    """
    Convert a feed entry into ricecooker json dict.
    """
    # METADATA
    ############################################################################
    # author (using ,-separated list in case of multiple authors/contributors)
    authors_str = _author_from_entry(entry)

    # license info
    # currently one of {'African Storybook Initiative', 'USAID'}
    dcterms_publisher = entry['dcterms_publisher']
    license_id = guess_license_id_from_string(entry['dcterms_license'])
    LICENSE = get_license(license_id, copyright_holder=dcterms_publisher).as_dict()

    # currently one of {'African Storybook Initiative', 'USAID'}
    provider = dcterms_publisher

    # since we're importing the content from here
    aggregator = 'Global Digital Library'

    # CONTENT
    ############################################################################
    pdf_link = None
    epub_link = None
    thumbnail_url = None
    for link in entry.links:
        if link['type'] == 'application/pdf':
            pdf_link = link
        elif link['type'] == 'application/epub+zip':
            epub_link = link
        elif link['rel'] == _REL_OPDS_IMAGE:
            thumbnail_url = link['href']
        elif link['rel'] == _REL_OPDS_THUMBNAIL:
            pass  # skip thumnail URLs silently --- prefer _REL_OPDS_IMAGE because has right extension
        else:
            print('Skipping link', link)
            pass

    # prefer EPUBs...
    if epub_link:
        epub_url = epub_link['href']
        child_node = dict(
            kind=content_kinds.DOCUMENT,
            source_id=entry['id'],
            language=lang_code,
            title=entry['title'],
            description=entry.get('summary', None),
            author=authors_str,
            license=LICENSE,
            provider=provider,
            aggregator=aggregator,
            thumbnail=thumbnail_url,
            files=[],
        )
        epub_file = dict(
            file_type=file_types.EPUB,
            path=epub_url,
            language=lang_code,
        )
        child_node['files'] = [epub_file]
        LOGGER.debug('Created EPUB Document Node from url ' + epub_url)
        return child_node


    # ... but if no EPUB, then get PDF.
    elif epub_link is None and pdf_link:
        pdf_url = pdf_link['href']
        child_node = dict(
            kind=content_kinds.DOCUMENT,
            source_id=entry['id'],
            language=lang_code,
            title=entry['title'],
            description=entry.get('summary', None),
            author=authors_str,
            license=LICENSE,
            provider=provider,
            aggregator=aggregator,
            thumbnail=thumbnail_url,
            files=[],
        )

        if dcterms_publisher in BOOK_PUBLISHERS_TO_CROP:  # crop African Storybook PDFs
            pdf_path = crop_pdf_from_url(pdf_url)
        else:
            pdf_path = pdf_url  # upload unmodified PDF

        pdf_file = dict(
            file_type=file_types.DOCUMENT,
            path=pdf_path,
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
        source_domain='digitallibrary.io',
        source_id='digitallibrary-testing-2',  # feed_dict['id'],
        title='Global Digital Library - Book Catalog',  # ({})'.format(lang),
        thumbnail='./content/globaldigitallibrary_logo.png',
        description='The Global Digital Library (GDL) is being developed to '
                    'increase the availability of high quality reading resources '
                    'in languages children and youth speak and understand.',
        language='en',  # lang,
        children=[],
    )

    OPDS_LANG_ROOTS = build_lang_lookup_table(FEED_ROOT_URL)

    print("{} languages found".format(len(OPDS_LANG_ROOTS)))
    for lang_code in sorted(OPDS_LANG_ROOTS.keys()):
        print("Processing lang_code", lang_code)
        lang_dict = OPDS_LANG_ROOTS[lang_code]
        start_url = lang_dict['href']
        feed_dict, all_entries = parse_entire_feed(start_url)
        if feed_dict is None:
            continue  # Skip over empty or broken feeds
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
                source_id='digitallibrary.io' + ':' + lang_code + ':' + level,
                title=level,
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
    Import EPUBs and PDFs for all languages from the GDL Book Catalog.
    """
    RICECOOKER_JSON_TREE = 'ricecooker_json_tree.json'

    def pre_run(self, args, options):
        """
        Run the preliminary step.
        """
        if os.path.exists('cache.sqlite'):
            os.remove('cache.sqlite')
        json_tree_path = self.get_json_tree_path()
        build_ricecooker_json_tree(args, options, json_tree_path)


if __name__ == '__main__':
    chef = GDLChef()
    chef.main()
