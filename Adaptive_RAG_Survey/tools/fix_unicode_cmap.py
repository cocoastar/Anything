"""Add a GB1 ToUnicode map to Tectonic-created XeLaTeX PDFs.

Some Tectonic/XeLaTeX builds of ctex render Fandol fonts correctly but omit
ToUnicode maps, so copying/searching Chinese in the PDF returns gibberish.
The CID -> Unicode data is Adobe's upstream cmap-resources table; we attach the
map to the *PDF*, without changing the TeX source or visible page layout.
Requires pypdf and network access to the official Adobe resource once.
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

ADOBE_TABLE = (
    'https://raw.githubusercontent.com/adobe-type-tools/cmap-resources/'
    'master/Adobe-GB1-6/cid2code.txt'
)


def cmap_bytes() -> bytes:
    with urllib.request.urlopen(ADOBE_TABLE, timeout=35) as response:
        table = response.read().decode('utf-8-sig')
    chars = []
    for row in table.splitlines():
        if not row or row.startswith('#') or row.startswith('CID\t'):
            continue
        cols = row.split('\t')
        if len(cols) < 11 or not cols[0].isdigit():
            continue
        cid = int(cols[0])
        ucs2 = cols[10].strip()
        if ucs2 == '*' or not ucs2:
            continue
        try:
            # Some CIDs list an ideographic radical followed by its normal CJK form;
            # take the latter for copy/search while keeping the rendered glyph unchanged.
            codepoint = int(ucs2.split(',')[-1], 16)
            if codepoint == 0 or 0xD800 <= codepoint <= 0xDFFF:
                continue
            utf16 = chr(codepoint).encode('utf-16-be').hex().upper()
        except (ValueError, UnicodeEncodeError):
            continue
        chars.append(f'<{cid:04X}> <{utf16}>\n')

    lines = [
        '/CIDInit /ProcSet findresource begin\n',
        '12 dict begin\nbegincmap\n',
        '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n',
        '/CMapName /Adobe-GB1-ToUnicode def\n/CMapType 2 def\n',
        '1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n',
    ]
    for start in range(0, len(chars), 100):
        batch = chars[start:start+100]
        lines.append(f'{len(batch)} beginbfchar\n')
        lines.extend(batch)
        lines.append('endbfchar\n')
    lines.append('endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n')
    return ''.join(lines).encode('ascii')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Use a different output file; retain the untouched PDF.')

    reader = PdfReader(args.input)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    tounicode = DecodedStreamObject()
    tounicode.set_data(cmap_bytes())
    unicode_ref = writer._add_object(tounicode)
    fixed = set()
    for page in writer.pages:
        fonts = page['/Resources']['/Font'].get_object()
        for fontref in fonts.values():
            font = fontref.get_object()
            if font.get('/ToUnicode') or font.get('/Subtype') != '/Type0':
                continue
            descendants = font.get('/DescendantFonts')
            if not descendants:
                continue
            info = descendants[0].get_object().get('/CIDSystemInfo')
            if not info or str(info.get('/Ordering')) != 'GB1':
                continue
            font[NameObject('/ToUnicode')] = unicode_ref
            fixed.add(str(font.get('/BaseFont')))
    if not fixed:
        raise ValueError('No GB1 Type0 fonts without ToUnicode found; output was not written')
    with args.output.open('wb') as out:
        writer.write(out)
    print(f'Added ToUnicode for {len(fixed)} fonts: {args.output}')


if __name__ == '__main__':
    main()
