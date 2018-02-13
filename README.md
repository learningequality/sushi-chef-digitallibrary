Sushi Chef script for the Global Digital Library - Book Catalog
===============================================================




Single-language channels
------------------------
When running the chef, pass `lang=<code>` where code is one of the following:

    'af', 'am', 'bn', 'en', 'hi', 'id', 'km', 'mr', 'nr', 'ne-NP', 'nso',
    'sot', 'ss', 'swa', 'tsn', 'ts', 've', 'xho', 'zul'

This will produce a cahnnel with a single langauge.





Corrupted downloads problem
---------------------------
Some of the PDFs and EPUB files get corrupted during download.

Consider the PDF file
https://books.staging.digitallibrary.io/pdf/ben/af7ad01d-7180-4cea-bda0-747c098e7818.pdf
linked to from the Referring page https://opds.staging.digitallibrary.io/ben/root.xml?page-size=100


This PDF downloads correctly with `curl` or when the browser is used (Firefox/Chrome tested)
but if downloading using  `wget` produces a different file:

    curl -v https://books.staging.digitallibrary.io/pdf/ben/af7ad01d-7180-4cea-bda0-747c098e7818.pdf  > curl_saved.pdf
    wget https://books.staging.digitallibrary.io/pdf/ben/af7ad01d-7180-4cea-bda0-747c098e7818.pdf -O wget_saved.pdf
    md5 *pdf

    MD5 (curl_saved.pdf) = 17a85a232cd132a48845cd37fd71ca8d
    MD5 (wget_saved.pdf) = 39de452fcb41a097910363ce8009d264

Not sure what's goign on; might want to followup with `digitallibrary.io` dev team if issue continues.
