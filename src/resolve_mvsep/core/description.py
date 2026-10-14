"""A small inert HTML-to-RTF renderer for catalog prose and real tables.

No HTML is executed; scripts, images, links, style rules and embedded objects are
never loaded. Unicode and RTF metacharacters are escaped as data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"br", "hr", "img", "meta", "link", "input", "col", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


IGNORED = {"script", "style", "iframe", "object", "svg", "img", "head"}
BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "section"}


def node_text(node) -> str:
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node)
    if node.tag in IGNORED:
        return ""
    if node.tag == "br":
        return "\n"
    value = "".join(node_text(child) for child in node.children)
    return value + ("\n" if node.tag in BLOCKS else "")


@dataclass
class Paragraph:
    text: str
    bold: bool = False


@dataclass
class Cell:
    text: str
    start: int
    end: int
    bold: bool = False
    merge: str = ""  # first/continue vertical span


@dataclass
class Table:
    rows: list[list[Cell]]

    @property
    def boundaries(self):
        # Drop artificial HTML columns which are spanned in every row.
        return sorted({0, *(cell.end for row in self.rows for cell in row),
                       *(cell.start for row in self.rows for cell in row)})


def descendants(node, tag):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag == tag:
                yield child
            elif child.tag != "table":
                yield from descendants(child, tag)


def table_from_node(node) -> Table:
    rows, carried = [], {}
    for row_index, row in enumerate(descendants(node, "tr")):
        cells = []
        for start, (end, until) in list(carried.items()):
            if row_index < until:
                cells.append(Cell("", start, end, merge="continue"))
            else:
                del carried[start]
        column = 0
        for entry in row.children:
            if not isinstance(entry, Node) or entry.tag not in {"td", "th"}:
                continue
            while any(cell.start <= column < cell.end for cell in cells):
                column = next(cell.end for cell in cells if cell.start <= column < cell.end)
            def span(key):
                try:
                    return max(1, min(32, int(entry.attrs.get(key, 1))))
                except (TypeError, ValueError):
                    return 1
            columns, lines = span("colspan"), span("rowspan")
            text = node_text(entry).strip()
            bold = entry.tag == "th" or any(True for _ in descendants(entry, "strong"))
            cells.append(Cell(text, column, column + columns, bold, "first" if lines > 1 else ""))
            if lines > 1:
                carried[column] = (column + columns, row_index + lines)
            column += columns
        if cells:
            rows.append(sorted(cells, key=lambda cell: cell.start))
    return Table(rows)


def html_blocks(value: str) -> list[Paragraph | Table]:
    parser = Parser()
    parser.feed(str(value or ""))
    result, pending = [], []

    def flush():
        text = "".join(pending).strip()
        if text:
            result.append(Paragraph(text))
        pending.clear()

    def walk(node):
        if isinstance(node, str):
            pending.append(node_text(node))
        elif node.tag in IGNORED:
            return
        elif node.tag == "table":
            flush()
            table = table_from_node(node)
            if table.rows:
                result.append(table)
        elif node.tag == "br":
            pending.append("\n")
        else:
            if node.tag in BLOCKS:
                flush()
            if node.tag == "li":
                pending.append("• ")
            for child in node.children:
                walk(child)
            if node.tag in BLOCKS:
                flush()
    walk(parser.root)
    flush()
    return result


def escape_rtf(text: str) -> str:
    output = []
    # RTF \u uses signed UTF-16 code units, including surrogate pairs.
    data = text.replace("\r\n", "\n").encode("utf-16-le", errors="replace")
    for i in range(0, len(data), 2):
        code = int.from_bytes(data[i:i + 2], "little")
        if code in (92, 123, 125):
            output.append("\\" + chr(code))
        elif code == 10:
            output.append(r"\line ")
        elif 32 <= code < 127:
            output.append(chr(code))
        elif code >= 128:
            output.append("\\u" + str(code if code < 32768 else code - 65536) + "?")
        else:
            output.append(" ")
    return "".join(output)


def document_rtf(blocks: list, width: int) -> bytes:
    """width in twips (1/1440 inch), re-rendered when the description column resizes."""
    width = max(2000, width)
    result = [r"{\rtf1\ansi\deff0\uc1{\fonttbl{\f0 Segoe UI;}}",
              r"{\colortbl ;\red37\green48\blue64;\red166\green187\blue207;\red214\green229\blue244;\red246\green250\blue254;}",
              r"\viewkind4\f0\fs24\cf1 "]
    for block in blocks:
        if isinstance(block, Paragraph):
            result.append(r"\pard\sa140\fs24 " + (r"\b " if block.bold else r"\b0 ") + escape_rtf(block.text) + r"\b0\par ")
        else:
            boundaries = block.boundaries
            if len(boundaries) < 2:
                continue
            # More room for model names; the metric columns share the rest.
            columns = len(boundaries) - 1
            first = .36 if columns > 2 else 1 / columns
            positions = {boundaries[0]: 0}
            for index, boundary in enumerate(boundaries[1:], 1):
                positions[boundary] = round(width * (first + (1 - first) * (index - 1) / max(1, columns - 1)))
            for row_index, row in enumerate(block.rows):
                result.append(r"\trowd\trgaph70\trleft0 ")
                for cell in row:
                    result.append(r"\clvertalc" + (r"\clvmgf" if cell.merge == "first" else r"\clvmrg" if cell.merge else ""))
                    result.append(r"\clcbpat" + ("3" if cell.bold else "4"))
                    result.append(r"\clbrdrt\brdrs\brdrw8\brdrcf2\clbrdrb\brdrs\brdrw8\brdrcf2"
                                  r"\clbrdrl\brdrs\brdrw8\brdrcf2\clbrdrr\brdrs\brdrw8\brdrcf2")
                    result.append("\\cellx" + str(positions[cell.end]) + " ")
                for cell in row:
                    result.append(r"\pard\intbl\sa60\sb60\fs22 " + (r"\b " if cell.bold else r"\b0 ")
                                  + escape_rtf(cell.text) + r"\cell ")
                result.append(r"\row ")
            result.append(r"\pard\b0\fs24\sa120\par ")
    result.append("}")
    return "".join(result).encode("ascii")
