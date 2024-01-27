# The MIT License (MIT)
#
# Copyright (c) 2014 - 2023 Isaac Muse
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# flake8: noqa

"""Collapsible code."""
import xml.etree.ElementTree as etree
from markdown import util as mutil
import re
from pymdownx.blocks.block import Block
from pymdownx.blocks import BlocksExtension

# Fenced block placeholder for SuperFences
FENCED_BLOCK_RE = re.compile(
    r'^([\> ]*){}({}){}$'.format(
        mutil.HTML_PLACEHOLDER[0],
        mutil.HTML_PLACEHOLDER[1:-1] % r'([0-9]+)',
        mutil.HTML_PLACEHOLDER[-1]
    )
)


class CollapseCode(Block):
    """Collapse code."""

    NAME = 'collapse-code'

    def on_init(self):
        """Handle initialization."""

        # Track tab group count across the entire page.
        if 'collapse_code_count' not in self.tracker:
            self.tracker['collapse_code_count'] = 0

        self.expand = self.config['expand_text']
        if not isinstance(self.expand, str):
            raise ValueError("'expand_text' must be a string")

        self.collapse = self.config['collapse_text']
        if not isinstance(self.collapse, str):
            raise ValueError("'collapse_text' must be a string")

        self.expand_title = self.config['expand_title']
        if not isinstance(self.expand_title, str):
            raise ValueError("'expand_title' must be a string")

        self.collapse_title = self.config['collapse_title']
        if not isinstance(self.collapse_title, str):
            raise ValueError("'collapse_title' must be a string")

    def on_create(self, parent):
        """Create the element."""

        self.count = self.tracker['collapse_code_count']
        self.tracker['collapse_code_count'] += 1
        el = etree.SubElement(parent, 'div', {'class': 'collapse-code'})
        etree.SubElement(
            el,
            'input',
            {
                "type": "checkbox",
                "id": "__collapse{}".format(self.count),
                "name": "__collapse{}".format(self.count),
                'checked': 'checked'
            }
        )
        return el

    def on_end(self, block):
        """Convert non list items to details."""

        el = etree.SubElement(block, 'div', {'class': 'code-footer'})
        attrs = {'for': '__collapse{}'.format(self.count), 'class': 'expand', 'tabindex': '0'}
        if self.expand_title:
            attrs['title'] = self.expand_title
        expand = etree.SubElement(el, 'label', attrs)
        expand.text = self.expand

        attrs = {'for': '__collapse{}'.format(self.count), 'class': 'collapse', 'tabindex': '0'}
        if self.collapse_title:
            attrs['title'] = self.collapse_title
        collapse = etree.SubElement(el, 'label', attrs)
        collapse.text = self.collapse


class CollapseCodeExtension(BlocksExtension):
    """Admonition Blocks Extension."""

    def __init__(self, *args, **kwargs):
        """Initialize."""

        self.config = {
            'expand_text': ['Expand', "Set the text for the expand button."],
            'collapse_text': ['Collapse', "Set the text for the collapse button."],
            'expand_title': ['expand', "Set the text for the expand title."],
            'collapse_title': ['collapse', "Set the text for the collapse title."]
        }

        super().__init__(*args, **kwargs)

    def extendMarkdownBlocks(self, md, blocks):
        """Extend Markdown blocks."""

        blocks.register(CollapseCode, self.getConfigs())


def makeExtension(*args, **kwargs):
    """Return extension."""

    return CollapseCodeExtension(*args, **kwargs)
